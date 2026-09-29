"""Starting, watching and recording a CSV import.

The API's part is small on purpose: accept the file, write it to storage, record what
was asked for, and queue the work. Reading a hundred thousand rows is the worker's
job, because a request that holds a connection open for twenty minutes is a request
that times out halfway through and leaves an office guessing which rows landed.

The counters on the import row are the interface between the two. The worker updates
them as it goes, so the screen can show progress on a file it will be reading for a
quarter of an hour.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session as SyncSession

from certex.core.deps import WorkspaceScope
from certex.core.errors import ConflictError, NotFoundError
from certex.db.base import utcnow
from certex.db.models import CertificateImport, ImportRowError
from certex.enums import ImportDuplicatePolicy, ImportStatus, UserRole
from certex.logging_setup import get_logger
from certex.schemas.common import Cursor

__all__ = [
    "MAX_ERRORS_RECORDED",
    "ImportCounts",
    "cancel_import",
    "create_import",
    "errors_of",
    "finish_import",
    "get_import",
    "list_errors",
    "list_imports",
    "record_row_error",
    "stale_running_imports",
    "start_import",
    "update_counts",
]

logger = get_logger(__name__)

MAX_ERRORS_RECORDED: Final = 10_000
"""Errors kept per import.

A file that fails ten thousand rows has something wrong with it as a whole - the wrong
columns, the wrong file - and the ten-thousand-and-first message tells nobody anything
the first hundred did not. The count keeps rising after the list stops.
"""


@dataclass(frozen=True, slots=True)
class ImportCounts:
    """What the worker has got through so far."""

    total: int = 0
    created: int = 0
    skipped: int = 0
    failed: int = 0

    @property
    def status(self) -> ImportStatus:
        """Finished with failures is not the same as finished."""
        return ImportStatus.PARTIAL if self.failed or self.skipped else ImportStatus.COMPLETED


# ---------------------------------------------------------------------------
# The API's side
# ---------------------------------------------------------------------------
async def create_import(
    session: AsyncSession,
    *,
    scope: WorkspaceScope,
    import_id: uuid.UUID,
    certificate_type_id: uuid.UUID,
    schema_version_id: uuid.UUID | None,
    original_filename: str,
    storage_key: str,
    byte_size: int,
    sha256: str,
    delimiter: str,
    duplicate_policy: ImportDuplicatePolicy,
) -> CertificateImport:
    """Record an uploaded file as an import waiting to be read.

    The id is the caller's, because the storage key is built from it: the object and
    the row that describes it have to agree on which import they are.
    """
    scope.require(UserRole.OPERATOR)

    record = CertificateImport(
        id=import_id,
        workspace_id=scope.workspace_id,
        certificate_type_id=certificate_type_id,
        schema_version_id=schema_version_id,
        original_filename=original_filename,
        storage_key=storage_key,
        byte_size=byte_size,
        sha256=sha256,
        delimiter=delimiter,
        duplicate_policy=duplicate_policy,
        status=ImportStatus.PENDING,
        created_by=scope.user_id,
    )
    session.add(record)
    await session.flush()
    logger.info(
        "import.created",
        entity_id=str(record.id),
        workspace_id=str(scope.workspace_id),
        byte_size=byte_size,
    )
    return record


async def get_import(
    session: AsyncSession, *, workspace_id: uuid.UUID, import_id: uuid.UUID
) -> CertificateImport:
    record = await session.scalar(
        select(CertificateImport).where(
            CertificateImport.id == import_id,
            CertificateImport.workspace_id == workspace_id,
        )
    )
    if record is None:
        raise NotFoundError("That import does not exist in this workspace.")
    return record


async def list_imports(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    limit: int = 25,
    cursor: Cursor | None = None,
) -> tuple[list[CertificateImport], Cursor | None]:
    """Imports newest first, which is the order an operator wants them."""
    query = select(CertificateImport).where(CertificateImport.workspace_id == workspace_id)
    if cursor is not None:
        query = query.where(
            or_(
                CertificateImport.created_at < cursor.created_at,
                (CertificateImport.created_at == cursor.created_at)
                & (CertificateImport.id < cursor.id),
            )
        )
    rows = list(
        (
            await session.scalars(
                query.order_by(
                    CertificateImport.created_at.desc(), CertificateImport.id.desc()
                ).limit(limit + 1)
            )
        ).all()
    )
    next_cursor: Cursor | None = None
    if len(rows) > limit:
        rows = rows[:limit]
        last = rows[-1]
        next_cursor = Cursor(created_at=last.created_at, id=last.id)
    return rows, next_cursor


async def list_errors(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    import_id: uuid.UUID,
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[ImportRowError], int]:
    """A page of failures in row order, with the total.

    Offset paging, unusually: the error list for one import is fixed once the run has
    finished, and a clerk working through it wants to jump to page four.
    """
    await get_import(session, workspace_id=workspace_id, import_id=import_id)

    total = await session.scalar(
        select(func.count())
        .select_from(ImportRowError)
        .where(ImportRowError.import_id == import_id)
    )
    rows = list(
        (
            await session.scalars(
                select(ImportRowError)
                .where(ImportRowError.import_id == import_id)
                .order_by(ImportRowError.row_number, ImportRowError.id)
                .limit(limit)
                .offset(offset)
            )
        ).all()
    )
    return rows, int(total or 0)


async def cancel_import(
    session: AsyncSession, *, scope: WorkspaceScope, import_id: uuid.UUID
) -> CertificateImport:
    """Ask a queued import not to run.

    Only a pending import can be cancelled here. Once the worker has started, rows are
    already in the register, and pretending otherwise would be worse than letting it
    finish - the operator can see exactly what landed and delete those entries.
    """
    scope.require(UserRole.OPERATOR)
    record = await get_import(session, workspace_id=scope.workspace_id, import_id=import_id)
    if record.status is not ImportStatus.PENDING:
        raise ConflictError(
            f"This import is {record.status.value.lower()} and cannot be cancelled.",
            remediation=(
                "Wait for it to finish. Entries it has already created can be "
                "reviewed and removed individually."
            ),
        )
    record.status = ImportStatus.CANCELLED
    record.finished_at = utcnow()
    await session.flush()
    return record


# ---------------------------------------------------------------------------
# The worker's side
# ---------------------------------------------------------------------------
def start_import(session: SyncSession, import_id: uuid.UUID) -> CertificateImport | None:
    """Claim an import for this worker, or say it is not ours to run.

    Returns None when the row has gone, or has already been started or cancelled -
    which is how a task that Celery delivered twice does nothing the second time.
    """
    record = session.get(CertificateImport, import_id)
    if record is None:
        return None
    if record.status is not ImportStatus.PENDING:
        logger.info("import.already_handled", entity_id=str(import_id), status=record.status.value)
        return None
    record.status = ImportStatus.RUNNING
    record.started_at = utcnow()
    session.flush()
    return record


def update_counts(
    session: SyncSession, record: CertificateImport, counts: ImportCounts, *, encoding: str | None
) -> None:
    """Publish progress so the screen can show it mid-run."""
    record.total_rows = counts.total
    record.created_rows = counts.created
    record.skipped_rows = counts.skipped
    record.failed_rows = counts.failed
    if encoding is not None:
        record.encoding = encoding
    session.flush()


def finish_import(
    session: SyncSession,
    record: CertificateImport,
    counts: ImportCounts,
    *,
    encoding: str | None = None,
    failure: tuple[str, str] | None = None,
) -> None:
    """Close the run: either the counts, or the reason the file was unusable."""
    update_counts(session, record, counts, encoding=encoding)
    if failure is not None:
        record.status = ImportStatus.FAILED
        record.error_code, record.error_message = failure
    else:
        record.status = counts.status
    record.finished_at = utcnow()
    session.flush()
    logger.info(
        "import.finished",
        entity_id=str(record.id),
        status=record.status.value,
        total=counts.total,
        created=counts.created,
        skipped=counts.skipped,
        failed=counts.failed,
    )


def record_row_error(
    session: SyncSession,
    record: CertificateImport,
    *,
    row_number: int,
    code: str,
    message: str,
    field_name: str | None = None,
    certificate_number: str | None = None,
    value_excerpt: str | None = None,
    recorded_so_far: int = 0,
) -> bool:
    """Write one row failure. Returns whether it was kept.

    The value is stored so the message can name it, and is never logged: it is a
    person's data out of somebody's spreadsheet.
    """
    if recorded_so_far >= MAX_ERRORS_RECORDED:
        return False
    session.add(
        ImportRowError(
            import_id=record.id,
            workspace_id=record.workspace_id,
            row_number=row_number,
            code=code,
            message=message,
            field_name=field_name,
            certificate_number=certificate_number[:120] if certificate_number else None,
            value_excerpt=value_excerpt[:200] if value_excerpt else None,
        )
    )
    return True


def errors_of(session: SyncSession, import_id: uuid.UUID) -> Sequence[ImportRowError]:
    """Every recorded failure for one import. Used by the worker's own tests."""
    return list(
        session.scalars(
            select(ImportRowError)
            .where(ImportRowError.import_id == import_id)
            .order_by(ImportRowError.row_number)
        ).all()
    )


def stale_running_imports(session: SyncSession, *, older_than: dt.timedelta) -> list[uuid.UUID]:
    """Imports marked running for longer than any file should take.

    A worker killed mid-file leaves one of these. Reported rather than repaired,
    because the repair - deciding whether to re-run a file that is already half loaded
    - is a person's call.
    """
    cutoff = utcnow() - older_than
    return list(
        session.scalars(
            select(CertificateImport.id).where(
                CertificateImport.status == ImportStatus.RUNNING,
                CertificateImport.started_at < cutoff,
            )
        ).all()
    )
