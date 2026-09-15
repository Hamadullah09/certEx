"""Login, refresh-token rotation and logout.

Rotation model
--------------
Every refresh issues a *new* token in the same ``family_id`` and marks the
presented one consumed. Presenting a consumed token means someone replayed a
stolen cookie, so the entire family is revoked and the user must sign in again.
That converts silent long-term token theft into an immediately visible logout.
"""

from __future__ import annotations

import datetime as dt
import secrets
import uuid
from dataclasses import dataclass
from functools import lru_cache

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from certex.config import Settings
from certex.core.audit import AuditContext, record_audit
from certex.core.errors import AppError, ErrorCode, UnauthorizedError
from certex.core.security import (
    create_access_token,
    generate_refresh_token,
    hash_password,
    hash_refresh_token,
    needs_rehash,
    verify_password,
)
from certex.db.models import RefreshToken, User, Workspace
from certex.enums import AuditAction
from certex.logging_setup import get_logger

__all__ = [
    "InvalidCredentialsError",
    "IssuedSession",
    "TokenReuseError",
    "authenticate",
    "issue_session",
    "revoke_family",
    "revoke_refresh_token",
    "rotate_session",
]

logger = get_logger(__name__)


@lru_cache(maxsize=1)
def _dummy_hash() -> str:
    """A throwaway hash used to equalise timing when no account matches.

    Derived from the *configured* work factor rather than hardcoded. A literal
    baked at cost 12 would be slower than a real comparison wherever
    PASSWORD_BCRYPT_ROUNDS is lower, which is the same timing oracle in reverse:
    an attacker could tell a known address from an unknown one by which request
    came back sooner. Computed once per process and cached.
    """
    return hash_password(secrets.token_urlsafe(32))


class InvalidCredentialsError(AppError):
    status = 401
    code = ErrorCode.INVALID_CREDENTIALS
    title = "Incorrect email or password"
    remediation = "Check the address and password, then try again."


class TokenReuseError(AppError):
    status = 401
    code = ErrorCode.TOKEN_REUSED
    title = "Session revoked"
    remediation = (
        "This session was replayed after it had already been used, so every session "
        "in the family was revoked as a precaution. Sign in again."
    )


@dataclass(frozen=True, slots=True)
class IssuedSession:
    """Everything the transport layer needs to set cookies and answer the caller."""

    user: User
    workspace: Workspace
    access_token: str
    access_expires_at: dt.datetime
    refresh_token: str
    csrf_token: str


async def authenticate(session: AsyncSession, *, email: str, password: str) -> User:
    """Verify credentials.

    Always performs a bcrypt comparison, even when the address is unknown, so the
    response time does not reveal which addresses have accounts.
    """
    normalised = email.strip().lower()
    user = await session.scalar(select(User).where(User.email == normalised))

    if user is None:
        # Burn the same KDF work a real comparison would, so response time does
        # not reveal which addresses have accounts.
        verify_password(password, _dummy_hash())
        raise InvalidCredentialsError("No account matches those credentials.")

    if not verify_password(password, user.password_hash):
        raise InvalidCredentialsError("No account matches those credentials.")

    if not user.is_active:
        error = UnauthorizedError(
            "This account has been deactivated.",
            remediation="Contact a workspace administrator.",
        )
        error.code = ErrorCode.ACCOUNT_DISABLED
        raise error

    # Opportunistic upgrade when the configured work factor has been raised.
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
        logger.info("auth.password_rehashed", user_id=str(user.id))

    return user


async def _load_workspace(session: AsyncSession, workspace_id: uuid.UUID) -> Workspace:
    workspace = await session.scalar(select(Workspace).where(Workspace.id == workspace_id))
    if workspace is None:  # pragma: no cover - FK guarantees this
        raise UnauthorizedError("The workspace for this account no longer exists.")
    return workspace


async def issue_session(
    session: AsyncSession,
    *,
    user: User,
    settings: Settings,
    context: AuditContext,
    family_id: uuid.UUID | None = None,
) -> IssuedSession:
    """Mint an access token and a fresh refresh token."""
    now = dt.datetime.now(dt.UTC)
    access_token, access_expires_at = create_access_token(
        user_id=user.id,
        workspace_id=user.workspace_id,
        role=user.role,
        settings=settings,
        now=now,
    )
    raw_refresh, refresh_hash = generate_refresh_token()

    record = RefreshToken(
        user_id=user.id,
        family_id=family_id or uuid.uuid4(),
        token_hash=refresh_hash,
        issued_at=now,
        expires_at=now + dt.timedelta(seconds=settings.refresh_token_ttl_seconds),
        ip_address=context.ip_address,
    )
    session.add(record)

    user.last_login_at = now
    workspace = await _load_workspace(session, user.workspace_id)

    return IssuedSession(
        user=user,
        workspace=workspace,
        access_token=access_token,
        access_expires_at=access_expires_at,
        refresh_token=raw_refresh,
        csrf_token=secrets.token_urlsafe(32),
    )


async def revoke_family(session: AsyncSession, family_id: uuid.UUID) -> int:
    """Revoke every unrevoked token in a family. Returns how many were revoked."""
    now = dt.datetime.now(dt.UTC)
    result = await session.execute(
        update(RefreshToken)
        .where(RefreshToken.family_id == family_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=now)
        .returning(RefreshToken.id)
    )
    return len(result.all())


async def rotate_session(
    session: AsyncSession,
    *,
    raw_refresh_token: str,
    settings: Settings,
    context: AuditContext,
) -> IssuedSession:
    """Consume a refresh token and issue its successor."""
    token_hash = hash_refresh_token(raw_refresh_token)
    record = await session.scalar(select(RefreshToken).where(RefreshToken.token_hash == token_hash))

    if record is None:
        raise UnauthorizedError(
            "This session is not recognised.",
            remediation="Sign in again.",
        )

    now = dt.datetime.now(dt.UTC)

    if record.consumed_at is not None:
        revoked = await revoke_family(session, record.family_id)
        await record_audit(
            session,
            AuditAction.TOKEN_REUSE_DETECTED,
            AuditContext(
                workspace_id=context.workspace_id,
                user_id=record.user_id,
                ip_address=context.ip_address,
                user_agent=context.user_agent,
            ),
            entity_type="refresh_token",
            entity_id=record.id,
            metadata={"count": revoked},
        )
        logger.warning(
            "auth.refresh_token_reuse",
            user_id=str(record.user_id),
            count=revoked,
        )
        raise TokenReuseError("This session token had already been used.")

    if record.revoked_at is not None:
        raise UnauthorizedError(
            "This session has been revoked.",
            remediation="Sign in again.",
        )

    if record.expires_at <= now:
        raise UnauthorizedError(
            "This session has expired.",
            remediation="Sign in again.",
        )

    user = await session.scalar(select(User).where(User.id == record.user_id))
    if user is None or not user.is_active:
        raise UnauthorizedError(
            "The account for this session is no longer active.",
            remediation="Contact a workspace administrator.",
        )

    issued = await issue_session(
        session,
        user=user,
        settings=settings,
        context=context,
        family_id=record.family_id,
    )

    record.consumed_at = now
    await session.flush()

    successor = await session.scalar(
        select(RefreshToken).where(
            RefreshToken.token_hash == hash_refresh_token(issued.refresh_token)
        )
    )
    if successor is not None:
        record.replaced_by_id = successor.id

    return issued


async def revoke_refresh_token(session: AsyncSession, raw_refresh_token: str) -> bool:
    """Revoke a single token on logout. Returns True when one was found."""
    record = await session.scalar(
        select(RefreshToken).where(RefreshToken.token_hash == hash_refresh_token(raw_refresh_token))
    )
    if record is None:
        return False
    if record.revoked_at is None:
        record.revoked_at = dt.datetime.now(dt.UTC)
    return True
