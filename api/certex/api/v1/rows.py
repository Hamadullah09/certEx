"""Review routes: the extracted rows, their corrections, and the pages behind them."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Query, Response, status

from certex.core.audit import record_audit
from certex.core.deps import AuditContextDep, SessionDep, SettingsDep, WorkspaceScopeDep
from certex.enums import AuditAction, CertificateType, ReviewStatus
from certex.logging_setup import get_logger
from certex.pipeline.dispatch import TASK_EXTRACT_UNIT, TASK_LEARN_TEMPLATE, enqueue
from certex.schemas.common import Cursor, Page
from certex.schemas.rows import RowCorrection, RowDetail, RowSummary
from certex.services import batch_service, row_service
from certex.services.page_image_service import page_image
from certex.storage.s3 import get_object_storage

__all__ = ["router"]

logger = get_logger(__name__)
router = APIRouter(tags=["rows"])

CursorParam = Annotated[str | None, Query(description="Opaque cursor from a previous page.")]
LimitParam = Annotated[int, Query(ge=1, le=200, description="Maximum rows to return.")]

_PAGE_IMAGE_CACHE = "private, max-age=3600"
"""Private: a page image is a person's certificate, never a shared cache's business."""


@router.get(
    "/batches/{batch_id}/rows",
    response_model=Page[RowSummary],
    summary="Extracted rows of a batch, in the order they will export",
)
async def list_batch_rows(
    batch_id: uuid.UUID,
    session: SessionDep,
    scope: WorkspaceScopeDep,
    audit: AuditContextDep,
    limit: LimitParam = 50,
    cursor: CursorParam = None,
    review_status: Annotated[ReviewStatus | None, Query(alias="filter[status]")] = None,
    certificate_type: Annotated[CertificateType | None, Query(alias="filter[type]")] = None,
    flag: Annotated[
        str | None, Query(alias="filter[flag]", max_length=64, description="One validation flag.")
    ] = None,
    search: Annotated[
        str | None, Query(max_length=200, description="Match any value or the file name.")
    ] = None,
    include_total: Annotated[bool, Query(description="Also count the matches.")] = False,
) -> Page[RowSummary]:
    # Confirms the batch exists in this workspace before anything else is read.
    await batch_service.get_batch(session, scope=scope, batch_id=batch_id)

    rows, next_cursor, total = await row_service.list_rows(
        session,
        scope=scope,
        batch_id=batch_id,
        review_status=review_status,
        certificate_type=certificate_type,
        flag=flag,
        search=search,
        limit=limit,
        cursor=Cursor.decode(cursor) if cursor else None,
        include_total=include_total,
    )
    await record_audit(
        session,
        AuditAction.ROWS_VIEWED,
        audit,
        entity_type="batch",
        entity_id=batch_id,
        metadata={"count": len(rows)},
    )
    await session.commit()
    return Page.build(
        [row_service.to_summary(row) for row in rows],
        limit=limit,
        next_cursor=next_cursor,
        total=total,
    )


@router.get(
    "/rows/{row_id}",
    response_model=RowDetail,
    summary="One row, with provenance for every field",
    responses={404: {"description": "No such row in this workspace."}},
)
async def get_row(
    row_id: uuid.UUID,
    session: SessionDep,
    scope: WorkspaceScopeDep,
) -> RowDetail:
    record = await row_service.get_row(session, scope=scope, row_id=row_id)
    return row_service.to_detail(record)


@router.patch(
    "/rows/{row_id}",
    response_model=RowDetail,
    summary="Correct a row's values",
    responses={
        400: {"description": "A field name that this certificate type does not have."},
        403: {"description": "Viewers cannot correct rows."},
        404: {"description": "No such row in this workspace."},
    },
)
async def correct_row(
    row_id: uuid.UUID,
    payload: RowCorrection,
    session: SessionDep,
    scope: WorkspaceScopeDep,
    audit: AuditContextDep,
    settings: SettingsDep,
) -> RowDetail:
    """Apply edits, keep them, and re-check the row against them.

    The audit entry records which fields changed and never their values: an audit log
    that copies certificate details is another place they can leak from.
    """
    record = await row_service.correct_row(
        session, scope=scope, row_id=row_id, correction=payload, settings=settings
    )
    await record_audit(
        session,
        AuditAction.EXTRACTION_CORRECTED,
        audit,
        entity_type="extraction",
        entity_id=row_id,
        metadata={"field_names": sorted(payload.fields), "count": len(payload.fields)},
    )
    if payload.approve:
        await record_audit(
            session,
            AuditAction.EXTRACTION_APPROVED,
            audit,
            entity_type="extraction",
            entity_id=row_id,
        )
    unit_id = record.unit.id
    await session.commit()
    await session.refresh(record.extraction)

    # After the commit, never inside it. Learning reads the row it is learning from, so
    # a task that started while the correction was still uncommitted would read the old
    # values - and it is best-effort anyway: the certificate in front of the reviewer is
    # already correct whether or not the form is ever learned.
    if payload.fields:
        enqueue(TASK_LEARN_TEMPLATE, {"unit_id": str(unit_id)})
    return row_service.to_detail(record)


@router.post(
    "/rows/{row_id}/reprocess",
    response_model=RowDetail,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Read this certificate again",
    responses={403: {"description": "Viewers cannot reprocess rows."}},
)
async def reprocess_row(
    row_id: uuid.UUID,
    session: SessionDep,
    scope: WorkspaceScopeDep,
    audit: AuditContextDep,
) -> RowDetail:
    """Re-run extraction for one certificate, keeping the values a person typed."""
    record = await row_service.mark_reprocessing(session, scope=scope, row_id=row_id)
    await record_audit(
        session,
        AuditAction.EXTRACTION_REPROCESSED,
        audit,
        entity_type="extraction",
        entity_id=row_id,
    )
    detail = row_service.to_detail(record)
    unit_id = record.unit.id
    await session.commit()

    enqueue(TASK_EXTRACT_UNIT, {"unit_id": str(unit_id)})
    return detail


@router.get(
    "/documents/{document_id}/pages/{page_number}/image",
    response_class=Response,
    summary="The page a value was read from",
    responses={
        200: {"content": {"image/jpeg": {}}, "description": "The page as a JPEG."},
        404: {"description": "No such file, or no such page in it."},
        415: {"description": "This kind of file has no page image."},
    },
)
async def get_page_image(
    document_id: uuid.UUID,
    page_number: Annotated[int, Query(ge=1)] | int,
    session: SessionDep,
    scope: WorkspaceScopeDep,
    audit: AuditContextDep,
    settings: SettingsDep,
) -> Response:
    """Serve the page image, rendering it on first request when it has never been drawn."""
    document = await batch_service.get_document(session, scope=scope, document_id=document_id)
    image = page_image(
        workspace_id=scope.workspace_id,
        document_id=document_id,
        page_number=int(page_number),
        storage_key=document.storage_key,
        mime_type=document.mime_type,
        storage=get_object_storage(),
        settings=settings,
    )
    await record_audit(
        session,
        AuditAction.DOCUMENT_PAGE_VIEWED,
        audit,
        entity_type="document",
        entity_id=document_id,
        metadata={"page_number": int(page_number)},
    )
    await session.commit()
    return Response(
        content=image.payload,
        media_type="image/jpeg",
        headers={"Cache-Control": _PAGE_IMAGE_CACHE},
    )
