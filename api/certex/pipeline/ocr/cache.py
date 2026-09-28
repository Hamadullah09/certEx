"""Remembering what OCR already read.

Reading one page costs seconds of CPU, and the same page image is read again more often
than it looks: an operator re-processes a batch after fixing a template, the same
certificate is uploaded twice by two clerks, a page is re-extracted after a correction.
The result is therefore stored under a digest of the page image *and* the recipe that
read it, so a change to the preprocessing, the DPI or the configured languages produces
a different key and nothing stale is ever served.

A cache must never be the reason a job fails: every read and write here swallows
storage errors and logs them.
"""

from __future__ import annotations

import hashlib
import uuid
from typing import Final

from pydantic import BaseModel, ConfigDict, Field

from certex.core.errors import AppError
from certex.logging_setup import get_logger, safe_error
from certex.pipeline.ocr.preprocess import PREPROCESS_VERSION
from certex.schemas.layout import PageLayout
from certex.storage.s3 import ObjectStorage, StorageKeys

__all__ = ["CACHE_VERSION", "CachedOcr", "OcrCache", "page_digest"]

logger = get_logger(__name__)

CACHE_VERSION: Final = 1
"""Bumped when this payload's meaning changes, independently of the recipe."""


class CachedOcr(BaseModel):
    """One page's OCR result, as stored."""

    model_config = ConfigDict(frozen=True)

    version: int = CACHE_VERSION
    layout: PageLayout
    mean_confidence: float | None = Field(default=None, ge=0.0, le=100.0)
    language: str
    psm: int
    dpi: int
    rotation: float
    variant: str


def page_digest(image_bytes: bytes, *, recipe: str) -> str:
    """SHA-256 of a page image together with the recipe used to read it."""
    digest = hashlib.sha256()
    digest.update(image_bytes)
    digest.update(b"|")
    digest.update(f"preprocess={PREPROCESS_VERSION}|{recipe}".encode())
    return digest.hexdigest()


class OcrCache:
    """Object-storage backed cache of page OCR results, scoped per workspace."""

    __slots__ = ("_storage",)

    def __init__(self, storage: ObjectStorage) -> None:
        self._storage = storage

    def get(self, workspace_id: uuid.UUID, digest: str) -> CachedOcr | None:
        try:
            payload = self._storage.get_bytes_if_exists(StorageKeys.ocr_cache(workspace_id, digest))
        except (AppError, ValueError) as exc:
            logger.info("ocr.cache_read_failed", error_type=safe_error(exc))
            return None
        if payload is None:
            return None
        try:
            cached = CachedOcr.model_validate_json(payload)
        except ValueError as exc:
            # A payload written by an older, incompatible build. Treat it as a miss.
            logger.info("ocr.cache_unreadable", error_type=safe_error(exc))
            return None
        if cached.version != CACHE_VERSION:
            return None
        return cached

    def put(self, workspace_id: uuid.UUID, digest: str, result: CachedOcr) -> None:
        try:
            self._storage.upload_bytes(
                StorageKeys.ocr_cache(workspace_id, digest),
                result.model_dump_json().encode("utf-8"),
                content_type="application/json",
            )
        except (AppError, ValueError) as exc:
            logger.info("ocr.cache_write_failed", error_type=safe_error(exc))
