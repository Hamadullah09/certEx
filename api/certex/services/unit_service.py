"""Listing certificate units, and correcting where the pipeline put their boundaries.

Splitting and merging are the reviewer's answer to the two ways boundary detection can
be wrong. Both rebuild the affected units and send them back through classification,
because everything downstream - the type, the fields, the confidence - was derived from
a page range that has just changed. Leaving the old extraction attached to a new range
would show a row whose values come from pages it no longer covers.

Both operations mark the units they touch as MANUAL boundaries with full confidence: a
person looked at the pages and said so, which is better evidence than any heuristic.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from itertools import pairwise

from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from certex.core.deps import WorkspaceScope
from certex.core.errors import BadRequestError, ConflictError, NotFoundError
from certex.db.models import CertificateUnit, Document, Extraction
from certex.enums import BoundaryMethod, CertificateType, ClassificationMethod, UnitStatus, UserRole
from certex.logging_setup import get_logger
from certex.schemas.common import Cursor

__all__ = ["get_unit", "list_units", "merge_units", "split_unit"]

logger = get_logger(__name__)


async def _owned_unit(
    session: AsyncSession, *, scope: WorkspaceScope, unit_id: uuid.UUID
) -> CertificateUnit:
    unit = await session.scalar(
        select(CertificateUnit)
        .join(Document, Document.id == CertificateUnit.document_id)
        .where(CertificateUnit.id == unit_id, Document.workspace_id == scope.workspace_id)
        .with_for_update(of=CertificateUnit)
    )
    if unit is None:
        raise NotFoundError(
            "No such certificate in this workspace.",
            remediation="Refresh the results to see what still exists.",
        )
    return unit


async def get_unit(
    session: AsyncSession, *, scope: WorkspaceScope, unit_id: uuid.UUID
) -> CertificateUnit:
    return await _owned_unit(session, scope=scope, unit_id=unit_id)


async def list_units(
    session: AsyncSession,
    *,
    scope: WorkspaceScope,
    batch_id: uuid.UUID | None = None,
    document_id: uuid.UUID | None = None,
    limit: int = 50,
    cursor: Cursor | None = None,
) -> tuple[list[CertificateUnit], Cursor | None]:
    """Units in creation order, which is document order then page order."""
    query = (
        select(CertificateUnit)
        .join(Document, Document.id == CertificateUnit.document_id)
        .where(Document.workspace_id == scope.workspace_id)
    )
    if batch_id is not None:
        query = query.where(CertificateUnit.batch_id == batch_id)
    if document_id is not None:
        query = query.where(CertificateUnit.document_id == document_id)
    if cursor is not None:
        # Keyset over (created_at, id): a bulk insert can share a microsecond, and a
        # timestamp alone would drop rows at a page boundary.
        query = query.where(
            or_(
                CertificateUnit.created_at > cursor.created_at,
                (CertificateUnit.created_at == cursor.created_at)
                & (CertificateUnit.id > cursor.id),
            )
        )

    rows = list(
        (
            await session.scalars(
                query.order_by(CertificateUnit.created_at, CertificateUnit.id).limit(limit + 1)
            )
        ).all()
    )
    next_cursor: Cursor | None = None
    if len(rows) > limit:
        rows = rows[:limit]
        last = rows[-1]
        next_cursor = Cursor(created_at=last.created_at, id=last.id)
    return rows, next_cursor


async def _renumber(session: AsyncSession, document_id: uuid.UUID) -> None:
    """Make ordinals match page order again, so rows export in the order they print."""
    units = list(
        (
            await session.scalars(
                select(CertificateUnit)
                .where(CertificateUnit.document_id == document_id)
                .order_by(CertificateUnit.page_start, CertificateUnit.page_end)
            )
        ).all()
    )
    for ordinal, unit in enumerate(units):
        unit.ordinal = ordinal


async def _reset_for_reprocessing(session: AsyncSession, units: Sequence[CertificateUnit]) -> None:
    """Drop what was derived from the old page range, and send the unit back."""
    unit_ids = [unit.id for unit in units]
    await session.execute(delete(Extraction).where(Extraction.unit_id.in_(unit_ids)))
    for unit in units:
        unit.boundary_method = BoundaryMethod.MANUAL
        unit.boundary_confidence = 1.0
        unit.certificate_type = CertificateType.OTHER
        unit.type_confidence = 0.0
        unit.classification_method = ClassificationMethod.DEFAULT
        unit.status = UnitStatus.TEXT_READY
        unit.error_code = None
        unit.error_message = None


async def split_unit(
    session: AsyncSession, *, scope: WorkspaceScope, unit_id: uuid.UUID, at_page: int
) -> list[CertificateUnit]:
    """Split a unit so that ``at_page`` starts a new certificate. Returns both units."""
    scope.require(UserRole.OPERATOR)
    unit = await _owned_unit(session, scope=scope, unit_id=unit_id)

    if not unit.page_start < at_page <= unit.page_end:
        raise BadRequestError(
            f"This certificate covers pages {unit.page_start} to {unit.page_end}, so it "
            f"cannot be split at page {at_page}.",
            title="Cannot split there",
            remediation=(
                f"Choose a page from {unit.page_start + 1} to {unit.page_end} - the first "
                "page of the second certificate."
            ),
        )

    tail = CertificateUnit(
        document_id=unit.document_id,
        batch_id=unit.batch_id,
        ordinal=unit.ordinal + 1,
        page_start=at_page,
        page_end=unit.page_end,
    )
    unit.page_end = at_page - 1
    session.add(tail)
    await session.flush()

    await _reset_for_reprocessing(session, [unit, tail])
    await _renumber(session, unit.document_id)
    await session.flush()

    logger.info(
        "units.split", unit_id=str(unit.id), document_id=str(unit.document_id), page=at_page
    )
    return [unit, tail]


async def merge_units(
    session: AsyncSession, *, scope: WorkspaceScope, unit_ids: Sequence[uuid.UUID]
) -> CertificateUnit:
    """Merge adjacent units of one document into the first of them."""
    scope.require(UserRole.OPERATOR)
    units = [await _owned_unit(session, scope=scope, unit_id=unit_id) for unit_id in unit_ids]
    units.sort(key=lambda unit: (unit.page_start, unit.page_end))

    documents = {unit.document_id for unit in units}
    if len(documents) != 1:
        raise BadRequestError(
            "These certificates are in different files and cannot be merged.",
            title="Cannot merge across files",
            remediation="Select certificates from one file at a time.",
        )
    for first, second in pairwise(units):
        if second.page_start != first.page_end + 1:
            raise ConflictError(
                "These certificates are not next to each other.",
                title="Cannot merge a gap",
                remediation=(
                    "Select certificates that follow one another, with no others in between."
                ),
            )

    survivor, *absorbed = units
    survivor.page_end = units[-1].page_end
    for unit in absorbed:
        await session.delete(unit)
    await session.flush()

    await _reset_for_reprocessing(session, [survivor])
    await _renumber(session, survivor.document_id)
    await session.flush()

    logger.info(
        "units.merged",
        unit_id=str(survivor.id),
        document_id=str(survivor.document_id),
        count=len(units),
    )
    return survivor
