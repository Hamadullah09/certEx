"""Batch and upload routes."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, File, Query, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from certex.core.audit import record_audit
from certex.core.deps import AuditContextDep, SessionDep, SettingsDep, WorkspaceScopeDep
from certex.core.errors import AppError, BadRequestError, ConflictError
from certex.core.ratelimit import get_rate_limiter
from certex.db.models import Batch, Document
from certex.enums import AuditAction, BatchStatus, DocumentStatus, UserRole
from certex.logging_setup import get_logger
from certex.pipeline.safety import sanitise_filename
from certex.schemas.batches import (
    BatchCreateRequest,
    BatchDetail,
    BatchSummary,
    DocumentSummary,
    UploadedFile,
)
from certex.schemas.common import Cursor, Page
from certex.services import batch_service
from certex.services.ingest_service import ingest_stream

__all__ = ["router"]

logger = get_logger(__name__)
router = APIRouter(prefix="/batches", tags=["batches"])

CursorParam = Annotated[str | None, Query(description="Opaque cursor from a previous page.")]
LimitParam = Annotated[int, Query(ge=1, le=200, description="Maximum rows to return.")]


def _to_detail(batch: Batch) -> BatchDetail:
    detail = BatchDetail.model_validate(batch)
    return detail.model_copy(update={"settings": batch_service.load_batch_settings(batch)})


@router.post(
    "",
    response_model=BatchDetail,
    status_code=status.HTTP_201_CREATED,
    summary="Create a batch",
    responses={403: {"description": "Viewers cannot create batches."}},
)
async def create_batch(
    payload: BatchCreateRequest,
    session: SessionDep,
    scope: WorkspaceScopeDep,
    audit: AuditContextDep,
) -> BatchDetail:
    """Create an empty batch, ready to receive files.

    Settings chosen here are frozen onto the batch, so a later change to a
    workspace default does not retroactively alter how these documents were read.
    """
    batch = await batch_service.create_batch(session, scope=scope, request=payload)
    await record_audit(
        session,
        AuditAction.BATCH_CREATED,
        audit,
        entity_type="batch",
        entity_id=batch.id,
    )
    await session.commit()
    return _to_detail(batch)


@router.get(
    "",
    response_model=Page[BatchSummary],
    summary="List batches, newest first",
)
async def list_batches(
    session: SessionDep,
    scope: WorkspaceScopeDep,
    limit: LimitParam = 50,
    cursor: CursorParam = None,
    batch_status: Annotated[
        BatchStatus | None, Query(alias="filter[status]", description="Only this status.")
    ] = None,
    search: Annotated[str | None, Query(max_length=200)] = None,
    include_total: Annotated[
        bool, Query(description="Also return a total count. Costs an extra query.")
    ] = False,
) -> Page[BatchSummary]:
    rows, next_cursor, total = await batch_service.list_batches(
        session,
        scope=scope,
        limit=limit,
        cursor=Cursor.decode(cursor) if cursor else None,
        status=batch_status,
        search=search,
        include_total=include_total,
    )
    return Page.build(
        [BatchSummary.model_validate(row) for row in rows],
        limit=limit,
        next_cursor=next_cursor,
        total=total,
    )


@router.get(
    "/{batch_id}",
    response_model=BatchDetail,
    summary="Batch detail and aggregate counts",
    responses={404: {"description": "No such batch in this workspace."}},
)
async def get_batch(
    batch_id: uuid.UUID,
    session: SessionDep,
    scope: WorkspaceScopeDep,
) -> BatchDetail:
    batch = await batch_service.get_batch(session, scope=scope, batch_id=batch_id)
    return _to_detail(batch)


@router.get(
    "/{batch_id}/documents",
    response_model=Page[DocumentSummary],
    summary="Files in a batch, in upload order",
)
async def list_documents(
    batch_id: uuid.UUID,
    session: SessionDep,
    scope: WorkspaceScopeDep,
    limit: LimitParam = 50,
    cursor: CursorParam = None,
    document_status: Annotated[DocumentStatus | None, Query(alias="filter[status]")] = None,
) -> Page[DocumentSummary]:
    rows, next_cursor = await batch_service.list_batch_documents(
        session,
        scope=scope,
        batch_id=batch_id,
        limit=limit,
        cursor=Cursor.decode(cursor) if cursor else None,
        status=document_status,
    )
    return Page.build(
        [DocumentSummary.model_validate(row) for row in rows],
        limit=limit,
        next_cursor=next_cursor,
    )


@router.post(
    "/{batch_id}/files",
    response_model=list[UploadedFile],
    status_code=status.HTTP_201_CREATED,
    summary="Upload one or more files into a batch",
    responses={
        409: {"description": "The batch has already started processing."},
        413: {"description": "A file, or the batch, exceeded its size limit."},
        415: {"description": "The file content is not a supported document type."},
        422: {"description": "The document is encrypted, corrupt or empty."},
        429: {"description": "Upload rate limit exceeded."},
    },
)
async def upload_files(
    batch_id: uuid.UUID,
    session: SessionDep,
    scope: WorkspaceScopeDep,
    settings: SettingsDep,
    audit: AuditContextDep,
    files: Annotated[list[UploadFile], File(description="One or more documents.")],
) -> list[UploadedFile]:
    """Accept files and record them as documents.

    Each file is processed independently: a corrupt or unsupported file is
    recorded as FAILED with its reason and the rest of the request still
    succeeds, because rejecting a 300-file upload over one bad scan would be
    useless to the operator.

    The whole request commits once at the end, so document rows and the batch's
    counters can never disagree.
    """
    scope.require(UserRole.OPERATOR)

    limiter = get_rate_limiter()
    decision = await limiter.check(
        "upload", str(scope.user_id), limit=settings.rate_limit_upload_per_minute
    )
    decision.raise_if_denied(what="upload requests")

    batch = await batch_service.get_batch(session, scope=scope, batch_id=batch_id)

    if batch.status not in (BatchStatus.CREATED, BatchStatus.UPLOADING):
        raise ConflictError(
            f"This batch is {batch.status.value.lower()} and no longer accepts files.",
            title="Batch is closed to uploads",
            remediation="Create a new batch for these files.",
        )

    if not files:
        raise BadRequestError(
            "No files were included in the request.",
            remediation="Attach at least one file and try again.",
        )

    if batch.file_count + len(files) > settings.max_batch_files:
        raise BadRequestError(
            f"A batch may hold at most {settings.max_batch_files} files; this "
            f"request would take it to {batch.file_count + len(files)}.",
            title="Too many files",
            remediation="Split the upload across several batches.",
        )

    await batch_service.mark_uploading(session, batch)

    results: list[UploadedFile] = []
    for upload in files:
        try:
            outcome = await ingest_stream(
                session,
                batch=batch,
                workspace_id=scope.workspace_id,
                stream=upload.file,
                filename=upload.filename or "document",
                declared_mime=upload.content_type,
                settings=settings,
            )
            results.append(outcome.uploaded)
        except Exception as exc:  # noqa: BLE001 - one bad file must not fail the batch
            results.append(
                await _record_failed_upload(
                    session,
                    batch=batch,
                    workspace_id=scope.workspace_id,
                    filename=upload.filename or "document",
                    exc=exc,
                )
            )
        finally:
            await upload.close()

    await record_audit(
        session,
        AuditAction.DOCUMENT_UPLOADED,
        audit,
        entity_type="batch",
        entity_id=batch.id,
        metadata={
            "count": len(results),
            "duplicate_count": sum(1 for item in results if item.is_duplicate),
            "failed_count": sum(1 for item in results if item.status is DocumentStatus.FAILED),
        },
    )
    await session.commit()

    logger.info(
        "upload.completed",
        batch_id=str(batch.id),
        count=len(results),
        byte_size=sum(item.byte_size for item in results),
    )
    return results


async def _record_failed_upload(
    session: AsyncSession,
    *,
    batch: Batch,
    workspace_id: uuid.UUID,
    filename: str,
    exc: Exception,
) -> UploadedFile:
    """Persist a rejected file so the operator can see what happened and why."""
    safe_name = sanitise_filename(filename)
    code = exc.code.value if isinstance(exc, AppError) else "ingest_failed"
    message = str(exc) if isinstance(exc, AppError) else "The file could not be ingested."

    document = Document(
        batch_id=batch.id,
        workspace_id=workspace_id,
        original_filename=safe_name,
        mime_type="application/octet-stream",
        byte_size=0,
        sha256="",
        storage_key="",
        status=DocumentStatus.FAILED,
        error_code=code,
        error_message=message[:1000],
    )
    session.add(document)
    await session.flush()

    batch.file_count += 1
    batch.failed_count += 1

    logger.warning(
        "upload.rejected",
        batch_id=str(batch.id),
        document_id=str(document.id),
        error_code=code,
    )
    return UploadedFile(
        document_id=document.id,
        original_filename=safe_name,
        byte_size=0,
        sha256="",
        mime_type="application/octet-stream",
        status=DocumentStatus.FAILED,
    )


@router.delete(
    "/{batch_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    # `from __future__ import annotations` turns the `-> None` return annotation
    # into the string "None", which FastAPI resolves to NoneType and treats as a
    # response model - illegal on a 204. Stating it explicitly settles the point.
    response_model=None,
    summary="Permanently delete a batch, its documents and its stored files",
    responses={
        403: {"description": "Only administrators may delete a batch."},
        409: {"description": "The batch is still processing."},
    },
)
async def delete_batch(
    batch_id: uuid.UUID,
    session: SessionDep,
    scope: WorkspaceScopeDep,
    audit: AuditContextDep,
) -> None:
    """Hard delete. Blobs go first, then the rows - see ``delete_batch``."""
    summary = await batch_service.delete_batch(session, scope=scope, batch_id=batch_id)
    await record_audit(
        session,
        AuditAction.BATCH_DELETED,
        audit,
        entity_type="batch",
        entity_id=batch_id,
        metadata={
            "count": summary.documents_deleted,
            "skipped_count": summary.documents_deleted - summary.objects_deleted,
        },
    )
    await session.commit()
