"""Stage 3 task: read each page's text and route pages that need OCR."""

from __future__ import annotations

import uuid

from sqlalchemy.exc import InterfaceError, OperationalError

from certex.core.errors import StorageUnavailableError
from certex.pipeline.dispatch import TASK_TEXT_DOCUMENT
from certex.pipeline.stages.text_stage import run_text_stage
from certex.queue import celery_app

__all__ = ["extract_document_text"]

# Infrastructure failures are retried with exponential backoff; a problem with the
# document itself is recorded on the document by the stage and never retried.
TRANSIENT_ERRORS = (StorageUnavailableError, OperationalError, InterfaceError)


@celery_app.task(
    name=TASK_TEXT_DOCUMENT,
    acks_late=True,
    autoretry_for=TRANSIENT_ERRORS,
    retry_backoff=True,
    retry_backoff_max=300,
    retry_jitter=True,
    max_retries=3,
)
def extract_document_text(document_id: str) -> dict[str, int]:
    result = run_text_stage(uuid.UUID(document_id))
    return {"page_count": result.page_count, "ocr_page_count": len(result.ocr_pages)}
