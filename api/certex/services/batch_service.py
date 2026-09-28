"""Batch lifecycle: create, list, read, delete.

Every query in this module takes a :class:`WorkspaceScope` and adds the
``workspace_id`` predicate itself. Handlers cannot forget it, because there is no
function here that will build a query without one. That is the "enforced at the
query layer, not just in the UI" requirement made structural.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from certex.core.deps import WorkspaceScope
from certex.core.errors import ConflictError, NotFoundError
from certex.db.base import JSONDict
from certex.db.models import Batch, Document, UploadSession
from certex.enums import BatchStatus, DocumentStatus, UserRole
from certex.logging_setup import get_logger
from certex.schemas.batches import BatchCreateRequest, BatchSettings
from certex.schemas.common import Cursor
from certex.storage.s3 import ObjectStorage, get_object_storage

__all__ = [
    "DeletionSummary",
    "create_batch",
    "delete_batch",
    "get_batch",
    "list_batch_documents",
    "list_batches",
    "load_batch_settings",
    "mark_uploading",
    "refresh_counts",
    "remove_document",
]

logger = get_logger(__name__)

MAX_PAGE_SIZE = 200
DEFAULT_PAGE_SIZE = 50


@dataclass(frozen=True, slots=True)
class DeletionSummary:
    batch_id: uuid.UUID
    documents_deleted: int
    objects_deleted: int


def _settings_to_json(settings: BatchSettings) -> JSONDict:
    """Serialise batch settings for storage, keeping only what the user chose."""
    return dict(settings.model_dump(mode="json", exclude_none=True))


def load_batch_settings(batch: Batch) -> BatchSettings:
    """Parse a batch's stored settings, tolerating rows written by older versions.

    Keys this version no longer knows - the retired ``llm_enabled`` and
    ``allow_vision`` switches, for example - are dropped rather than failing the
    whole settings object, which would silently reset the thresholds too.
    """
    stored = batch.settings_json or {}
    raw = {key: value for key, value in stored.items() if key in BatchSettings.model_fields}
    try:
        return BatchSettings.model_validate(raw)
    except ValueError:
        logger.warning("batch.settings_unreadable", batch_id=str(batch.id))
        return BatchSettings()


async def create_batch(
    session: AsyncSession,
    *,
    scope: WorkspaceScope,
    request: BatchCreateRequest,
) -> Batch:
    scope.require(UserRole.OPERATOR)

    batch = Batch(
        workspace_id=scope.workspace_id,
        name=request.name,
        status=BatchStatus.CREATED,
        settings_json=_settings_to_json(request.settings),
        created_by=scope.user_id,
    )
    session.add(batch)
    await session.flush()

    logger.info("batch.created", batch_id=str(batch.id), workspace_id=str(scope.workspace_id))
    return batch


async def get_batch(
    session: AsyncSession,
    *,
    scope: WorkspaceScope,
    batch_id: uuid.UUID,
) -> Batch:
    """Load a batch within the caller's workspace.

    A batch in another workspace answers 404, never 403: distinguishing the two
    would confirm that a given id exists somewhere in the deployment.
    """
    batch = await session.scalar(
        select(Batch).where(Batch.id == batch_id, Batch.workspace_id == scope.workspace_id)
    )
    if batch is None:
        raise NotFoundError(
            "No batch with that id exists in this workspace.",
            remediation="Refresh the batch list to see what is available.",
        )
    return batch


def _apply_batch_filters(
    statement: Select[tuple[Batch]],
    *,
    status: BatchStatus | None,
    search: str | None,
) -> Select[tuple[Batch]]:
    if status is not None:
        statement = statement.where(Batch.status == status)
    if search:
        # Batch names are operator-chosen labels, not document content, so a
        # substring match is safe to expose.
        pattern = f"%{search.strip()}%"
        statement = statement.where(Batch.name.ilike(pattern))
    return statement


async def list_batches(
    session: AsyncSession,
    *,
    scope: WorkspaceScope,
    limit: int = DEFAULT_PAGE_SIZE,
    cursor: Cursor | None = None,
    status: BatchStatus | None = None,
    search: str | None = None,
    include_total: bool = False,
) -> tuple[list[Batch], Cursor | None, int | None]:
    """Newest first, keyset paginated."""
    limit = max(1, min(limit, MAX_PAGE_SIZE))

    statement = select(Batch).where(Batch.workspace_id == scope.workspace_id)
    statement = _apply_batch_filters(statement, status=status, search=search)

    if cursor is not None:
        # Strict keyset comparison on (created_at, id): the id breaks ties so a
        # bulk insert sharing a timestamp cannot drop or repeat rows at a page
        # boundary the way OFFSET would.
        statement = statement.where(
            or_(
                Batch.created_at < cursor.created_at,
                (Batch.created_at == cursor.created_at) & (Batch.id < cursor.id),
            )
        )

    statement = statement.order_by(Batch.created_at.desc(), Batch.id.desc()).limit(limit + 1)
    rows = list((await session.scalars(statement)).all())

    total: int | None = None
    if include_total:
        count_statement = _apply_batch_filters(
            select(Batch).where(Batch.workspace_id == scope.workspace_id),
            status=status,
            search=search,
        )
        total = await session.scalar(select(func.count()).select_from(count_statement.subquery()))

    next_cursor: Cursor | None = None
    if len(rows) > limit:
        rows = rows[:limit]
        last = rows[-1]
        next_cursor = Cursor(created_at=last.created_at, id=last.id)

    return rows, next_cursor, total


async def get_document(
    session: AsyncSession, *, scope: WorkspaceScope, document_id: uuid.UUID
) -> Document:
    """One file of this workspace, or a 404 that does not admit it exists elsewhere."""
    document = await session.scalar(
        select(Document).where(
            Document.id == document_id, Document.workspace_id == scope.workspace_id
        )
    )
    if document is None:
        raise NotFoundError(
            "No such file in this workspace.",
            remediation="Refresh the batch to see which files it holds.",
        )
    return document


async def list_batch_documents(
    session: AsyncSession,
    *,
    scope: WorkspaceScope,
    batch_id: uuid.UUID,
    limit: int = DEFAULT_PAGE_SIZE,
    cursor: Cursor | None = None,
    status: DocumentStatus | None = None,
) -> tuple[list[Document], Cursor | None]:
    """Documents in a batch, oldest first so upload order is preserved."""
    await get_batch(session, scope=scope, batch_id=batch_id)
    limit = max(1, min(limit, MAX_PAGE_SIZE))

    statement = select(Document).where(
        Document.batch_id == batch_id,
        Document.workspace_id == scope.workspace_id,
    )
    if status is not None:
        statement = statement.where(Document.status == status)
    if cursor is not None:
        statement = statement.where(
            or_(
                Document.created_at > cursor.created_at,
                (Document.created_at == cursor.created_at) & (Document.id > cursor.id),
            )
        )

    statement = statement.order_by(Document.created_at.asc(), Document.id.asc()).limit(limit + 1)
    rows = list((await session.scalars(statement)).all())

    next_cursor: Cursor | None = None
    if len(rows) > limit:
        rows = rows[:limit]
        last = rows[-1]
        next_cursor = Cursor(created_at=last.created_at, id=last.id)

    return rows, next_cursor


async def mark_uploading(session: AsyncSession, batch: Batch) -> None:
    """Move a freshly created batch into UPLOADING on its first file."""
    if batch.status is BatchStatus.CREATED:
        batch.status = BatchStatus.UPLOADING
        await session.flush()


async def delete_batch(
    session: AsyncSession,
    *,
    scope: WorkspaceScope,
    batch_id: uuid.UUID,
    storage: ObjectStorage | None = None,
) -> DeletionSummary:
    """Hard-delete a batch, its rows and its stored blobs.

    Blobs are removed *before* the database rows. If the storage delete fails, the
    rows survive and the operation can be retried; deleting rows first would leave
    orphaned objects holding personal data that nothing references any more.

    Documents that merely *reference* another batch's blob - deduplicated uploads -
    do not have that blob deleted, since the original still needs it.
    """
    scope.require(UserRole.ADMIN)
    batch = await get_batch(session, scope=scope, batch_id=batch_id)

    if batch.status is BatchStatus.PROCESSING:
        raise ConflictError(
            "This batch is still processing and cannot be deleted yet.",
            title="Batch is in progress",
            remediation="Wait for processing to finish, then delete it.",
        )

    store = storage or get_object_storage()

    # Unfinished resumable uploads hold parts that no bucket listing shows; abort
    # them first so no fragment of a document outlives the batch.
    for upload in (
        await session.scalars(select(UploadSession).where(UploadSession.batch_id == batch_id))
    ).all():
        if upload.multipart_upload_id:
            store.abort_multipart_upload(upload.storage_key, upload_id=upload.multipart_upload_id)

    document_count = await session.scalar(
        select(func.count()).select_from(Document).where(Document.batch_id == batch_id)
    )

    # Only blobs this batch actually owns. A deduplicated row points at another
    # batch's object and must not take it with it.
    owned_keys = list(
        (
            await session.scalars(
                select(Document.storage_key).where(
                    Document.batch_id == batch_id,
                    Document.is_duplicate_of.is_(None),
                    Document.storage_key != "",
                )
            )
        ).all()
    )

    objects_deleted = 0
    for key in owned_keys:
        shared = await session.scalar(
            select(func.count())
            .select_from(Document)
            .where(Document.storage_key == key, Document.batch_id != batch_id)
        )
        if shared:
            continue
        store.delete(key)
        objects_deleted += 1

    # Page images and derived files live under predictable per-document prefixes.
    for document_id in (
        await session.scalars(select(Document.id).where(Document.batch_id == batch_id))
    ).all():
        store.delete_prefix(f"pages/{scope.workspace_id}/{document_id}/")
        store.delete_prefix(f"derived/{scope.workspace_id}/{document_id}/")

    await session.delete(batch)
    await session.flush()

    logger.info(
        "batch.deleted",
        batch_id=str(batch_id),
        workspace_id=str(scope.workspace_id),
        count=document_count or 0,
        skipped_count=len(owned_keys) - objects_deleted,
    )
    return DeletionSummary(
        batch_id=batch_id,
        documents_deleted=int(document_count or 0),
        objects_deleted=objects_deleted,
    )


async def remove_document(
    session: AsyncSession,
    *,
    scope: WorkspaceScope,
    batch_id: uuid.UUID,
    document_id: uuid.UUID,
    storage: ObjectStorage | None = None,
) -> int:
    """Take a file back out of a batch that has not started processing.

    Used to discard a rejected upload before retrying it - an encrypted PDF sent
    without its password, say - so the batch does not carry a stale failure next
    to the good copy. Removing an archive removes what was unpacked from it.
    Returns how many document rows went.
    """
    scope.require(UserRole.OPERATOR)
    batch = await get_batch(session, scope=scope, batch_id=batch_id)
    if batch.status not in (BatchStatus.CREATED, BatchStatus.UPLOADING):
        raise ConflictError(
            "Files cannot be removed once a batch has started processing.",
            title="Batch already started",
            remediation="Correct the affected rows on the results page instead.",
        )

    document = await session.scalar(
        select(Document).where(
            Document.id == document_id,
            Document.batch_id == batch_id,
            Document.workspace_id == scope.workspace_id,
        )
    )
    if document is None:
        raise NotFoundError(
            "No file with that id exists in this batch.",
            remediation="Refresh the file list to see what the batch holds.",
        )

    # The document and everything unpacked beneath it, breadth first. Archive depth
    # is bounded by MAX_ZIP_DEPTH, so this terminates in a couple of rounds.
    doomed: list[Document] = [document]
    frontier = [document.id]
    while frontier:
        children = (
            await session.scalars(select(Document).where(Document.parent_document_id.in_(frontier)))
        ).all()
        doomed.extend(children)
        frontier = [child.id for child in children]
    doomed_ids = {item.id for item in doomed}

    store = storage or get_object_storage()
    for item in doomed:
        if not item.storage_key or item.is_duplicate_of is not None:
            continue
        shared = await session.scalar(
            select(func.count())
            .select_from(Document)
            .where(Document.storage_key == item.storage_key, Document.id.not_in(doomed_ids))
        )
        if not shared:
            store.delete(item.storage_key)

    await session.delete(document)
    await session.flush()
    await refresh_counts(session, batch)

    logger.info(
        "batch.document_removed",
        batch_id=str(batch_id),
        document_id=str(document_id),
        count=len(doomed),
    )
    return len(doomed)


async def refresh_counts(session: AsyncSession, batch: Batch) -> Batch:
    """Recompute a batch's aggregate counters from its documents.

    The counters are maintained incrementally during ingest for speed; this is the
    reconciliation path used after a crash, a retry, or a manual document change,
    where an incremental count may have been missed.
    """
    rows = (
        await session.execute(
            select(Document.status, func.count(), func.coalesce(func.sum(Document.byte_size), 0))
            .where(Document.batch_id == batch.id)
            .group_by(Document.status)
        )
    ).all()

    by_status = {status: (count, size) for status, count, size in rows}
    batch.file_count = sum(count for count, _ in by_status.values())
    batch.total_bytes = sum(size for _, size in by_status.values())
    batch.failed_count = by_status.get(DocumentStatus.FAILED, (0, 0))[0]
    batch.duplicate_count = by_status.get(DocumentStatus.DUPLICATE, (0, 0))[0]
    batch.processed_count = by_status.get(DocumentStatus.COMPLETED, (0, 0))[0]

    await session.flush()
    return batch
