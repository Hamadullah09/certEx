"""Reading the templates a workspace has learned.

Templates are written by the extraction stage, never by a person, so this is a
read-only view of what the pipeline taught itself. The ordering is the point: the
template that has been applied to the most certificates is the one an office cares
about, because that is the form its clerks actually hold.
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from certex.core.deps import WorkspaceScope
from certex.db.models import Template
from certex.enums import CertificateType
from certex.schemas.common import Cursor

__all__ = ["count_templates", "list_templates"]


async def list_templates(
    session: AsyncSession,
    *,
    scope: WorkspaceScope,
    certificate_type: CertificateType | None = None,
    active_only: bool = False,
    limit: int = 50,
    cursor: Cursor | None = None,
) -> tuple[list[Template], Cursor | None]:
    """Newest first, so a template learned this morning is at the top.

    Paged by keyset over (created_at, id) like every other list in the API. Two
    templates learned in the same transaction share a timestamp, so the id breaks the
    tie and no row falls through a page boundary.
    """
    query = select(Template).where(Template.workspace_id == scope.workspace_id)
    if certificate_type is not None:
        query = query.where(Template.certificate_type == certificate_type)
    if active_only:
        query = query.where(Template.is_active.is_(True))
    if cursor is not None:
        query = query.where(
            or_(
                Template.created_at < cursor.created_at,
                (Template.created_at == cursor.created_at) & (Template.id < cursor.id),
            )
        )

    rows = list(
        (
            await session.scalars(
                query.order_by(Template.created_at.desc(), Template.id.desc()).limit(limit + 1)
            )
        ).all()
    )
    next_cursor: Cursor | None = None
    if len(rows) > limit:
        rows = rows[:limit]
        last = rows[-1]
        next_cursor = Cursor(created_at=last.created_at, id=last.id)
    return rows, next_cursor


async def count_templates(
    session: AsyncSession,
    *,
    scope: WorkspaceScope,
    certificate_type: CertificateType | None = None,
    active_only: bool = False,
) -> int:
    """Counted only when asked for. A workspace holds one template per form layout,
    so this is tens of rows rather than millions - but the rule across the API is that
    nobody pays for a count they did not request, and this follows it."""
    query = (
        select(func.count())
        .select_from(Template)
        .where(Template.workspace_id == scope.workspace_id)
    )
    if certificate_type is not None:
        query = query.where(Template.certificate_type == certificate_type)
    if active_only:
        query = query.where(Template.is_active.is_(True))
    return int(await session.scalar(query) or 0)


async def get_template(
    session: AsyncSession, *, scope: WorkspaceScope, template_id: uuid.UUID
) -> Template | None:
    """One template, or nothing if it belongs to another office."""
    template: Template | None = await session.scalar(
        select(Template).where(
            Template.id == template_id, Template.workspace_id == scope.workspace_id
        )
    )
    return template
