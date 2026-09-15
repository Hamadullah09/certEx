"""FastAPI dependencies: authentication, role gates and workspace scoping.

Workspace isolation is enforced *here and in the query layer*, never in the UI.
Handlers receive a :class:`WorkspaceScope` and pass it to repository functions
which add the ``workspace_id`` predicate themselves; a handler cannot forget it,
because the repository refuses to build a query without one.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from certex.config import Settings, get_settings
from certex.core.audit import AuditContext, client_ip
from certex.core.cookies import ACCESS_COOKIE
from certex.core.errors import ErrorCode, ForbiddenError, UnauthorizedError
from certex.core.security import AccessTokenClaims, decode_access_token
from certex.db.models import User
from certex.db.session import get_async_session
from certex.enums import UserRole
from certex.logging_setup import bind_request_context

__all__ = [
    "AuditContextDep",
    "CurrentUser",
    "SessionDep",
    "SettingsDep",
    "WorkspaceScope",
    "WorkspaceScopeDep",
    "require_admin",
    "require_operator",
    "require_role",
    "require_viewer",
]

SessionDep = Annotated[AsyncSession, Depends(get_async_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


class WorkspaceScope:
    """The tenancy boundary for one request.

    Passed to every repository call. Holding one is proof that the caller
    authenticated into this specific workspace.
    """

    __slots__ = ("role", "user_id", "workspace_id")

    def __init__(self, *, workspace_id: uuid.UUID, user_id: uuid.UUID, role: UserRole) -> None:
        self.workspace_id = workspace_id
        self.user_id = user_id
        self.role = role

    def require(self, minimum: UserRole) -> None:
        if not self.role.satisfies(minimum):
            raise ForbiddenError(
                f"This action requires the {minimum.value} role; you have {self.role.value}.",
                remediation="Ask a workspace administrator to raise your role.",
            )

    @property
    def can_write(self) -> bool:
        return self.role.satisfies(UserRole.OPERATOR)

    def __repr__(self) -> str:
        return f"<WorkspaceScope workspace={self.workspace_id} role={self.role.value}>"


def _extract_token(request: Request) -> str:
    """Read the access token from the httpOnly cookie, or a bearer header.

    The cookie is the browser path. The header exists for CLI clients and the
    test suite; both are verified identically.
    """
    cookie_token = request.cookies.get(ACCESS_COOKIE)
    if cookie_token:
        return cookie_token

    authorization = request.headers.get("authorization", "")
    scheme, _, credentials = authorization.partition(" ")
    if scheme.lower() == "bearer" and credentials.strip():
        return credentials.strip()

    raise UnauthorizedError(
        "No session cookie or bearer token was supplied.",
        remediation="Sign in to obtain a session.",
    )


async def get_current_claims(request: Request) -> AccessTokenClaims:
    """Verify the access token and bind its ids to the logging context."""
    claims = decode_access_token(_extract_token(request))
    bind_request_context(
        user_id=str(claims.sub),
        workspace_id=str(claims.wsp),
        role=claims.role.value,
    )
    return claims


ClaimsDep = Annotated[AccessTokenClaims, Depends(get_current_claims)]


async def get_current_user(session: SessionDep, claims: ClaimsDep) -> User:
    """Load the user behind a valid token.

    The token alone is not sufficient: a user deactivated mid-session must stop
    being able to act before their access token expires.
    """
    user = await session.scalar(select(User).where(User.id == claims.sub))
    if user is None:
        raise UnauthorizedError(
            "The account for this session no longer exists.",
            remediation="Sign in again.",
        )
    if not user.is_active:
        error = UnauthorizedError(
            "This account has been deactivated.",
            remediation="Contact a workspace administrator.",
        )
        error.code = ErrorCode.ACCOUNT_DISABLED
        raise error
    if user.workspace_id != claims.wsp:
        # The token was minted for a different tenant than the account now sits in.
        raise UnauthorizedError(
            "Session does not match the account workspace.",
            remediation="Sign in again.",
        )
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


async def get_workspace_scope(user: CurrentUser) -> WorkspaceScope:
    return WorkspaceScope(
        workspace_id=user.workspace_id,
        user_id=user.id,
        role=user.role,
    )


WorkspaceScopeDep = Annotated[WorkspaceScope, Depends(get_workspace_scope)]


def require_role(minimum: UserRole) -> Callable[[WorkspaceScope], Awaitable[WorkspaceScope]]:
    """Dependency factory gating a route on a minimum role."""

    async def _guard(scope: WorkspaceScopeDep) -> WorkspaceScope:
        scope.require(minimum)
        return scope

    return _guard


require_viewer = require_role(UserRole.VIEWER)
require_operator = require_role(UserRole.OPERATOR)
require_admin = require_role(UserRole.ADMIN)

ViewerScope = Annotated[WorkspaceScope, Depends(require_viewer)]
OperatorScope = Annotated[WorkspaceScope, Depends(require_operator)]
AdminScope = Annotated[WorkspaceScope, Depends(require_admin)]


async def get_audit_context(request: Request, user: CurrentUser) -> AuditContext:
    return AuditContext(
        workspace_id=user.workspace_id,
        user_id=user.id,
        ip_address=client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )


AuditContextDep = Annotated[AuditContext, Depends(get_audit_context)]


async def get_anonymous_audit_context(request: Request) -> AuditContext:
    """Audit context for routes that run before authentication (login attempts)."""
    return AuditContext(
        ip_address=client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )


AnonymousAuditContextDep = Annotated[AuditContext, Depends(get_anonymous_audit_context)]
