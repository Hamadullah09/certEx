"""Certificate unit routes: see where the boundaries fell, and correct them."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Query

from certex.core.audit import record_audit
from certex.core.deps import AuditContextDep, SessionDep, WorkspaceScopeDep
from certex.enums import AuditAction
from certex.logging_setup import get_logger
from certex.pipeline.dispatch import TASK_CLASSIFY_UNIT, enqueue
from certex.schemas.common import Cursor, Page
from certex.schemas.units import UnitMergeRequest, UnitSplitRequest, UnitSummary
from certex.services import unit_service

__all__ = ["router"]

logger = get_logger(__name__)
router = APIRouter(prefix="/units", tags=["units"])

CursorParam = Annotated[str | None, Query(description="Opaque cursor from a previous page.")]
LimitParam = Annotated[int, Query(ge=1, le=200, description="Maximum rows to return.")]


@router.get(
    "",
    response_model=Page[UnitSummary],
    summary="List certificates found in a batch or a file",
)
async def list_units(
    session: SessionDep,
    scope: WorkspaceScopeDep,
    batch_id: Annotated[uuid.UUID | None, Query(description="Only this batch.")] = None,
    document_id: Annotated[uuid.UUID | None, Query(description="Only this file.")] = None,
    limit: LimitParam = 50,
    cursor: CursorParam = None,
) -> Page[UnitSummary]:
    rows, next_cursor = await unit_service.list_units(
        session,
        scope=scope,
        batch_id=batch_id,
        document_id=document_id,
        limit=limit,
        cursor=Cursor.decode(cursor) if cursor else None,
    )
    return Page.build(
        [UnitSummary.model_validate(row) for row in rows],
        limit=limit,
        next_cursor=next_cursor,
    )


@router.post(
    "/{unit_id}/split",
    response_model=list[UnitSummary],
    summary="Split one certificate into two at a page",
    responses={
        400: {"description": "That page is not inside this certificate."},
        404: {"description": "No such certificate in this workspace."},
    },
)
async def split_unit(
    unit_id: uuid.UUID,
    payload: UnitSplitRequest,
    session: SessionDep,
    scope: WorkspaceScopeDep,
    audit: AuditContextDep,
) -> list[UnitSummary]:
    """Both halves go back through classification, because their pages have changed."""
    units = await unit_service.split_unit(
        session, scope=scope, unit_id=unit_id, at_page=payload.at_page
    )
    await record_audit(
        session,
        AuditAction.UNIT_SPLIT,
        audit,
        entity_type="certificate_unit",
        entity_id=unit_id,
        # page_number is the audit vocabulary for "which page"; at_page is not.
        metadata={"page_number": payload.at_page, "count": len(units)},
    )
    summaries = [UnitSummary.model_validate(unit) for unit in units]
    await session.commit()

    for summary in summaries:
        enqueue(TASK_CLASSIFY_UNIT, {"unit_id": str(summary.id)})
    return summaries


@router.post(
    "/merge",
    response_model=UnitSummary,
    summary="Merge adjacent certificates into one",
    responses={
        400: {"description": "The certificates are in different files."},
        409: {"description": "The certificates are not next to each other."},
        404: {"description": "No such certificate in this workspace."},
    },
)
async def merge_units(
    payload: UnitMergeRequest,
    session: SessionDep,
    scope: WorkspaceScopeDep,
    audit: AuditContextDep,
) -> UnitSummary:
    unit = await unit_service.merge_units(session, scope=scope, unit_ids=payload.unit_ids)
    await record_audit(
        session,
        AuditAction.UNIT_MERGED,
        audit,
        entity_type="certificate_unit",
        entity_id=unit.id,
        metadata={"count": len(payload.unit_ids)},
    )
    summary = UnitSummary.model_validate(unit)
    await session.commit()

    enqueue(TASK_CLASSIFY_UNIT, {"unit_id": str(summary.id)})
    return summary
