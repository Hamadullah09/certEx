"""Live progress for a batch that is being processed.

An operator who has just uploaded two hundred certificates wants to watch them go
through, and polling for that is both wasteful and laggy. This is a server-sent event
stream: one event per change, and a heartbeat while nothing is happening so that an idle
connection is not mistaken for a dead one by whatever sits between the browser and here.

The stream reads the batch's own counters rather than subscribing to the pipeline, which
means it is correct no matter which worker did what, and it survives a worker restart.
It ends by itself when the batch finishes, so the browser does not have to decide when to
stop listening.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from typing import Any, Final

from fastapi import APIRouter, Request
from sse_starlette.sse import EventSourceResponse

from certex.core.deps import SessionDep, WorkspaceScopeDep
from certex.db.models import Batch
from certex.db.session import async_session_scope
from certex.enums import BatchStatus
from certex.logging_setup import get_logger
from certex.services import batch_service

__all__ = ["router"]

logger = get_logger(__name__)
router = APIRouter(prefix="/batches", tags=["batches"])

_POLL_SECONDS: Final = 1.0
"""How often the batch's counters are re-read while a client is watching."""

_HEARTBEAT_SECONDS: Final = 15.0
"""A silent stream looks broken to a proxy; this keeps it visibly alive."""

_MAX_STREAM_SECONDS: Final = 3600.0
"""A batch that has not finished in an hour is a problem for a person, not a stream."""

_TERMINAL: Final = (
    BatchStatus.COMPLETED,
    BatchStatus.COMPLETED_WITH_ERRORS,
    BatchStatus.FAILED,
    BatchStatus.CANCELLED,
)


def _snapshot(batch: Batch) -> dict[str, Any]:
    total = batch.file_count or 0
    done = (batch.processed_count or 0) + (batch.failed_count or 0) + (batch.duplicate_count or 0)
    return {
        "batch_id": str(batch.id),
        "status": batch.status.value,
        "file_count": total,
        "processed_count": batch.processed_count,
        "failed_count": batch.failed_count,
        "duplicate_count": batch.duplicate_count,
        "unit_count": batch.unit_count,
        # What the progress bar shows, computed here so every client agrees on it.
        "percent": round(100.0 * done / total, 1) if total else 0.0,
        "finished": batch.status in _TERMINAL,
    }


@router.get(
    "/{batch_id}/progress",
    summary="Live progress of a batch, as server-sent events",
    response_class=EventSourceResponse,
)
async def stream_progress(
    batch_id: uuid.UUID,
    request: Request,
    session: SessionDep,
    scope: WorkspaceScopeDep,
) -> EventSourceResponse:
    """Stream this batch's progress until it finishes or the client goes away."""
    # Proves the batch exists in this workspace before a long-lived stream is opened.
    batch = await batch_service.get_batch(session, scope=scope, batch_id=batch_id)
    first = _snapshot(batch)

    async def events() -> AsyncIterator[dict[str, str]]:
        yield {"event": "progress", "data": json.dumps(first)}
        if first["finished"]:
            return

        previous = first
        silent_for = 0.0
        waited = 0.0
        while waited < _MAX_STREAM_SECONDS:
            if await request.is_disconnected():
                return
            await asyncio.sleep(_POLL_SECONDS)
            waited += _POLL_SECONDS
            silent_for += _POLL_SECONDS

            # A short-lived session per poll: a stream must not hold a connection from
            # the request pool for an hour.
            async with async_session_scope() as watcher:
                current = await watcher.get(Batch, batch_id)
                snapshot = _snapshot(current) if current is not None else None

            if snapshot is None:
                yield {"event": "gone", "data": json.dumps({"batch_id": str(batch_id)})}
                return
            if snapshot != previous:
                yield {"event": "progress", "data": json.dumps(snapshot)}
                previous = snapshot
                silent_for = 0.0
            elif silent_for >= _HEARTBEAT_SECONDS:
                yield {"event": "heartbeat", "data": "{}"}
                silent_for = 0.0

            if snapshot["finished"]:
                return

        logger.info("progress.stream_timed_out", batch_id=str(batch_id))

    return EventSourceResponse(events())
