"""Workspace settings.

Two routes, because the numbers on them decide how much work the office does: at what
confidence a reading is accepted without a person, and below what confidence it is
treated as unread rather than doubtful. Reading them is open to any member - an
operator needs to know why a row was routed the way it was - and changing them is an
administrator's, because it changes what every reading after it means.
"""

from __future__ import annotations

from fastapi import APIRouter

from certex.core.audit import record_audit
from certex.core.deps import AuditContextDep, SessionDep, WorkspaceScopeDep
from certex.enums import AuditAction
from certex.logging_setup import get_logger
from certex.schemas.workspace import (
    WorkspaceProfile,
    WorkspaceRename,
    WorkspaceSettings,
    WorkspaceSettingsUpdate,
)
from certex.services import workspace_service

__all__ = ["router"]

logger = get_logger(__name__)
router = APIRouter(prefix="/workspace", tags=["workspace"])


@router.get(
    "/settings",
    response_model=WorkspaceSettings,
    summary="This office's settings",
)
async def get_settings(session: SessionDep, scope: WorkspaceScopeDep) -> WorkspaceSettings:
    return await workspace_service.read_settings(session, workspace_id=scope.workspace_id)


@router.put(
    "/settings",
    response_model=WorkspaceSettings,
    summary="Change this office's settings",
    responses={
        403: {"description": "Only an administrator may change settings."},
        422: {"description": "The thresholds contradict each other."},
    },
)
async def put_settings(
    payload: WorkspaceSettingsUpdate,
    session: SessionDep,
    scope: WorkspaceScopeDep,
    audit: AuditContextDep,
) -> WorkspaceSettings:
    """A partial update: a section the caller leaves out is left as it was.

    Two administrators with this page open would otherwise overwrite each other's
    sections without either of them having touched the other's fields.
    """
    settings = await workspace_service.update_settings(session, scope=scope, update=payload)
    await record_audit(
        session,
        AuditAction.SETTINGS_UPDATED,
        audit,
        entity_type="workspace",
        entity_id=scope.workspace_id,
    )
    await session.commit()
    return settings


@router.put(
    "/name",
    response_model=WorkspaceProfile,
    summary="Rename this office",
    responses={403: {"description": "Only an administrator may rename the office."}},
)
async def rename(
    payload: WorkspaceRename,
    session: SessionDep,
    scope: WorkspaceScopeDep,
    audit: AuditContextDep,
) -> WorkspaceProfile:
    """The name shows on every screen and in every exported filename.

    A deployment starts with whatever name the seeding script was given, which unless
    somebody set it is "Demo Records Office" - so this is how a real office stops
    introducing itself as a demo.
    """
    workspace = await workspace_service.rename_workspace(session, scope=scope, name=payload.name)
    await record_audit(
        session,
        AuditAction.SETTINGS_UPDATED,
        audit,
        entity_type="workspace",
        entity_id=scope.workspace_id,
    )
    await session.commit()
    return WorkspaceProfile.model_validate(workspace)
