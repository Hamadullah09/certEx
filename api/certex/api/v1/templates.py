"""Template routes: what the extraction pipeline has learned about an office's forms.

Read-only on purpose. A template is earned - it exists because somebody corrected the
same printed form enough times for the pipeline to be sure where each value sits - and
letting a route hand-write one would put rules into the extractor that no correction
ever justified. Switching a template off is the one change an administrator can make,
and that is a separate decision from editing it.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Query

from certex.core.deps import SessionDep, WorkspaceScopeDep
from certex.core.errors import NotFoundError
from certex.enums import CertificateType
from certex.schemas.common import Cursor, Page
from certex.schemas.templates import TemplateSummary
from certex.services import template_service

__all__ = ["router"]

router = APIRouter(prefix="/templates", tags=["templates"])

CursorParam = Annotated[str | None, Query(description="Opaque cursor from a previous page.")]
LimitParam = Annotated[int, Query(ge=1, le=200, description="Maximum rows to return.")]


@router.get(
    "",
    response_model=Page[TemplateSummary],
    summary="List the forms the app has learned to read",
)
async def list_templates(
    session: SessionDep,
    scope: WorkspaceScopeDep,
    certificate_type: Annotated[
        CertificateType | None, Query(description="Only templates for this kind of certificate.")
    ] = None,
    active_only: Annotated[
        bool, Query(description="Leave out templates that have been switched off.")
    ] = False,
    include_total: Annotated[
        bool, Query(description="Also count every match, not just this page.")
    ] = False,
    limit: LimitParam = 50,
    cursor: CursorParam = None,
) -> Page[TemplateSummary]:
    rows, next_cursor = await template_service.list_templates(
        session,
        scope=scope,
        certificate_type=certificate_type,
        active_only=active_only,
        limit=limit,
        cursor=Cursor.decode(cursor) if cursor else None,
    )
    total = (
        await template_service.count_templates(
            session, scope=scope, certificate_type=certificate_type, active_only=active_only
        )
        if include_total
        else None
    )
    return Page.build(
        [TemplateSummary.of(row) for row in rows],
        limit=limit,
        next_cursor=next_cursor,
        total=total,
    )


@router.get(
    "/{template_id}",
    response_model=TemplateSummary,
    summary="One learned form",
    responses={404: {"description": "No such template in this workspace."}},
)
async def get_template(
    session: SessionDep, scope: WorkspaceScopeDep, template_id: uuid.UUID
) -> TemplateSummary:
    template = await template_service.get_template(session, scope=scope, template_id=template_id)
    if template is None:
        raise NotFoundError(
            "No template with that id exists in this workspace.",
            remediation="Open the templates list to see what has been learned.",
        )
    return TemplateSummary.of(template)
