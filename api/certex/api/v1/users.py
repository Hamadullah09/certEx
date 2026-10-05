"""The people in a workspace: who they are, what they may do, and their passwords.

Reading the list is open to any signed-in member - a reviewer looking at "approved by"
needs to be able to put a name to it. Everything that changes an account is
administrator-only, and every one of those changes is audited, because who was given
which role is exactly what an auditor asks about after something goes wrong.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, status

from certex.core.audit import record_audit
from certex.core.deps import AuditContextDep, SessionDep, WorkspaceScopeDep
from certex.enums import AuditAction
from certex.schemas.users import (
    PasswordChange,
    PasswordSet,
    UserCreate,
    UserSummary,
    UserUpdate,
)
from certex.services import user_service

__all__ = ["router"]

router = APIRouter(prefix="/users", tags=["users"])


@router.get("", response_model=list[UserSummary], summary="Everyone in this office")
async def list_users(session: SessionDep, scope: WorkspaceScopeDep) -> list[UserSummary]:
    people = await user_service.list_users(session, scope=scope)
    return [UserSummary.model_validate(person) for person in people]


@router.post(
    "",
    response_model=UserSummary,
    status_code=status.HTTP_201_CREATED,
    summary="Add somebody to this office",
    responses={
        403: {"description": "Only administrators may add people."},
        409: {"description": "That email address is already in use."},
    },
)
async def create_user(
    payload: UserCreate,
    session: SessionDep,
    scope: WorkspaceScopeDep,
    audit: AuditContextDep,
) -> UserSummary:
    user = await user_service.create_user(
        session,
        scope=scope,
        email=payload.email,
        password=payload.password,
        full_name=payload.full_name,
        role=payload.role,
    )
    await record_audit(
        session,
        AuditAction.USER_CREATED,
        audit,
        entity_type="user",
        entity_id=user.id,
        # The role, never the address and never anything derived from the password.
        metadata={"role": user.role.value},
    )
    await session.commit()
    return UserSummary.model_validate(user)


@router.patch(
    "/{user_id}",
    response_model=UserSummary,
    summary="Change somebody's name, role, or whether they can sign in",
    responses={
        400: {"description": "You cannot change your own role or switch yourself off."},
        403: {"description": "Only administrators may change an account."},
        404: {"description": "No such person in this workspace."},
        409: {"description": "That would leave the office with no administrator."},
    },
)
async def update_user(
    user_id: uuid.UUID,
    payload: UserUpdate,
    session: SessionDep,
    scope: WorkspaceScopeDep,
    audit: AuditContextDep,
) -> UserSummary:
    user = await user_service.update_user(
        session,
        scope=scope,
        user_id=user_id,
        full_name=payload.full_name,
        role=payload.role,
        is_active=payload.is_active,
    )
    await record_audit(
        session,
        AuditAction.USER_UPDATED,
        audit,
        entity_type="user",
        entity_id=user.id,
        metadata={"role": user.role.value, "is_active": user.is_active},
    )
    await session.commit()
    return UserSummary.model_validate(user)


@router.post(
    "/me/password",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    summary="Change your own password",
    responses={400: {"description": "The current password is wrong."}},
)
async def change_own_password(
    payload: PasswordChange,
    session: SessionDep,
    scope: WorkspaceScopeDep,
    audit: AuditContextDep,
) -> None:
    await user_service.change_own_password(
        session, scope=scope, current=payload.current_password, new=payload.new_password
    )
    await record_audit(
        session,
        AuditAction.USER_PASSWORD_CHANGED,
        audit,
        entity_type="user",
        entity_id=scope.user_id,
    )
    await session.commit()


@router.post(
    "/{user_id}/password",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    summary="Set somebody's password, having been asked in person",
    responses={
        403: {"description": "Only administrators may set another person's password."},
        404: {"description": "No such person in this workspace."},
    },
)
async def set_password(
    user_id: uuid.UUID,
    payload: PasswordSet,
    session: SessionDep,
    scope: WorkspaceScopeDep,
    audit: AuditContextDep,
) -> None:
    """There is no mail server in a records office, so this is the reset path.

    Every session of theirs ends: if the reason for the reset is that somebody else
    knew the old password, leaving those alive would defeat it.
    """
    user = await user_service.set_password(
        session, scope=scope, user_id=user_id, password=payload.password
    )
    await record_audit(
        session,
        AuditAction.USER_PASSWORD_RESET,
        audit,
        entity_type="user",
        entity_id=user.id,
    )
    await session.commit()
