"""Stage 5 tasks: split a document into certificates, and classify each one."""

from __future__ import annotations

import uuid

from sqlalchemy.exc import InterfaceError, OperationalError

from certex.core.errors import StorageUnavailableError
from certex.pipeline.dispatch import TASK_CLASSIFY_UNIT, TASK_SPLIT_DOCUMENT
from certex.pipeline.stages.classify_stage import run_classify_unit_stage
from certex.pipeline.stages.split_stage import run_split_stage
from certex.queue import celery_app

__all__ = ["classify_unit", "split_document"]

TRANSIENT_ERRORS = (StorageUnavailableError, OperationalError, InterfaceError)


@celery_app.task(
    name=TASK_SPLIT_DOCUMENT,
    acks_late=True,
    autoretry_for=TRANSIENT_ERRORS,
    retry_backoff=True,
    retry_backoff_max=300,
    retry_jitter=True,
    max_retries=3,
)
def split_document(document_id: str) -> dict[str, object]:
    result = run_split_stage(uuid.UUID(document_id))
    return {
        "ran": result.ran,
        "unit_count": result.unit_count,
        "method": result.method.value if result.method else None,
    }


@celery_app.task(
    name=TASK_CLASSIFY_UNIT,
    acks_late=True,
    autoretry_for=TRANSIENT_ERRORS,
    retry_backoff=True,
    retry_backoff_max=300,
    retry_jitter=True,
    max_retries=3,
)
def classify_unit(unit_id: str) -> dict[str, object]:
    result = run_classify_unit_stage(uuid.UUID(unit_id))
    return {
        "ran": result.ran,
        "certificate_type": (result.certificate_type.value if result.certificate_type else None),
        "confidence": result.confidence,
        "advanced": result.advanced,
    }
