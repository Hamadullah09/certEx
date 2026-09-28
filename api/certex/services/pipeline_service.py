"""Starting a batch's pipeline.

Starting is explicit: uploads can arrive over minutes or hours, and processing a
half-uploaded batch would split its results across two runs. Once started, the
batch is closed to uploads and every readable document is handed to the text stage.
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from certex.core.deps import WorkspaceScope
from certex.core.errors import BatchNotReadyError, ConflictError, NotFoundError
from certex.db.base import utcnow
from certex.db.models import Batch, Document, UploadSession
from certex.enums import BatchStatus, DocumentStatus, UserRole
from certex.logging_setup import get_logger

__all__ = ["start_batch"]

logger = get_logger(__name__)


async def start_batch(
    session: AsyncSession,
    *,
    scope: WorkspaceScope,
    batch_id: uuid.UUID,
) -> tuple[Batch, list[uuid.UUID]]:
    """Move a batch into PROCESSING and return the documents to enqueue.

    Documents that failed at upload, duplicates and archive containers need no
    processing of their own. When nothing needs processing the batch completes at
    once, so a batch of nothing but duplicates does not sit "processing" forever.
    """
    scope.require(UserRole.OPERATOR)
    batch = await session.scalar(
        select(Batch)
        .where(Batch.id == batch_id, Batch.workspace_id == scope.workspace_id)
        .with_for_update()
    )
    if batch is None:
        raise NotFoundError(
            "No batch with that id exists in this workspace.",
            remediation="Refresh the batch list to see what is available.",
        )

    if batch.status not in (BatchStatus.CREATED, BatchStatus.UPLOADING):
        raise ConflictError(
            f"This batch is already {batch.status.value.lower()}.",
            title="Batch already started",
            remediation="Open the batch to follow its progress.",
        )

    unfinished_uploads = await session.scalar(
        select(func.count())
        .select_from(UploadSession)
        .where(UploadSession.batch_id == batch_id, UploadSession.is_complete.is_(False))
    )
    if unfinished_uploads:
        raise BatchNotReadyError(
            f"{unfinished_uploads} upload(s) in this batch have not finished.",
            remediation="Let the uploads finish, or cancel them, then start the batch.",
        )

    total = await session.scalar(
        select(func.count()).select_from(Document).where(Document.batch_id == batch_id)
    )
    if not total:
        raise BatchNotReadyError("This batch has no files yet.")

    document_ids = list(
        (
            await session.scalars(
                select(Document.id)
                .where(Document.batch_id == batch_id, Document.status == DocumentStatus.QUEUED)
                .order_by(Document.created_at, Document.id)
            )
        ).all()
    )

    now = utcnow()
    batch.status = BatchStatus.PROCESSING
    batch.started_at = now
    if not document_ids:
        batch.status = (
            BatchStatus.COMPLETED_WITH_ERRORS if batch.failed_count else BatchStatus.COMPLETED
        )
        batch.completed_at = now
    await session.flush()

    logger.info(
        "pipeline.batch_started",
        batch_id=str(batch_id),
        count=len(document_ids),
        status=batch.status.value,
    )
    return batch, document_ids
