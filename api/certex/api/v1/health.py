"""Liveness and readiness probes.

``/live`` answers as long as the process can serve a request - it is what an
orchestrator uses to decide whether to restart the container.

``/ready`` proves the dependencies a request actually needs are reachable, and is
what a load balancer uses to decide whether to send traffic. Checks run
concurrently with individual timeouts so one wedged dependency cannot make the
probe itself hang.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import enum
import time
from collections.abc import Awaitable, Callable

import redis.asyncio as aioredis
from fastapi import APIRouter, Response, status
from pydantic import BaseModel, ConfigDict, Field
from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError

from certex import __version__
from certex.config import Settings, get_settings
from certex.db.session import ping_database
from certex.logging_setup import get_logger, safe_error

__all__ = ["router"]

logger = get_logger(__name__)
router = APIRouter(tags=["health"])

_CHECK_TIMEOUT_SECONDS = 3.0


class ComponentState(str, enum.Enum):
    UP = "up"
    DOWN = "down"
    DEGRADED = "degraded"
    """Reachable but not fully usable, or an optional component that is absent."""


class ComponentHealth(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    state: ComponentState
    latency_ms: float | None = None
    detail: str | None = Field(
        default=None, description="Error class and scrubbed message when not up."
    )
    required: bool = True


class HealthResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: ComponentState
    version: str
    environment: str
    checked_at: dt.datetime
    components: list[ComponentHealth]


async def _timed(
    name: str,
    probe: Callable[[], Awaitable[None]],
    *,
    required: bool = True,
) -> ComponentHealth:
    started = time.perf_counter()
    try:
        await asyncio.wait_for(probe(), timeout=_CHECK_TIMEOUT_SECONDS)
    except TimeoutError:
        return ComponentHealth(
            name=name,
            state=ComponentState.DOWN if required else ComponentState.DEGRADED,
            latency_ms=round((time.perf_counter() - started) * 1000, 2),
            detail=f"probe exceeded {_CHECK_TIMEOUT_SECONDS}s",
            required=required,
        )
    except Exception as exc:  # noqa: BLE001 - a probe must classify, never propagate
        return ComponentHealth(
            name=name,
            state=ComponentState.DOWN if required else ComponentState.DEGRADED,
            latency_ms=round((time.perf_counter() - started) * 1000, 2),
            detail=safe_error(exc),
            required=required,
        )
    return ComponentHealth(
        name=name,
        state=ComponentState.UP,
        latency_ms=round((time.perf_counter() - started) * 1000, 2),
        required=required,
    )


async def _probe_database() -> None:
    try:
        await ping_database()
    except SQLAlchemyError as exc:
        raise RuntimeError(safe_error(exc)) from exc


async def _probe_redis(settings: Settings) -> None:
    client = aioredis.from_url(  # type: ignore[no-untyped-call]
        settings.redis_url,
        socket_connect_timeout=2,
        socket_timeout=2,
    )
    try:
        await client.ping()
    except RedisError as exc:
        raise RuntimeError(safe_error(exc)) from exc
    finally:
        await client.aclose()


async def _collect(settings: Settings) -> list[ComponentHealth]:
    checks = [
        _timed("database", _probe_database),
        _timed("redis", lambda: _probe_redis(settings)),
    ]
    return list(await asyncio.gather(*checks))


def _overall(components: list[ComponentHealth]) -> ComponentState:
    if any(c.state is ComponentState.DOWN and c.required for c in components):
        return ComponentState.DOWN
    if any(c.state is not ComponentState.UP for c in components):
        return ComponentState.DEGRADED
    return ComponentState.UP


@router.get(
    "/live",
    summary="Liveness probe",
    response_model=HealthResponse,
    status_code=status.HTTP_200_OK,
)
async def live() -> HealthResponse:
    """Answers whenever the process is able to serve. Touches no dependency."""
    settings = get_settings()
    return HealthResponse(
        status=ComponentState.UP,
        version=__version__,
        environment=settings.app_env.value,
        checked_at=dt.datetime.now(dt.UTC),
        components=[],
    )


@router.get(
    "/ready",
    summary="Readiness probe",
    response_model=HealthResponse,
    responses={503: {"description": "One or more required dependencies are unreachable."}},
)
async def ready(response: Response) -> HealthResponse:
    settings = get_settings()
    components = await _collect(settings)
    overall = _overall(components)

    if overall is ComponentState.DOWN:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        logger.warning(
            "health.not_ready",
            field_names=[c.name for c in components if c.state is ComponentState.DOWN],
        )

    return HealthResponse(
        status=overall,
        version=__version__,
        environment=settings.app_env.value,
        checked_at=dt.datetime.now(dt.UTC),
        components=components,
    )
