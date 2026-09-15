"""Stage 1 - Ingest.

Takes an uploaded byte stream and turns it into one or more ``Document`` rows,
with the bytes safely in object storage.

Memory discipline
-----------------
Nothing here holds a whole file. The incoming stream is spooled to a temporary
file while the digest is computed, then handed to boto3, which streams it in
parts. A 500 MB scan costs one temp file and one part-sized buffer, never 500 MB
of RSS. That is what makes a 2,000-file batch survivable.

Order of operations, and why
----------------------------
1. **Sanitise the filename** - before it touches a database column or a log line.
2. **Spool and hash in one pass** - reading twice would double the I/O on the
   largest files in the system.
3. **Sniff the type from content** - the extension is not evidence.
4. **Deduplicate on the digest** - a records office re-uploads the same folder
   constantly. A duplicate skips both the storage write and the entire pipeline,
   and reuses the prior extraction.
5. **Probe structure** - so "password protected" is reported now, by name, and
   not as a mysterious empty extraction later.
6. **Store, then record** - the ``Document`` row is only written once the bytes
   are durable, so a row can never point at an object that does not exist.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import tempfile
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import IO, cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from certex.config import Settings, get_settings
from certex.core.errors import AppError, CorruptDocumentError, PayloadTooLargeError
from certex.db.models import Batch, Document
from certex.enums import DocumentStatus
from certex.logging_setup import get_logger
from certex.pipeline.filetypes import (
    SNIFF_BYTES,
    DetectedType,
    FileKind,
    detect_file_kind,
    require_supported,
)
from certex.pipeline.pdfprobe import probe_pdf, raise_for_probe
from certex.pipeline.safety import (
    VirusDetectedError,
    inspect_archive,
    sanitise_filename,
    scan_for_viruses,
)
from certex.schemas.batches import UploadedFile
from certex.storage.s3 import ObjectStorage, StorageKeys, get_object_storage

__all__ = [
    "IngestOutcome",
    "SpooledUpload",
    "ingest_stream",
    "spool_and_hash",
]

logger = get_logger(__name__)

_READ_CHUNK = 1024 * 1024
# A ZIP is read into memory only to inspect its central directory; that is bounded
# by the archive's own index, not by the size of what it contains.
_MAX_ARCHIVE_INSPECT_BYTES = 64 * 1024 * 1024


@dataclass(slots=True)
class SpooledUpload:
    """An upload that has been written to a temporary file and digested."""

    handle: IO[bytes]
    path: Path | None
    size: int
    sha256: str
    head: bytes
    """First bytes, kept for content sniffing without re-reading the file."""

    def close(self) -> None:
        try:
            self.handle.close()
        finally:
            if self.path is not None and self.path.exists():
                self.path.unlink(missing_ok=True)


@dataclass(frozen=True, slots=True)
class IngestOutcome:
    document: Document
    uploaded: UploadedFile


def spool_and_hash(
    stream: IO[bytes],
    *,
    max_bytes: int,
    remaining_batch_bytes: int | None = None,
) -> SpooledUpload:
    """Copy a stream to a temporary file, computing SHA-256 as it goes.

    Enforces both limits *while* copying rather than trusting a declared length,
    so a client that lies about Content-Length cannot fill the disk.
    """
    digest = hashlib.sha256()
    spool = tempfile.NamedTemporaryFile(  # noqa: SIM115 - closed via SpooledUpload
        prefix="certex-upload-", suffix=".bin", delete=False
    )
    path = Path(spool.name)
    size = 0
    head = b""

    try:
        while True:
            chunk = stream.read(_READ_CHUNK)
            if not chunk:
                break

            size += len(chunk)
            if size > max_bytes:
                raise PayloadTooLargeError(
                    f"The file exceeds the {max_bytes // (1024 * 1024)} MB limit.",
                    remediation=(
                        "Split the document, or raise MAX_FILE_BYTES for this deployment."
                    ),
                )
            if remaining_batch_bytes is not None and size > remaining_batch_bytes:
                raise PayloadTooLargeError(
                    "This file would take the batch over its total size limit.",
                    title="Batch size limit reached",
                    remediation=(
                        "Start a second batch for the remaining files, or raise MAX_BATCH_BYTES."
                    ),
                )

            digest.update(chunk)
            if len(head) < SNIFF_BYTES:
                head += chunk[: SNIFF_BYTES - len(head)]
            spool.write(chunk)

        spool.flush()
        spool.seek(0)
    except Exception:
        spool.close()
        path.unlink(missing_ok=True)
        raise

    return SpooledUpload(
        # NamedTemporaryFile returns a wrapper that implements the binary file
        # protocol but is not declared as IO[bytes].
        handle=cast("IO[bytes]", spool),
        path=path,
        size=size,
        sha256=digest.hexdigest(),
        head=head,
    )


async def _find_duplicate(
    session: AsyncSession, *, workspace_id: uuid.UUID, sha256: str
) -> Document | None:
    """Find a previously ingested document with identical bytes.

    Scoped to the workspace: identical files in two tenants are two documents, and
    surfacing one across the boundary would leak the existence of the other.
    Chains are collapsed so a duplicate always points at the original.
    """
    existing = await session.scalar(
        select(Document)
        .where(
            Document.workspace_id == workspace_id,
            Document.sha256 == sha256,
            Document.status != DocumentStatus.FAILED,
        )
        .order_by(Document.created_at)
        .limit(1)
    )
    if existing is None:
        return None
    if existing.is_duplicate_of is not None:
        original = await session.get(Document, existing.is_duplicate_of)
        return original or existing
    return existing


def _probe_structure(
    spooled: SpooledUpload,
    detected: DetectedType,
    *,
    password: str | None,
) -> tuple[int | None, bool]:
    """Return ``(page_count, was_encrypted)``, raising for unreadable documents.

    One probe, not two: parsing a large PDF's page tree twice to answer two
    questions would double the cost of the most common file type in the system.
    """
    if detected.kind is not FileKind.PDF:
        return None, False

    spooled.handle.seek(0)
    probe = probe_pdf(spooled.handle, password=password)
    spooled.handle.seek(0)
    raise_for_probe(probe)
    return probe.page_count, probe.is_encrypted


async def ingest_stream(
    session: AsyncSession,
    *,
    batch: Batch,
    workspace_id: uuid.UUID,
    stream: IO[bytes],
    filename: str,
    declared_mime: str | None = None,
    settings: Settings | None = None,
    storage: ObjectStorage | None = None,
    parent: Document | None = None,
    archive_member_path: str | None = None,
    archive_depth: int = 0,
) -> IngestOutcome:
    """Ingest one uploaded stream into the batch.

    Returns the created ``Document`` and a client-facing summary. The row is added
    to the session but not committed; the caller owns the transaction so that a
    document, its batch counters and its audit entry commit together.
    """
    active = settings or get_settings()
    store = storage or get_object_storage()
    safe_name = sanitise_filename(filename)

    remaining = max(active.max_batch_bytes - batch.total_bytes, 0)
    spooled = spool_and_hash(
        stream,
        max_bytes=active.max_file_bytes,
        remaining_batch_bytes=remaining,
    )

    try:
        # The upload is already on disk, so ZIP-family types get the authoritative
        # check against the real archive index rather than the sniffed prefix.
        detected = detect_file_kind(
            spooled.head,
            filename=safe_name,
            path=str(spooled.path) if spooled.path else None,
        )
        require_supported(detected)

        if spooled.size == 0:
            raise CorruptDocumentError(
                "The file is empty.",
                title="Empty file",
                remediation="Check the export and upload a file with contents.",
            )

        # -- deduplicate before spending a storage write ----------------------
        duplicate = await _find_duplicate(session, workspace_id=workspace_id, sha256=spooled.sha256)
        if duplicate is not None:
            document = Document(
                batch_id=batch.id,
                workspace_id=workspace_id,
                original_filename=safe_name,
                mime_type=detected.mime,
                declared_mime_type=declared_mime,
                byte_size=spooled.size,
                sha256=spooled.sha256,
                storage_key=duplicate.storage_key,
                page_count=duplicate.page_count,
                status=DocumentStatus.DUPLICATE,
                is_duplicate_of=duplicate.id,
                parent_document_id=parent.id if parent else None,
                archive_depth=archive_depth,
                archive_member_path=archive_member_path,
                is_encrypted=duplicate.is_encrypted,
            )
            session.add(document)
            await session.flush()

            batch.file_count += 1
            batch.duplicate_count += 1
            batch.total_bytes += spooled.size

            logger.info(
                "ingest.deduplicated",
                document_id=str(document.id),
                batch_id=str(batch.id),
                sha256=spooled.sha256,
                byte_size=spooled.size,
            )
            return IngestOutcome(
                document=document,
                uploaded=UploadedFile(
                    document_id=document.id,
                    original_filename=safe_name,
                    byte_size=spooled.size,
                    sha256=spooled.sha256,
                    mime_type=detected.mime,
                    status=DocumentStatus.DUPLICATE,
                    is_duplicate=True,
                    duplicate_of=duplicate.id,
                    extracted_from_archive=parent is not None,
                ),
            )

        # -- optional virus scan ----------------------------------------------
        if active.clamav_enabled and spooled.size <= _MAX_ARCHIVE_INSPECT_BYTES:
            spooled.handle.seek(0)
            signature = scan_for_viruses(spooled.handle.read(), settings=active)
            spooled.handle.seek(0)
            if signature:
                raise VirusDetectedError(
                    f"The file was rejected by the virus scanner ({signature}).",
                )

        # -- structural probe --------------------------------------------------
        batch_settings = batch.settings_json or {}
        password = batch_settings.get("password")
        page_count, was_encrypted = _probe_structure(
            spooled, detected, password=password if isinstance(password, str) else None
        )

        # -- store, then record ------------------------------------------------
        document_id = uuid.uuid4()
        now = dt.datetime.now(dt.UTC)
        key = StorageKeys.document(workspace_id, document_id, year=now.year, month=now.month)

        store.upload_stream(
            key,
            spooled.handle,
            content_type=detected.mime,
            # Metadata carries ids only. A filename here would put a person's name
            # into object-storage metadata and every access log that touches it.
            metadata={"document-id": str(document_id), "workspace-id": str(workspace_id)},
        )

        is_archive = detected.kind is FileKind.ZIP
        document = Document(
            id=document_id,
            batch_id=batch.id,
            workspace_id=workspace_id,
            original_filename=safe_name,
            mime_type=detected.mime,
            declared_mime_type=declared_mime,
            byte_size=spooled.size,
            sha256=spooled.sha256,
            storage_key=key,
            page_count=page_count,
            # An archive is a container: its members are processed, it is not.
            status=DocumentStatus.SKIPPED if is_archive else DocumentStatus.QUEUED,
            parent_document_id=parent.id if parent else None,
            archive_depth=archive_depth,
            archive_member_path=archive_member_path,
            is_encrypted=was_encrypted,
        )
        session.add(document)
        await session.flush()

        batch.file_count += 1
        batch.total_bytes += spooled.size

        logger.info(
            "ingest.stored",
            document_id=str(document.id),
            batch_id=str(batch.id),
            mime_type=detected.mime,
            byte_size=spooled.size,
            page_count=page_count,
            sha256=spooled.sha256,
        )

        uploaded = UploadedFile(
            document_id=document.id,
            original_filename=safe_name,
            byte_size=spooled.size,
            sha256=spooled.sha256,
            mime_type=detected.mime,
            status=document.status,
            extracted_from_archive=parent is not None,
        )

        # -- expand archives ---------------------------------------------------
        if is_archive:
            children = await _expand_archive(
                session,
                batch=batch,
                workspace_id=workspace_id,
                spooled=spooled,
                parent=document,
                depth=archive_depth,
                settings=active,
                storage=store,
            )
            uploaded = uploaded.model_copy(update={"children": children})

        return IngestOutcome(document=document, uploaded=uploaded)
    finally:
        spooled.close()


async def _expand_archive(
    session: AsyncSession,
    *,
    batch: Batch,
    workspace_id: uuid.UUID,
    spooled: SpooledUpload,
    parent: Document,
    depth: int,
    settings: Settings,
    storage: ObjectStorage,
) -> list[UploadedFile]:
    """Ingest each supported member of an archive as its own document.

    The archive is inspected from its central directory first, so a bomb is
    rejected before a single byte is extracted. Members are then streamed out one
    at a time - never all at once - and each is ingested through the same path as
    a directly uploaded file, which is what makes nested archives, dedup and
    per-member type detection work uniformly.
    """
    if spooled.path is None:  # pragma: no cover - always spooled to disk
        return []

    inspect_archive(str(spooled.path), depth=depth, settings=settings)

    children: list[UploadedFile] = []
    with zipfile.ZipFile(spooled.path) as archive:
        for info in archive.infolist():
            if info.is_dir():
                continue

            member_name = sanitise_filename(Path(info.filename.replace("\\", "/")).name)
            if member_name.startswith("__MACOSX") or member_name in (".DS_Store", "Thumbs.db"):
                continue

            if len(children) + batch.file_count >= settings.max_batch_files:
                logger.warning(
                    "ingest.archive_truncated",
                    batch_id=str(batch.id),
                    count=len(children),
                    reason_code="max_batch_files",
                )
                break

            try:
                with archive.open(info) as member_stream:
                    outcome = await ingest_stream(
                        session,
                        batch=batch,
                        workspace_id=workspace_id,
                        stream=member_stream,
                        filename=member_name,
                        settings=settings,
                        storage=storage,
                        parent=parent,
                        archive_member_path=info.filename[:1024],
                        archive_depth=depth + 1,
                    )
                children.append(outcome.uploaded)
            except Exception as exc:  # noqa: BLE001 - one bad member must not
                # take the archive down with it; record it and carry on.
                code = exc.code.value if isinstance(exc, AppError) else "ingest_failed"
                logger.warning(
                    "ingest.archive_member_failed",
                    batch_id=str(batch.id),
                    document_id=str(parent.id),
                    error_code=code,
                )
                failed = Document(
                    batch_id=batch.id,
                    workspace_id=workspace_id,
                    original_filename=member_name,
                    mime_type="application/octet-stream",
                    byte_size=info.file_size,
                    sha256="",
                    storage_key="",
                    status=DocumentStatus.FAILED,
                    error_code=code,
                    error_message=str(exc)[:1000],
                    parent_document_id=parent.id,
                    archive_depth=depth + 1,
                    archive_member_path=info.filename[:1024],
                )
                session.add(failed)
                await session.flush()
                batch.file_count += 1
                batch.failed_count += 1

    logger.info(
        "ingest.archive_expanded",
        document_id=str(parent.id),
        batch_id=str(batch.id),
        count=len(children),
    )
    return children
