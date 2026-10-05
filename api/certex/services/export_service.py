"""Producing an export, and recording that it happened.

An export is the point of the whole system: everything before it exists so that this file
is right. Three things therefore matter more than they might look.

**The same filters as the screen.** A reviewer who has filtered to "needs review" and
presses download expects that file, not the whole batch. The export takes the same filter
arguments as the rows endpoint, so what you see is what you get.

**A warning when the batch is not finished being reviewed.** Downloading a batch with
rows nobody has looked at is legitimate - an office may want the machine's first pass -
but it should never be a surprise. The count of unreviewed rows is returned with the
export so the screen can say so before the file lands in someone's inbox.

**Every export is recorded.** Who exported which batch, with what filters, how many rows -
because this is the moment certificate details leave the system.
"""

from __future__ import annotations

import datetime as dt
import re
import uuid
from collections.abc import AsyncIterator, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Final

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from certex.core.deps import WorkspaceScope
from certex.db.models import Batch, Export, Extraction
from certex.enums import CertificateType, ExportFormat, ReviewStatus
from certex.export.columns import ColumnPlan, build_column_plan
from certex.export.rows import ExportRow, render_row
from certex.logging_setup import get_logger
from certex.schemas.common import Cursor
from certex.services import row_service
from certex.services.row_service import RowRecord
from certex.services.schema_service import schema_for_batch

__all__ = [
    "ExportFilters",
    "ExportPlan",
    "export_filename",
    "extra_field_names",
    "plan_export",
    "record_export",
    "stream_rows",
]

logger = get_logger(__name__)

_PAGE_SIZE: Final = 200
"""Rows fetched per query while streaming: large enough to be few queries, small enough
that memory stays flat on a batch of thousands."""

_UNSAFE_FILENAME: Final = re.compile(r"[^A-Za-z0-9._-]+")


@dataclass(frozen=True, slots=True)
class ExportFilters:
    """The same narrowing the results screen offers."""

    review_status: ReviewStatus | None = None
    certificate_type: CertificateType | None = None
    flag: str | None = None
    search: str | None = None


@dataclass(slots=True)
class ExportPlan:
    """What an export will contain, decided before a byte is written."""

    batch: Batch
    columns: ColumnPlan
    row_count: int
    unreviewed_count: int
    certificate_types: list[CertificateType] = field(default_factory=list)


async def plan_export(
    session: AsyncSession,
    *,
    scope: WorkspaceScope,
    batch: Batch,
    filters: ExportFilters,
    include_confidence: bool = False,
    include_snippet: bool = False,
    include_extras: bool = False,
) -> ExportPlan:
    """Work out the columns and the counts by looking at what the export will cover.

    The columns cannot be known without looking: a batch of birth certificates should not
    carry a "Cause of death" column, and an office that prints "Blood Group" should not
    lose it.
    """
    del scope  # the batch has already been resolved within the caller's workspace

    base = select(Extraction).where(Extraction.batch_id == batch.id)
    if filters.review_status is not None:
        base = base.where(Extraction.review_status == filters.review_status)
    if filters.certificate_type is not None:
        base = base.where(Extraction.certificate_type == filters.certificate_type)
    if filters.flag:
        base = base.where(Extraction.flags_jsonb.contains([filters.flag]))

    row_count = int(await session.scalar(base.with_only_columns(func.count()).order_by(None)) or 0)
    unreviewed = int(
        await session.scalar(
            base.with_only_columns(func.count())
            .where(Extraction.review_status.in_((ReviewStatus.NEEDS_REVIEW, ReviewStatus.FAILED)))
            .order_by(None)
        )
        or 0
    )
    types = list(
        (
            await session.scalars(base.with_only_columns(Extraction.certificate_type).distinct())
        ).all()
    )
    # The batch's own columns decide the export, in the order its creator put them in.
    # Without this the download falls back to the built-in fields for whichever types
    # the classifier found, which for a batch that defined its own columns means a CSV
    # that does not have them - the one thing a configured batch must never produce.
    # Only a schema the batch actually pinned replaces the type's columns. A batch that
    # pinned none still resolves to a schema - the common fields - and handing *that*
    # over as the whole column plan would drop every birth or death field from the
    # export of a batch that never configured anything.
    configured = batch.schema_version_id is not None
    schemas = [await schema_for_batch(session, batch)] if configured else []

    # Extras are everything else the form printed, under labels the schema has no field
    # for. Worth having when nobody said what to extract; noise when somebody did - a
    # register configured with four columns does not want fourteen more beside them.
    # An office that wants them anyway asks for them.
    extras = (
        await extra_field_names(session, batch_id=batch.id)
        if include_extras or not configured
        else []
    )

    return ExportPlan(
        batch=batch,
        columns=build_column_plan(
            types or [CertificateType.OTHER],
            schemas=schemas,
            extra_field_names=extras,
            include_extras=include_extras or not configured,
            include_confidence=include_confidence,
            include_snippet=include_snippet,
        ),
        row_count=row_count,
        unreviewed_count=unreviewed,
        certificate_types=types,
    )


async def extra_field_names(session: AsyncSession, *, batch_id: uuid.UUID) -> list[str]:
    """Every label this batch carried that the schema has no field for."""
    rows = await session.scalars(
        select(Extraction.extra_fields_jsonb).where(Extraction.batch_id == batch_id)
    )
    names: set[str] = set()
    for extra in rows:
        if isinstance(extra, dict):
            names.update(str(name) for name in extra)
    return sorted(names)


async def stream_rows(
    session: AsyncSession,
    *,
    scope: WorkspaceScope,
    batch_id: uuid.UUID,
    plan: ExportPlan,
    filters: ExportFilters,
) -> AsyncIterator[ExportRow]:
    """Every row of the export, in serial order, a page of rows at a time."""
    cursor: Cursor | None = None
    while True:
        records, cursor, _total = await row_service.list_rows(
            session,
            scope=scope,
            batch_id=batch_id,
            review_status=filters.review_status,
            certificate_type=filters.certificate_type,
            flag=filters.flag,
            search=filters.search,
            limit=_PAGE_SIZE,
            cursor=cursor,
        )
        for record in records:
            yield render_row(record, plan.columns)
        if cursor is None:
            return


def rendered_rows(records: Sequence[RowRecord], plan: ExportPlan) -> list[ExportRow]:
    """Render already-loaded rows, for formats that cannot stream."""
    return [render_row(record, plan.columns) for record in records]


def export_filename(batch: Batch, *, extension: str, moment: dt.datetime | None = None) -> str:
    """``certificates_<batch>_<when>.<ext>`` - sortable, and safe on every filesystem.

    The batch name comes from a person, so it is reduced to characters that cannot
    surprise a shell, a Windows path or a Content-Disposition header.
    """
    stamp = (moment or dt.datetime.now(tz=dt.UTC)).strftime("%Y%m%d-%H%M%S")
    safe = _UNSAFE_FILENAME.sub("-", batch.name).strip("-_.") or "batch"
    return f"certificates_{safe[:60]}_{stamp}.{extension}"


async def record_export(
    session: AsyncSession,
    *,
    scope: WorkspaceScope,
    batch: Batch,
    export_format: ExportFormat,
    plan: ExportPlan,
    filters: ExportFilters,
    byte_size: int = 0,
) -> Export:
    """Write down that this batch left the system, and under what narrowing."""
    export = Export(
        batch_id=batch.id,
        workspace_id=scope.workspace_id,
        format=export_format,
        options_jsonb={
            "review_status": filters.review_status.value if filters.review_status else None,
            "certificate_type": (
                filters.certificate_type.value if filters.certificate_type else None
            ),
            "flag": filters.flag,
            "searched": bool(filters.search),
            "column_count": len(plan.columns),
        },
        row_count=plan.row_count,
        column_count=len(plan.columns),
        byte_size=byte_size,
        created_by=scope.user_id,
    )
    session.add(export)
    await session.flush()
    logger.info(
        "export.produced",
        batch_id=str(batch.id),
        format=export_format.value,
        count=plan.row_count,
        column_count=len(plan.columns),
    )
    return export


def types_in(plan: ExportPlan) -> Iterable[CertificateType]:
    """The certificate types present, for the per-type download."""
    return plan.certificate_types or [CertificateType.OTHER]
