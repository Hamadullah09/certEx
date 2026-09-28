"""Stage 8 tasks: check and route a row, then close the document it belongs to."""

from __future__ import annotations

import uuid

from sqlalchemy.exc import InterfaceError, OperationalError

from certex.core.errors import StorageUnavailableError
from certex.pipeline.dispatch import TASK_FINALIZE_DOCUMENT, TASK_VALIDATE_UNIT
from certex.pipeline.stages.finalize_stage import run_finalize_document_stage
from certex.pipeline.stages.validate_stage import run_validate_unit_stage
from certex.queue import celery_app

__all__ = ["finalize_document", "validate_unit"]

TRANSIENT_ERRORS = (StorageUnavailableError, OperationalError, InterfaceError)


@celery_app.task(
    name=TASK_VALIDATE_UNIT,
    acks_late=True,
    autoretry_for=TRANSIENT_ERRORS,
    retry_backoff=True,
    retry_backoff_max=300,
    retry_jitter=True,
    max_retries=3,
)
def validate_unit(unit_id: str) -> dict[str, object]:
    result = run_validate_unit_stage(uuid.UUID(unit_id))
    return {
        "ran": result.ran,
        "confidence": result.confidence,
        "review_status": result.review_status.value if result.review_status else None,
        "flag_count": len(result.flags or []),
        "document_finished": result.document_finished,
    }


@celery_app.task(
    name=TASK_FINALIZE_DOCUMENT,
    acks_late=True,
    autoretry_for=TRANSIENT_ERRORS,
    retry_backoff=True,
    retry_backoff_max=300,
    retry_jitter=True,
    max_retries=3,
)
def finalize_document(document_id: str) -> dict[str, object]:
    result = run_finalize_document_stage(uuid.UUID(document_id))
    return {
        "ran": result.ran,
        "unit_count": result.unit_count,
        "duplicates_filled": result.duplicates_filled,
    }
