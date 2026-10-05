"""The people who use this office's workspace, and what they are allowed to do.

Three rules hold this together, and every one of them exists because of a way an
office can lock itself out or quietly lose its audit trail:

**An account is never deleted.** Deactivating keeps the row, so the audit log's
``performed_by`` still resolves to a person a year later. A deleted user turns every
entry they ever touched into an anonymous one.

**Nobody changes their own role or switches themselves off.** This is what actually
keeps an office from locking itself out. An administrator who demotes themselves by
mistake cannot undo it, because undoing it needs the role they just gave up - and an
office with no administrator has no way back in short of a database console, which the
people this is built for do not have.

**The last administrator cannot be removed.** Defence in depth rather than a live
check: it cannot fire today, because the only person who can demote an administrator is
another active administrator, who is therefore still there afterwards. It stays because
the rule it protects is the important one, and because the self-change rules above are
exactly the kind that get relaxed later for a good-sounding reason.

Passwords are set by an administrator rather than mailed out, because a records office
has no mail server. The forgotten-password path records a request that shows up in the
user list; the administrator then walks over and sets one.
"""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from certex.core.deps import WorkspaceScope
from certex.core.errors import BadRequestError, ConflictError, NotFoundError
from certex.core.security import hash_password, verify_password
from certex.db.base import utcnow
from certex.db.models import RefreshToken, User
from certex.enums import UserRole
from certex.logging_setup import get_logger

__all__ = [
    "change_own_password",
    "create_user",
    "list_users",
    "record_reset_request",
    "set_password",
    "update_user",
]

logger = get_logger(__name__)

MIN_PASSWORD_LENGTH = 10


async def list_users(session: AsyncSession, *, scope: WorkspaceScope) -> list[User]:
    """Everyone in this workspace, administrators first, then by name.

    Readable by any signed-in member rather than administrators only: knowing who else
    works here is not privileged, and a reviewer looking at "approved by" needs to be
    able to put a name to it.
    """
    return list(
        (
            await session.scalars(
                select(User)
                .where(User.workspace_id == scope.workspace_id)
                .order_by(User.role, User.full_name, User.email)
            )
        ).all()
    )


async def _get(session: AsyncSession, *, scope: WorkspaceScope, user_id: uuid.UUID) -> User:
    user = await session.scalar(
        select(User).where(User.id == user_id, User.workspace_id == scope.workspace_id)
    )
    if user is None:
        raise NotFoundError(
            "No such person in this workspace.",
            remediation="Refresh the list to see who is here.",
        )
    return user


async def _other_active_admins(
    session: AsyncSession, *, workspace_id: uuid.UUID, excluding: uuid.UUID
) -> int:
    return int(
        await session.scalar(
            select(func.count())
            .select_from(User)
            .where(
                User.workspace_id == workspace_id,
                User.role == UserRole.ADMIN,
                User.is_active.is_(True),
                User.id != excluding,
            )
        )
        or 0
    )


def _check_password(password: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise BadRequestError(
            f"A password must be at least {MIN_PASSWORD_LENGTH} characters.",
            title="Password is too short",
            remediation="Use a longer one - a few unrelated words is easier to remember.",
        )


async def create_user(
    session: AsyncSession,
    *,
    scope: WorkspaceScope,
    email: str,
    password: str,
    full_name: str | None,
    role: UserRole,
) -> User:
    """Add somebody to this office."""
    scope.require(UserRole.ADMIN)
    _check_password(password)

    cleaned = email.strip().lower()
    user = User(
        workspace_id=scope.workspace_id,
        email=cleaned,
        full_name=(full_name or "").strip() or None,
        password_hash=hash_password(password),
        role=role,
        is_active=True,
    )
    session.add(user)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        # Email is unique across the whole deployment, not per workspace, so this can
        # collide with an account in another office. The message says neither which.
        raise ConflictError(
            "That email address is already in use.",
            title="Account already exists",
            remediation="Use a different address, or ask the person to sign in.",
        ) from exc

    logger.info(
        "user.created",
        entity_id=str(user.id),
        workspace_id=str(scope.workspace_id),
        role=role.value,
    )
    return user


async def update_user(
    session: AsyncSession,
    *,
    scope: WorkspaceScope,
    user_id: uuid.UUID,
    full_name: str | None = None,
    role: UserRole | None = None,
    is_active: bool | None = None,
) -> User:
    """Change somebody's name, role or whether they can sign in."""
    scope.require(UserRole.ADMIN)
    user = await _get(session, scope=scope, user_id=user_id)
    themselves = user.id == scope.user_id

    if role is not None and role is not user.role:
        if themselves:
            raise BadRequestError(
                "You cannot change your own role.",
                title="Not your own role",
                remediation="Ask another administrator to do it.",
            )
        # Unreachable while self-demotion is refused above - see the module docstring.
        if user.role is UserRole.ADMIN and not await _other_active_admins(
            session, workspace_id=scope.workspace_id, excluding=user.id
        ):  # pragma: no cover - defence in depth
            raise ConflictError(
                "This is the only administrator left.",
                title="The office needs an administrator",
                remediation="Make somebody else an administrator first.",
            )
        user.role = role

    if is_active is not None and is_active is not user.is_active:
        if themselves:
            raise BadRequestError(
                "You cannot switch off your own account.",
                title="Not your own account",
                remediation="Ask another administrator to do it.",
            )
        if (
            not is_active
            and user.role is UserRole.ADMIN
            and not await _other_active_admins(
                session, workspace_id=scope.workspace_id, excluding=user.id
            )
        ):  # pragma: no cover - defence in depth, as above
            raise ConflictError(
                "This is the only administrator left.",
                title="The office needs an administrator",
                remediation="Make somebody else an administrator first.",
            )
        user.is_active = is_active
        if not is_active:
            # Signing out everywhere is the point of switching an account off; leaving
            # live refresh tokens would keep them working until the token expired.
            await _revoke_sessions(session, user_id=user.id)

    if full_name is not None:
        user.full_name = full_name.strip() or None

    await session.flush()
    logger.info("user.updated", entity_id=str(user.id), workspace_id=str(scope.workspace_id))
    return user


async def set_password(
    session: AsyncSession, *, scope: WorkspaceScope, user_id: uuid.UUID, password: str
) -> User:
    """An administrator sets somebody's password, having been asked in person.

    Every other session of theirs is ended. If the reason for the reset is that
    somebody else knew the old password, leaving their sessions alive would defeat it.
    """
    scope.require(UserRole.ADMIN)
    _check_password(password)
    user = await _get(session, scope=scope, user_id=user_id)

    user.password_hash = hash_password(password)
    user.password_reset_requested_at = None
    await _revoke_sessions(session, user_id=user.id)
    await session.flush()

    logger.info("user.password_set", entity_id=str(user.id), by=str(scope.user_id))
    return user


async def change_own_password(
    session: AsyncSession, *, scope: WorkspaceScope, current: str, new: str
) -> User:
    """Change your own password, proving you know the current one."""
    _check_password(new)
    user = await _get(session, scope=scope, user_id=scope.user_id)

    if not verify_password(current, user.password_hash):
        raise BadRequestError(
            "That is not your current password.",
            title="Current password is wrong",
            remediation="Try again, or ask an administrator to reset it for you.",
        )
    if current == new:
        raise BadRequestError(
            "The new password is the same as the old one.",
            title="Password unchanged",
            remediation="Choose a different one.",
        )

    user.password_hash = hash_password(new)
    user.password_reset_requested_at = None
    await session.flush()

    logger.info("user.password_changed", entity_id=str(user.id))
    return user


async def record_reset_request(session: AsyncSession, *, email: str) -> None:
    """Note that somebody says they have forgotten their password.

    Unauthenticated, so it says nothing back. Whether the address exists, whether the
    account is active, whether it is in this deployment at all - the caller is told the
    same thing either way, because an endpoint that answers differently is a list of
    who works here for anybody who asks.
    """
    user = await session.scalar(select(User).where(User.email == email.strip().lower()))
    if user is None or not user.is_active:
        logger.info("user.reset_requested_for_unknown_account")
        return

    user.password_reset_requested_at = utcnow()
    await session.flush()
    logger.info("user.reset_requested", entity_id=str(user.id))


async def _revoke_sessions(session: AsyncSession, *, user_id: uuid.UUID) -> None:
    """End every live session of one account."""
    now = dt.datetime.now(dt.UTC)
    for token in (
        await session.scalars(
            select(RefreshToken).where(
                RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None)
            )
        )
    ).all():
        token.revoked_at = now
