"""Resumable, chunked uploads.

A browser that closes halfway through a 400 MB scan must not have to start again.
The protocol is deliberately small:

1. ``open_session`` announces the file under a client-chosen stable id. Announcing
   the same id again returns the existing session, which is how a client resumes.
2. ``receive_chunk`` accepts the bytes starting at ``received_bytes``. Each chunk
   becomes one S3 multipart part, so a chunk is never held beyond one request and
   the assembled file never exists in this process's memory.
3. ``complete_session`` assembles the parts in storage, then runs the same ingest
   path as a direct upload: sniffing, dedupe, the encryption probe.

Every function is scoped by workspace *and* batch, and locks the session row while
it changes it, so two tabs resuming the same file cannot interleave parts.
"""

from __future__ import annotations

import datetime as dt
import tempfile
import uuid
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from certex.config import Settings
from certex.core.deps import WorkspaceScope
from certex.core.errors import (
    BadRequestError,
    ConflictError,
    NotFoundError,
    PayloadTooLargeError,
    UploadIncompleteError,
    remediation_for,
)
from certex.db.base import JSONList
from certex.db.models import Batch, Document, UploadSession
from certex.enums import BatchStatus, UserRole
from certex.logging_setup import get_logger
from certex.schemas.batches import UploadedFile, UploadInit, UploadSessionState
from certex.services import batch_service
from certex.services.ingest_service import ingest_local_file, record_failed_upload
from certex.storage.s3 import ObjectStorage, StorageKeys

__all__ = [
    "MIN_PART_BYTES",
    "abort_session",
    "chunk_size",
    "complete_session",
    "get_session",
    "open_session",
    "purge_expired_sessions",
    "receive_chunk",
    "to_state",
    "uploaded_file_for",
]

logger = get_logger(__name__)

MIN_PART_BYTES = 5 * 1024 * 1024
"""S3 rejects any multipart part below 5 MiB except the final one."""

_ACCEPTING = (BatchStatus.CREATED, BatchStatus.UPLOADING)


def chunk_size(settings: Settings) -> int:
    """The chunk length every non-final chunk must have."""
    return max(settings.upload_chunk_bytes, MIN_PART_BYTES)


def to_state(upload: UploadSession, settings: Settings) -> UploadSessionState:
    return UploadSessionState(
        upload_id=upload.id,
        client_file_id=upload.client_file_id,
        filename=upload.original_filename,
        declared_size=upload.declared_size,
        received_bytes=upload.received_bytes,
        chunk_size=chunk_size(settings),
        is_complete=upload.is_complete,
        document_id=upload.document_id,
        expires_at=upload.expires_at,
    )


def _require_accepting(batch: Batch) -> None:
    if batch.status not in _ACCEPTING:
        raise ConflictError(
            f"This batch is {batch.status.value.lower()} and no longer accepts files.",
            title="Batch is closed to uploads",
            remediation="Create a new batch for these files.",
        )


def _parts(upload: UploadSession) -> list[tuple[int, str, int]]:
    """Recorded parts as ``(part_number, etag, size)``, narrowed from JSONB."""
    parts: list[tuple[int, str, int]] = []
    for entry in upload.parts_jsonb:
        if not isinstance(entry, dict):
            continue
        number, etag, size = entry.get("part_number"), entry.get("etag"), entry.get("size")
        if isinstance(number, int) and isinstance(etag, str) and isinstance(size, int):
            parts.append((number, etag, size))
    return parts


async def _load_locked(
    session: AsyncSession, *, scope: WorkspaceScope, batch_id: uuid.UUID, upload_id: uuid.UUID
) -> UploadSession:
    upload = await session.scalar(
        select(UploadSession)
        .where(
            UploadSession.id == upload_id,
            UploadSession.batch_id == batch_id,
            UploadSession.workspace_id == scope.workspace_id,
        )
        .with_for_update()
    )
    if upload is None:
        raise NotFoundError(
            "No upload with that id exists for this batch.",
            remediation="Start the upload again; nothing was kept from the old attempt.",
        )
    return upload


def _expired(upload: UploadSession, now: dt.datetime) -> bool:
    return not upload.is_complete and upload.expires_at <= now


async def open_session(
    session: AsyncSession,
    *,
    scope: WorkspaceScope,
    batch_id: uuid.UUID,
    request: UploadInit,
    settings: Settings,
    storage: ObjectStorage,
) -> UploadSession:
    """Start a resumable upload, or return the one already open for this file."""
    scope.require(UserRole.OPERATOR)
    batch = await batch_service.get_batch(session, scope=scope, batch_id=batch_id)
    _require_accepting(batch)

    if request.size > settings.max_file_bytes:
        raise PayloadTooLargeError(
            f"The file exceeds the {settings.max_file_bytes // (1024 * 1024)} MB limit.",
        )
    if batch.total_bytes + request.size > settings.max_batch_bytes:
        raise PayloadTooLargeError(
            "This file would take the batch over its total size limit.",
            title="Batch size limit reached",
            remediation="Start a second batch for the remaining files, or raise MAX_BATCH_BYTES.",
        )
    if batch.file_count + 1 > settings.max_batch_files:
        raise BadRequestError(
            f"A batch may hold at most {settings.max_batch_files} files.",
            title="Too many files",
            remediation="Split the upload across several batches.",
        )

    now = dt.datetime.now(dt.UTC)
    existing = await session.scalar(
        select(UploadSession)
        .where(
            UploadSession.batch_id == batch.id,
            UploadSession.client_file_id == request.client_file_id,
        )
        .with_for_update()
    )
    if existing is not None:
        same_file = existing.declared_size == request.size
        if same_file and not _expired(existing, now):
            logger.info("upload.resumed", batch_id=str(batch.id), job_id=str(existing.id))
            return existing
        # A different file under a reused id, or an upload left too long: discard it
        # rather than splice new bytes onto parts that belong to something else.
        if existing.multipart_upload_id:
            storage.abort_multipart_upload(
                existing.storage_key, upload_id=existing.multipart_upload_id
            )
        await session.delete(existing)
        await session.flush()

    upload_id = uuid.uuid4()
    key = StorageKeys.upload(scope.workspace_id, upload_id)
    multipart_id = storage.create_multipart_upload(
        key,
        # The declared type is not trusted; the assembled file is sniffed at completion.
        content_type="application/octet-stream",
        metadata={"workspace-id": str(scope.workspace_id)},
    )
    upload = UploadSession(
        id=upload_id,
        batch_id=batch.id,
        workspace_id=scope.workspace_id,
        client_file_id=request.client_file_id,
        original_filename=request.filename,
        declared_size=request.size,
        declared_mime_type=request.content_type,
        storage_key=key,
        multipart_upload_id=multipart_id,
        parts_jsonb=[],
        received_bytes=0,
        expires_at=now + dt.timedelta(hours=settings.upload_session_ttl_hours),
    )
    session.add(upload)
    await batch_service.mark_uploading(session, batch)
    await session.flush()

    logger.info(
        "upload.opened",
        batch_id=str(batch.id),
        job_id=str(upload.id),
        byte_size=request.size,
    )
    return upload


async def get_session(
    session: AsyncSession, *, scope: WorkspaceScope, batch_id: uuid.UUID, upload_id: uuid.UUID
) -> UploadSession:
    upload = await session.scalar(
        select(UploadSession).where(
            UploadSession.id == upload_id,
            UploadSession.batch_id == batch_id,
            UploadSession.workspace_id == scope.workspace_id,
        )
    )
    if upload is None:
        raise NotFoundError(
            "No upload with that id exists for this batch.",
            remediation="Start the upload again; nothing was kept from the old attempt.",
        )
    return upload


async def receive_chunk(
    session: AsyncSession,
    *,
    scope: WorkspaceScope,
    batch_id: uuid.UUID,
    upload_id: uuid.UUID,
    offset: int,
    payload: bytes,
    settings: Settings,
    storage: ObjectStorage,
) -> UploadSession:
    """Store the chunk that starts at ``offset``.

    Re-sending a chunk that already arrived is a no-op, so a client that lost the
    response to a successful request can simply retry it.
    """
    scope.require(UserRole.OPERATOR)
    upload = await _load_locked(session, scope=scope, batch_id=batch_id, upload_id=upload_id)
    now = dt.datetime.now(dt.UTC)

    if upload.is_complete:
        raise ConflictError(
            "This upload has already been completed.",
            title="Upload already complete",
            remediation="No further chunks are needed.",
        )
    if _expired(upload, now) or upload.multipart_upload_id is None:
        raise ConflictError(
            "This upload expired before it finished.",
            title="Upload expired",
            remediation="Start the upload again from the beginning.",
        )

    size = chunk_size(settings)
    if offset < 0 or offset % size != 0 or offset >= upload.declared_size:
        raise BadRequestError(
            f"Chunks must start at a multiple of {size} bytes within the file.",
            title="Invalid chunk offset",
            remediation=f"Resume from offset {upload.received_bytes}.",
        )

    expected_length = min(size, upload.declared_size - offset)
    if len(payload) != expected_length:
        raise BadRequestError(
            f"The chunk at offset {offset} must be {expected_length} bytes; got {len(payload)}.",
            title="Wrong chunk length",
            remediation="Send exactly the bytes for this chunk and retry.",
        )

    part_number = offset // size + 1
    recorded = {number: part_size for number, _etag, part_size in _parts(upload)}

    if offset < upload.received_bytes:
        if recorded.get(part_number) == expected_length:
            return upload
        raise ConflictError(  # pragma: no cover - parts are recorded contiguously
            "The upload's recorded parts are inconsistent.",
            remediation="Cancel this upload and start it again.",
        )

    if offset > upload.received_bytes:
        raise ConflictError(
            f"Chunks must arrive in order; this upload has {upload.received_bytes} bytes.",
            title="Chunk out of order",
            remediation=f"Resume from offset {upload.received_bytes}.",
        )

    etag = storage.upload_part(
        upload.storage_key,
        upload_id=upload.multipart_upload_id,
        part_number=part_number,
        payload=payload,
    )
    parts: JSONList = list(upload.parts_jsonb)
    parts.append({"part_number": part_number, "etag": etag, "size": expected_length})
    upload.parts_jsonb = parts
    upload.received_bytes += expected_length
    await session.flush()
    return upload


def _document_to_uploaded(document: Document) -> UploadedFile:
    return UploadedFile(
        document_id=document.id,
        original_filename=document.original_filename,
        byte_size=document.byte_size,
        sha256=document.sha256,
        mime_type=document.mime_type,
        status=document.status,
        is_duplicate=document.is_duplicate_of is not None,
        duplicate_of=document.is_duplicate_of,
        error_code=document.error_code,
        error_message=document.error_message,
        remediation=remediation_for(document.error_code),
    )


async def uploaded_file_for(session: AsyncSession, upload: UploadSession) -> UploadedFile | None:
    """The result of a completed session, for a client that asks twice."""
    if upload.document_id is None:
        return None
    document = await session.get(Document, upload.document_id)
    return _document_to_uploaded(document) if document is not None else None


async def complete_session(
    session: AsyncSession,
    *,
    scope: WorkspaceScope,
    batch_id: uuid.UUID,
    upload_id: uuid.UUID,
    password: str | None,
    settings: Settings,
    storage: ObjectStorage,
) -> UploadedFile:
    """Assemble the file and ingest it.

    A file the ingest path rejects - corrupt, encrypted, unsupported - is recorded
    as a FAILED document with its reason, exactly as a direct upload would be; the
    request itself still succeeds. Completing twice returns the first result.
    """
    scope.require(UserRole.OPERATOR)
    upload = await _load_locked(session, scope=scope, batch_id=batch_id, upload_id=upload_id)

    if upload.is_complete:
        existing = await uploaded_file_for(session, upload)
        if existing is not None:
            return existing

    if upload.received_bytes != upload.declared_size or upload.multipart_upload_id is None:
        raise UploadIncompleteError(
            f"Only {upload.received_bytes} of {upload.declared_size} bytes have arrived.",
            remediation=(
                f"Send the remaining chunks from offset {upload.received_bytes}, then retry."
            ),
        )

    batch = await batch_service.get_batch(session, scope=scope, batch_id=batch_id)
    _require_accepting(batch)

    storage.complete_multipart_upload(
        upload.storage_key,
        upload_id=upload.multipart_upload_id,
        parts=[(number, etag) for number, etag, _size in _parts(upload)],
    )
    upload.multipart_upload_id = None

    temp_dir = Path(tempfile.mkdtemp(prefix="certex-assembled-"))
    local_copy = temp_dir / "upload.bin"
    try:
        storage.download_to_path(upload.storage_key, local_copy)
        try:
            outcome = await ingest_local_file(
                session,
                batch=batch,
                workspace_id=scope.workspace_id,
                path=local_copy,
                filename=upload.original_filename,
                source_key=upload.storage_key,
                declared_mime=upload.declared_mime_type,
                password=password,
                settings=settings,
                storage=storage,
            )
            result = outcome.uploaded
        except Exception as exc:  # noqa: BLE001 - recorded as a failed document
            result = await record_failed_upload(
                session,
                batch=batch,
                workspace_id=scope.workspace_id,
                filename=upload.original_filename,
                exc=exc,
                byte_size=upload.declared_size,
            )
    finally:
        local_copy.unlink(missing_ok=True)
        temp_dir.rmdir()
        # The assembled object has been copied to its document key, or rejected.
        # Either way the staging copy must not outlive the request.
        storage.delete(upload.storage_key)

    upload.is_complete = True
    upload.document_id = result.document_id
    await session.flush()

    logger.info(
        "upload.completed",
        batch_id=str(batch.id),
        job_id=str(upload.id),
        document_id=str(result.document_id),
        status=result.status.value,
    )
    return result


async def abort_session(
    session: AsyncSession,
    *,
    scope: WorkspaceScope,
    batch_id: uuid.UUID,
    upload_id: uuid.UUID,
    storage: ObjectStorage,
) -> None:
    scope.require(UserRole.OPERATOR)
    upload = await _load_locked(session, scope=scope, batch_id=batch_id, upload_id=upload_id)
    if upload.multipart_upload_id:
        storage.abort_multipart_upload(upload.storage_key, upload_id=upload.multipart_upload_id)
    await session.delete(upload)
    await session.flush()
    logger.info("upload.aborted", batch_id=str(batch_id), job_id=str(upload_id))


async def purge_expired_sessions(
    session: AsyncSession, *, storage: ObjectStorage, now: dt.datetime | None = None
) -> int:
    """Discard uploads abandoned past their expiry, releasing their stored parts.

    Unfinished parts are invisible in a bucket listing but still hold document
    bytes, so leaving them would let personal data outlive any retention policy.
    """
    cutoff = now or dt.datetime.now(dt.UTC)
    stale = (
        await session.scalars(select(UploadSession).where(UploadSession.expires_at <= cutoff))
    ).all()
    for upload in stale:
        if upload.multipart_upload_id:
            storage.abort_multipart_upload(upload.storage_key, upload_id=upload.multipart_upload_id)
        await session.delete(upload)
    await session.flush()
    if stale:
        logger.info("upload.expired_purged", count=len(stale))
    return len(stale)
