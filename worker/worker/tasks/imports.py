"""Loading a CSV of existing records into the register.

Its own task rather than a pipeline stage: an import has no document, no pages and no
units. What it shares with the stages is the shape - read its input from the database,
write its result there, and be safe to deliver twice.
"""

from __future__ import annotations

import uuid

from sqlalchemy.exc import InterfaceError, OperationalError

from certex.core.errors import StorageUnavailableError
from certex.pipeline.dispatch import TASK_IMPORT_CSV
from certex.pipeline.stages.import_stage import run_import_stage
from certex.queue import celery_app

__all__ = ["import_csv"]

TRANSIENT_ERRORS = (StorageUnavailableError, OperationalError, InterfaceError)


@celery_app.task(
    name=TASK_IMPORT_CSV,
    acks_late=True,
    autoretry_for=TRANSIENT_ERRORS,
    retry_backoff=True,
    retry_backoff_max=300,
    retry_jitter=True,
    max_retries=3,
    # A large file takes minutes, and the general task limit is set for a page of OCR.
    time_limit=7200,
    soft_time_limit=7080,
)
def import_csv(import_id: str) -> dict[str, object]:
    """Read one uploaded CSV. A retry re-reads the file from the beginning, which is
    safe: a row already in the register is detected as a duplicate of itself and
    skipped, so nothing is written twice."""
    result = run_import_stage(uuid.UUID(import_id))
    return {
        "ran": result.ran,
        "total": result.counts.total,
        "created": result.counts.created,
        "skipped": result.counts.skipped,
        "failed": result.counts.failed,
        "failure": result.failure,
    }
