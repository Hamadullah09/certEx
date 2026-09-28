"""Stage 4 task: read one scanned page.

One task per page. The page number is part of the task's arguments rather than
something the task discovers, so a redelivered message reads exactly the page it was
sent for and the stage's own guard makes the second read a no-op.
"""

from __future__ import annotations

import uuid

from sqlalchemy.exc import InterfaceError, OperationalError

from certex.core.errors import StorageUnavailableError
from certex.pipeline.dispatch import TASK_OCR_PAGE
from certex.pipeline.stages.ocr_stage import run_ocr_page_stage
from certex.queue import celery_app

__all__ = ["ocr_page_task"]

# Infrastructure failures are retried with exponential backoff. A page Tesseract
# cannot read, or a document that is not readable at all, is recorded on the document
# by the stage and never retried - retrying would fail the same way.
TRANSIENT_ERRORS = (StorageUnavailableError, OperationalError, InterfaceError)


@celery_app.task(
    name=TASK_OCR_PAGE,
    acks_late=True,
    autoretry_for=TRANSIENT_ERRORS,
    retry_backoff=True,
    retry_backoff_max=300,
    retry_jitter=True,
    max_retries=3,
)
def ocr_page_task(document_id: str, page_number: int) -> dict[str, object]:
    result = run_ocr_page_stage(uuid.UUID(document_id), int(page_number))
    return {
        "ran": result.ran,
        "word_count": result.word_count,
        "mean_confidence": result.mean_confidence,
        "from_cache": result.from_cache,
        "advanced": result.advanced,
    }
