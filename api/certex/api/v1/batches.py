"""Batch and upload routes."""

from __future__ import annotations

import json
import uuid
from typing import Annotated

from fastapi import APIRouter, File, Form, Query, Request, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from certex.core.audit import record_audit
from certex.core.deps import AuditContextDep, SessionDep, SettingsDep, WorkspaceScopeDep
from certex.core.errors import BadRequestError, ConflictError, PayloadTooLargeError
from certex.core.ratelimit import get_rate_limiter
from certex.db.models import Batch
from certex.enums import AuditAction, BatchStatus, DocumentStatus, UserRole
from certex.logging_setup import get_logger
from certex.pipeline.dispatch import TASK_TEXT_DOCUMENT, enqueue
from certex.schemas.batches import (
    BatchCreateRequest,
    BatchDetail,
    BatchSummary,
    DocumentSummary,
    UploadCompleteRequest,
    UploadedFile,
    UploadInit,
    UploadSessionState,
)
from certex.schemas.common import Cursor, Page
from certex.services import batch_service, pipeline_service, upload_service
from certex.services.ingest_service import ingest_stream, record_failed_upload
from certex.storage.s3 import get_object_storage

__all__ = ["router"]

logger = get_logger(__name__)
router = APIRouter(prefix="/batches", tags=["batches"])

CursorParam = Annotated[str | None, Query(description="Opaque cursor from a previous page.")]
LimitParam = Annotated[int, Query(ge=1, le=200, description="Maximum rows to return.")]

_MAX_PASSWORD_LENGTH = 256


async def _to_detail(session: AsyncSession, batch: Batch) -> BatchDetail:
    """One batch, with the columns every document in it is read for.

    The columns are resolved rather than stored on the row, because a batch that
    pinned no schema still has columns - the built-in ones for its type - and a screen
    that showed nothing for those batches would be wrong about what is extracted.
    """
    detail = BatchDetail.model_validate(batch)
    return detail.model_copy(
        update={
            "settings": batch_service.load_batch_settings(batch),
            "columns": await batch_service.columns_for(session, batch),
        }
    )


def _parse_passwords(raw: str | None, *, max_entries: int) -> dict[str, str]:
    """Decode the ``passwords`` form field: ``{"<uploaded file name>": "<password>"}``.

    Errors never quote the submitted value - it is, by construction, a secret.
    """
    if raw is None or not raw.strip():
        return {}
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise BadRequestError(
            "The passwords field is not valid JSON.",
            remediation='Send a JSON object such as {"scan.pdf": "password"}.',
        ) from exc
    if not isinstance(decoded, dict) or len(decoded) > max_entries:
        raise BadRequestError(
            "The passwords field must be a JSON object mapping file names to passwords.",
            remediation='Send a JSON object such as {"scan.pdf": "password"}.',
        )
    passwords: dict[str, str] = {}
    for name, secret in decoded.items():
        if not isinstance(secret, str) or not 0 < len(secret) <= _MAX_PASSWORD_LENGTH:
            raise BadRequestError(
                f"Each password must be text of 1 to {_MAX_PASSWORD_LENGTH} characters.",
                remediation="Correct the password entered for the file and try again.",
            )
        passwords[str(name)] = secret
    return passwords


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
    return await _to_detail(session, batch)


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
    certificate_type_id: Annotated[
        uuid.UUID | None,
        Query(
            alias="filter[certificate_type_id]",
            description="Only batches in this certificate category.",
        ),
    ] = None,
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
        certificate_type_id=certificate_type_id,
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
    return await _to_detail(session, batch)


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


@router.delete(
    "/{batch_id}/documents/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    summary="Remove a file from a batch that has not started processing",
    responses={409: {"description": "The batch has already started processing."}},
)
async def remove_document(
    batch_id: uuid.UUID,
    document_id: uuid.UUID,
    session: SessionDep,
    scope: WorkspaceScopeDep,
    audit: AuditContextDep,
) -> None:
    removed = await batch_service.remove_document(
        session, scope=scope, batch_id=batch_id, document_id=document_id
    )
    await record_audit(
        session,
        AuditAction.DOCUMENT_REMOVED,
        audit,
        entity_type="document",
        entity_id=document_id,
        metadata={"batch_id": str(batch_id), "count": removed},
    )
    await session.commit()


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
    passwords: Annotated[
        str | None,
        Form(
            description=(
                "JSON object mapping an uploaded file's name to the password that opens "
                "it. Each password is used once, at ingest, and never stored."
            )
        ),
    ] = None,
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

    password_by_name = _parse_passwords(passwords, max_entries=settings.max_batch_files)
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
        filename = upload.filename or "document"
        try:
            outcome = await ingest_stream(
                session,
                batch=batch,
                workspace_id=scope.workspace_id,
                stream=upload.file,
                filename=filename,
                declared_mime=upload.content_type,
                password=password_by_name.get(filename),
                settings=settings,
            )
            results.append(outcome.uploaded)
        except Exception as exc:  # noqa: BLE001 - one bad file must not fail the batch
            results.append(
                await record_failed_upload(
                    session,
                    batch=batch,
                    workspace_id=scope.workspace_id,
                    filename=filename,
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


@router.post(
    "/{batch_id}/start",
    response_model=BatchDetail,
    summary="Start processing every uploaded file in the batch",
    responses={
        409: {"description": "Already started, uploads unfinished, or no files."},
    },
)
async def start_batch(
    batch_id: uuid.UUID,
    session: SessionDep,
    scope: WorkspaceScopeDep,
    audit: AuditContextDep,
) -> BatchDetail:
    """Close the batch to uploads and enqueue its documents for processing.

    Tasks are enqueued only after the commit, so a worker picking one up always
    finds the batch already marked as processing.
    """
    batch, document_ids = await pipeline_service.start_batch(
        session, scope=scope, batch_id=batch_id
    )
    await record_audit(
        session,
        AuditAction.BATCH_STARTED,
        audit,
        entity_type="batch",
        entity_id=batch.id,
        metadata={"count": len(document_ids)},
    )
    await session.commit()

    for document_id in document_ids:
        enqueue(TASK_TEXT_DOCUMENT, {"document_id": str(document_id)})
    return await _to_detail(session, batch)


# ---------------------------------------------------------------------------
# Resumable uploads
# ---------------------------------------------------------------------------
@router.post(
    "/{batch_id}/uploads",
    response_model=UploadSessionState,
    status_code=status.HTTP_201_CREATED,
    summary="Start, or resume, a chunked upload",
    responses={
        409: {"description": "The batch has already started processing."},
        413: {"description": "The file, or the batch, would exceed its size limit."},
        429: {"description": "Upload rate limit exceeded."},
    },
)
async def open_upload(
    batch_id: uuid.UUID,
    payload: UploadInit,
    session: SessionDep,
    scope: WorkspaceScopeDep,
    settings: SettingsDep,
) -> UploadSessionState:
    """Announce a file. Announcing the same ``client_file_id`` again resumes it.

    The response says how many bytes the server already holds; send the chunk that
    starts there. A browser that crashed mid-upload therefore loses at most one
    chunk of work.
    """
    decision = await get_rate_limiter().check(
        "upload", str(scope.user_id), limit=settings.rate_limit_upload_per_minute
    )
    decision.raise_if_denied(what="upload requests")

    upload = await upload_service.open_session(
        session,
        scope=scope,
        batch_id=batch_id,
        request=payload,
        settings=settings,
        storage=get_object_storage(),
    )
    await session.commit()
    return upload_service.to_state(upload, settings)


@router.get(
    "/{batch_id}/uploads/{upload_id}",
    response_model=UploadSessionState,
    summary="Where a chunked upload stands",
)
async def get_upload(
    batch_id: uuid.UUID,
    upload_id: uuid.UUID,
    session: SessionDep,
    scope: WorkspaceScopeDep,
    settings: SettingsDep,
) -> UploadSessionState:
    upload = await upload_service.get_session(
        session, scope=scope, batch_id=batch_id, upload_id=upload_id
    )
    return upload_service.to_state(upload, settings)


async def _read_bounded_body(request: Request, *, limit: int) -> bytes:
    """Read a request body, refusing it the moment it exceeds ``limit`` bytes.

    Checks the declared length first, but does not trust it: the stream is counted
    as it arrives, so a client that understates Content-Length gains nothing.
    """
    declared = request.headers.get("content-length")
    if declared is not None:
        try:
            if int(declared) > limit:
                raise PayloadTooLargeError(
                    f"A chunk may be at most {limit} bytes.",
                    title="Chunk too large",
                    remediation="Split the file into chunks of the size the upload reported.",
                )
        except ValueError as exc:
            raise BadRequestError("Content-Length must be a whole number.") from exc

    received = bytearray()
    async for part in request.stream():
        received.extend(part)
        if len(received) > limit:
            raise PayloadTooLargeError(
                f"A chunk may be at most {limit} bytes.",
                title="Chunk too large",
                remediation="Split the file into chunks of the size the upload reported.",
            )
    return bytes(received)


@router.put(
    "/{batch_id}/uploads/{upload_id}/chunks/{offset}",
    response_model=UploadSessionState,
    summary="Send the chunk that starts at a byte offset",
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {"application/octet-stream": {"schema": {"type": "string"}}},
        }
    },
    responses={
        400: {"description": "Wrong offset or chunk length."},
        409: {"description": "Out of order, expired, or already complete."},
    },
)
async def put_chunk(
    batch_id: uuid.UUID,
    upload_id: uuid.UUID,
    offset: int,
    request: Request,
    session: SessionDep,
    scope: WorkspaceScopeDep,
    settings: SettingsDep,
) -> UploadSessionState:
    """Store one chunk. Resending a chunk that already arrived is harmless."""
    scope.require(UserRole.OPERATOR)
    payload = await _read_bounded_body(request, limit=upload_service.chunk_size(settings))
    upload = await upload_service.receive_chunk(
        session,
        scope=scope,
        batch_id=batch_id,
        upload_id=upload_id,
        offset=offset,
        payload=payload,
        settings=settings,
        storage=get_object_storage(),
    )
    await session.commit()
    return upload_service.to_state(upload, settings)


@router.post(
    "/{batch_id}/uploads/{upload_id}/complete",
    response_model=UploadedFile,
    status_code=status.HTTP_201_CREATED,
    summary="Assemble a chunked upload and ingest it",
    responses={409: {"description": "Not every byte has arrived yet."}},
)
async def complete_upload(
    batch_id: uuid.UUID,
    upload_id: uuid.UUID,
    payload: UploadCompleteRequest,
    session: SessionDep,
    scope: WorkspaceScopeDep,
    settings: SettingsDep,
    audit: AuditContextDep,
) -> UploadedFile:
    """Finish the upload. A file the ingest checks reject is returned as FAILED.

    Completing an already completed upload returns the same result again, so a
    client that lost the first response can simply ask twice.
    """
    result = await upload_service.complete_session(
        session,
        scope=scope,
        batch_id=batch_id,
        upload_id=upload_id,
        password=payload.password,
        settings=settings,
        storage=get_object_storage(),
    )
    await record_audit(
        session,
        AuditAction.DOCUMENT_UPLOADED,
        audit,
        entity_type="batch",
        entity_id=batch_id,
        metadata={
            "count": 1,
            "duplicate_count": 1 if result.is_duplicate else 0,
            "failed_count": 1 if result.status is DocumentStatus.FAILED else 0,
            "job_id": str(upload_id),
        },
    )
    await session.commit()
    return result


@router.delete(
    "/{batch_id}/uploads/{upload_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    summary="Cancel a chunked upload and discard what was sent",
)
async def abort_upload(
    batch_id: uuid.UUID,
    upload_id: uuid.UUID,
    session: SessionDep,
    scope: WorkspaceScopeDep,
) -> None:
    await upload_service.abort_session(
        session,
        scope=scope,
        batch_id=batch_id,
        upload_id=upload_id,
        storage=get_object_storage(),
    )
    await session.commit()


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
        409: {
            "description": ("The batch is still processing, or the register still cites its files.")
        },
    },
)
async def delete_batch(
    batch_id: uuid.UUID,
    session: SessionDep,
    scope: WorkspaceScopeDep,
    audit: AuditContextDep,
    force: Annotated[
        bool,
        Query(
            description=(
                "Delete even while the batch is still being read. For a batch that has "
                "stopped making progress and would otherwise never be deletable."
            )
        ),
    ] = False,
    include_register: Annotated[
        bool,
        Query(
            description=(
                "Also delete the register entries this batch filed. Without it, a batch "
                "whose certificates reached the register is refused, because those "
                "entries cite its scans and must not lose them by accident."
            )
        ),
    ] = False,
) -> None:
    """Hard delete. Blobs go first, then the rows - see ``delete_batch``."""
    summary = await batch_service.delete_batch(
        session,
        scope=scope,
        batch_id=batch_id,
        force=force,
        include_register=include_register,
    )
    await record_audit(
        session,
        AuditAction.BATCH_DELETED,
        audit,
        entity_type="batch",
        entity_id=batch_id,
        metadata={
            "count": summary.documents_deleted,
            "skipped_count": summary.documents_deleted - summary.objects_deleted,
            "forced": force,
            "register_entries_deleted": include_register,
        },
    )
    await session.commit()
