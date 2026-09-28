"""Stage 6 task: read one certificate's fields."""

from __future__ import annotations

import uuid

from sqlalchemy.exc import InterfaceError, OperationalError

from certex.core.errors import StorageUnavailableError
from certex.pipeline.dispatch import TASK_EXTRACT_UNIT
from certex.pipeline.stages.extract_stage import run_extract_unit_stage
from certex.queue import celery_app

__all__ = ["extract_unit"]

TRANSIENT_ERRORS = (StorageUnavailableError, OperationalError, InterfaceError)


@celery_app.task(
    name=TASK_EXTRACT_UNIT,
    acks_late=True,
    autoretry_for=TRANSIENT_ERRORS,
    retry_backoff=True,
    retry_backoff_max=300,
    retry_jitter=True,
    max_retries=3,
)
def extract_unit(unit_id: str) -> dict[str, object]:
    result = run_extract_unit_stage(uuid.UUID(unit_id))
    return {
        "ran": result.ran,
        "field_count": result.field_count,
        "extra_field_count": result.extra_field_count,
        "template_used": result.template_used,
    }
