"""Remembering what the model already answered.

The same certificate is asked about more often than it looks: a batch reprocessed after
a template fix, a document two clerks both uploaded, a unit re-read after its page range
was corrected. Each of those is a paid request for an answer the system already has.

Kept in Redis rather than in object storage, unlike the OCR cache, for two reasons: the
payload is a few hundred bytes where a page layout is tens of kilobytes, and this one
should expire. An answer from a model version that has since been replaced is not worth
serving a year later.

A cache is never allowed to be the reason a document fails, so every read and write here
swallows its errors and logs the type.
"""

from __future__ import annotations

import json
from typing import Final

from redis import Redis
from redis.exceptions import RedisError

from certex.config import Settings, get_settings
from certex.llm.client import LLMResponse
from certex.logging_setup import get_logger, safe_error

__all__ = ["CACHE_VERSION", "LLMCache"]

logger = get_logger(__name__)

CACHE_VERSION: Final = 1
"""Bumped when the stored shape changes, so older entries are ignored rather than
misread."""

_PREFIX: Final = "llm:extract"


class LLMCache:
    """Answers from the fallback, keyed by a digest of the whole question.

    Scoped per workspace. Two offices can hold the same certificate, and one office's
    answers are not the other's to read - the key is cheap to make specific and a shared
    cache would be a quiet way across the tenancy boundary.
    """

    __slots__ = ("_client", "_settings")

    def __init__(self, client: Redis, *, settings: Settings | None = None) -> None:
        self._client = client
        self._settings = settings or get_settings()

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> LLMCache:
        active = settings or get_settings()
        return cls(
            Redis.from_url(
                active.redis_url,
                encoding="utf-8",
                decode_responses=True,
                socket_connect_timeout=2,
                socket_timeout=2,
            ),
            settings=active,
        )

    def _key(self, workspace_id: str, digest: str) -> str:
        return f"{_PREFIX}:{CACHE_VERSION}:{workspace_id}:{digest}"

    def get(self, workspace_id: str, digest: str) -> LLMResponse | None:
        if not self._settings.llm_cache_enabled:
            return None
        try:
            raw = self._client.get(self._key(workspace_id, digest))
        except RedisError as exc:
            logger.info("llm.cache_read_failed", error_type=safe_error(exc))
            return None
        if not isinstance(raw, str):
            return None
        try:
            stored = json.loads(raw)
            values = {
                str(name): str(value)
                for name, value in (stored.get("values") or {}).items()
                if isinstance(value, str)
            }
        except (ValueError, AttributeError) as exc:
            logger.info("llm.cache_unreadable", error_type=safe_error(exc))
            return None
        return LLMResponse(
            values=values,
            input_tokens=int(stored.get("input_tokens") or 0),
            output_tokens=int(stored.get("output_tokens") or 0),
            model=str(stored.get("model") or ""),
            from_cache=True,
        )

    def put(self, workspace_id: str, digest: str, response: LLMResponse) -> None:
        if not self._settings.llm_cache_enabled:
            return
        payload = json.dumps(
            {
                "values": response.values,
                "input_tokens": response.input_tokens,
                "output_tokens": response.output_tokens,
                "model": response.model,
            },
            ensure_ascii=False,
        )
        try:
            self._client.setex(
                self._key(workspace_id, digest),
                self._settings.llm_cache_ttl_seconds,
                payload,
            )
        except RedisError as exc:
            logger.info("llm.cache_write_failed", error_type=safe_error(exc))

    def close(self) -> None:
        try:
            self._client.close()  # type: ignore[no-untyped-call]
        except RedisError as exc:  # pragma: no cover - closing rarely fails
            logger.info("llm.cache_close_failed", error_type=safe_error(exc))
