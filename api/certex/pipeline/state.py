"""Pipeline state transitions, shared by every stage.

Stages communicate through the database, not through task return values. A stage
claims its document with a compare-and-set: ``UPDATE ... WHERE status IN (...)``.
Exactly one of several concurrent or redelivered tasks wins that update, so work
is never done twice, and a stage re-run after a crash resumes rather than repeats.

Batch progress is recomputed from document rows rather than incremented, so a
retried or redelivered task cannot double-count.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from certex.db.base import utcnow
from certex.db.models import Batch, CertificateUnit, Document
from certex.enums import BatchStatus, DocumentStatus
from certex.logging_setup import get_logger

__all__ = [
    "claim_document",
    "fail_document",
    "refresh_batch_progress",
]

logger = get_logger(__name__)

_MAX_ERROR_MESSAGE = 1000


def claim_document(
    session: Session,
    document_id: uuid.UUID,
    *,
    from_statuses: Iterable[DocumentStatus],
    to_status: DocumentStatus,
) -> bool:
    """Atomically move a document between statuses. True when this caller won."""
    allowed = list(from_statuses)
    result = session.execute(
        update(Document)
        .where(Document.id == document_id, Document.status.in_(allowed))
        .values(status=to_status, updated_at=utcnow())
        .execution_options(synchronize_session=False)
    )
    won = bool(getattr(result, "rowcount", 0) == 1)
    if won:
        logger.info(
            "pipeline.document_claimed",
            document_id=str(document_id),
            status=to_status.value,
        )
    return won


def fail_document(
    session: Session,
    document_id: uuid.UUID,
    *,
    code: str,
    message: str,
) -> None:
    """Mark a document FAILED with a reason. A failure is terminal for that file only.

    Only the caller-supplied message is stored, and callers pass the message of an
    :class:`~certex.core.errors.AppError` or a fixed sentence - never raw exception
    text, which can quote document content.
    """
    now = utcnow()
    session.execute(
        update(Document)
        .where(Document.id == document_id)
        .values(
            status=DocumentStatus.FAILED,
            error_code=code[:64],
            error_message=message[:_MAX_ERROR_MESSAGE],
            processing_completed_at=now,
            updated_at=now,
        )
        .execution_options(synchronize_session=False)
    )
    logger.warning("pipeline.document_failed", document_id=str(document_id), error_code=code)


def refresh_batch_progress(session: Session, batch_id: uuid.UUID) -> Batch | None:
    """Recompute a batch's counters and close it once every document is terminal."""
    batch = session.get(Batch, batch_id, with_for_update=True)
    if batch is None:
        return None

    rows = session.execute(
        select(Document.status, func.count(), func.coalesce(func.sum(Document.byte_size), 0))
        .where(Document.batch_id == batch_id)
        .group_by(Document.status)
    ).all()
    by_status = {status: (int(count), int(size)) for status, count, size in rows}

    batch.file_count = sum(count for count, _ in by_status.values())
    batch.total_bytes = sum(size for _, size in by_status.values())
    batch.processed_count = by_status.get(DocumentStatus.COMPLETED, (0, 0))[0]
    batch.failed_count = by_status.get(DocumentStatus.FAILED, (0, 0))[0]
    batch.duplicate_count = by_status.get(DocumentStatus.DUPLICATE, (0, 0))[0]
    batch.unit_count = int(
        session.scalar(
            select(func.count())
            .select_from(CertificateUnit)
            .where(CertificateUnit.batch_id == batch_id)
        )
        or 0
    )

    still_running = sum(count for status, (count, _) in by_status.items() if not status.is_terminal)
    if batch.status is BatchStatus.PROCESSING and still_running == 0:
        batch.status = (
            BatchStatus.COMPLETED_WITH_ERRORS if batch.failed_count else BatchStatus.COMPLETED
        )
        batch.completed_at = utcnow()
        logger.info(
            "pipeline.batch_completed",
            batch_id=str(batch_id),
            status=batch.status.value,
            processed_count=batch.processed_count,
            failed_count=batch.failed_count,
        )
    session.flush()
    return batch
