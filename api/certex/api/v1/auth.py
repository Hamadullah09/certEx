"""Authentication routes: login, refresh, logout, current session."""

from __future__ import annotations

from fastapi import APIRouter, Request, Response, status
from sqlalchemy import select

from certex.core.audit import AuditContext, record_audit
from certex.core.cookies import (
    CSRF_COOKIE,
    REFRESH_COOKIE,
    clear_auth_cookies,
    set_auth_cookies,
)
from certex.core.deps import (
    AnonymousAuditContextDep,
    ClaimsDep,
    CurrentUser,
    SessionDep,
    SettingsDep,
)
from certex.core.errors import UnauthorizedError
from certex.core.ratelimit import get_rate_limiter
from certex.db.models import Workspace
from certex.enums import AuditAction
from certex.logging_setup import get_logger
from certex.schemas.auth import LoginRequest, SessionResponse, UserProfile, WorkspaceSummary
from certex.schemas.users import PasswordResetRequest
from certex.services import user_service
from certex.services.auth_service import (
    IssuedSession,
    authenticate,
    issue_session,
    revoke_refresh_token,
    rotate_session,
)

__all__ = ["router"]

logger = get_logger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])


def _to_response(issued: IssuedSession) -> SessionResponse:
    return SessionResponse(
        user=UserProfile.model_validate(issued.user),
        workspace=WorkspaceSummary.model_validate(issued.workspace),
        access_expires_at=issued.access_expires_at,
        csrf_token=issued.csrf_token,
    )


@router.post(
    "/login",
    response_model=SessionResponse,
    status_code=status.HTTP_200_OK,
    summary="Exchange credentials for a session",
    responses={
        401: {"description": "Credentials rejected or the account is disabled."},
        429: {"description": "Too many login attempts from this address."},
    },
)
async def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    session: SessionDep,
    settings: SettingsDep,
    audit: AnonymousAuditContextDep,
) -> SessionResponse:
    """Authenticate and set the session cookies.

    Throttled per client address rather than per account: limiting by email would
    let an attacker lock a known user out by exhausting their bucket.
    """
    limiter = get_rate_limiter()
    decision = await limiter.check(
        "login",
        audit.ip_address or "unknown",
        limit=settings.rate_limit_login_per_minute,
    )
    decision.raise_if_denied(what="login attempts")

    try:
        user = await authenticate(session, email=payload.email, password=payload.password)
    except Exception:
        await record_audit(
            session,
            AuditAction.LOGIN_FAILED,
            audit,
            entity_type="user",
            metadata={"reason_code": "invalid_credentials"},
        )
        await session.commit()
        raise

    issued = await issue_session(session, user=user, settings=settings, context=audit)

    await record_audit(
        session,
        AuditAction.LOGIN_SUCCEEDED,
        AuditContext(
            workspace_id=user.workspace_id,
            user_id=user.id,
            ip_address=audit.ip_address,
            user_agent=audit.user_agent,
        ),
        entity_type="user",
        entity_id=user.id,
        metadata={"role": user.role.value},
    )
    await session.commit()

    set_auth_cookies(
        response,
        access_token=issued.access_token,
        refresh_token=issued.refresh_token,
        csrf_token=issued.csrf_token,
        settings=settings,
        access_expires_at=issued.access_expires_at,
    )
    logger.info("auth.login", user_id=str(user.id), workspace_id=str(user.workspace_id))
    return _to_response(issued)


@router.post(
    "/forgot-password",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=None,
    summary="Say that you have forgotten your password",
    responses={
        202: {"description": "Recorded, whether or not that address has an account."},
        429: {"description": "Too many requests from this address."},
    },
)
async def forgot_password(
    payload: PasswordResetRequest,
    session: SessionDep,
    settings: SettingsDep,
    audit: AnonymousAuditContextDep,
) -> None:
    """Record the request. An administrator sees it in the user list and sets a new one.

    There is no mail server in a records office, so no link can be sent - and that is
    not really a loss, because the administrator is down the corridor and setting a
    password for somebody you can see is better evidence of who they are than an email
    link is.

    Always answers 202, whatever address is given. Answering differently for an address
    that exists would turn this into a list of who works here, readable by anybody.
    Throttled per address for the same reason login is.
    """
    decision = await get_rate_limiter().check(
        "forgot-password",
        audit.ip_address or "unknown",
        limit=settings.rate_limit_login_per_minute,
    )
    decision.raise_if_denied(what="password reset requests")

    await user_service.record_reset_request(session, email=payload.email)
    await session.commit()


@router.post(
    "/refresh",
    response_model=SessionResponse,
    summary="Rotate the refresh token and mint a new access token",
    responses={401: {"description": "Token missing, expired, revoked or replayed."}},
)
async def refresh(
    request: Request,
    response: Response,
    session: SessionDep,
    settings: SettingsDep,
    audit: AnonymousAuditContextDep,
) -> SessionResponse:
    raw_token = request.cookies.get(REFRESH_COOKIE)
    if not raw_token:
        raise UnauthorizedError(
            "No refresh cookie was supplied.",
            remediation="Sign in again.",
        )

    try:
        issued = await rotate_session(
            session,
            raw_refresh_token=raw_token,
            settings=settings,
            context=audit,
        )
    except Exception:
        # Reuse detection writes its own audit row inside rotate_session; commit it
        # before the error response unwinds the request.
        await session.commit()
        clear_auth_cookies(response, settings=settings)
        raise

    await record_audit(
        session,
        AuditAction.TOKEN_REFRESHED,
        AuditContext(
            workspace_id=issued.user.workspace_id,
            user_id=issued.user.id,
            ip_address=audit.ip_address,
            user_agent=audit.user_agent,
        ),
        entity_type="user",
        entity_id=issued.user.id,
    )
    await session.commit()

    set_auth_cookies(
        response,
        access_token=issued.access_token,
        refresh_token=issued.refresh_token,
        csrf_token=issued.csrf_token,
        settings=settings,
        access_expires_at=issued.access_expires_at,
    )
    return _to_response(issued)


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke the current refresh token and clear cookies",
)
async def logout(
    request: Request,
    response: Response,
    session: SessionDep,
    settings: SettingsDep,
    audit: AnonymousAuditContextDep,
) -> Response:
    """Idempotent: logging out without a session still clears cookies and returns 204."""
    raw_token = request.cookies.get(REFRESH_COOKIE)
    if raw_token:
        await revoke_refresh_token(session, raw_token)
        await record_audit(session, AuditAction.LOGOUT, audit, entity_type="user")
        await session.commit()

    clear_auth_cookies(response, settings=settings)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.get(
    "/me",
    response_model=SessionResponse,
    summary="Describe the current session",
    responses={401: {"description": "Not signed in."}},
)
async def me(
    user: CurrentUser,
    claims: ClaimsDep,
    session: SessionDep,
    request: Request,
) -> SessionResponse:
    """Used by the SPA on boot to restore session state from the cookie.

    Reports the expiry of the token the caller actually presented, so the client
    can schedule its refresh against the real deadline.
    """
    workspace = await session.scalar(select(Workspace).where(Workspace.id == user.workspace_id))
    if workspace is None:  # pragma: no cover - FK guarantees this
        raise UnauthorizedError("The workspace for this account no longer exists.")

    return SessionResponse(
        user=UserProfile.model_validate(user),
        workspace=WorkspaceSummary.model_validate(workspace),
        access_expires_at=claims.expires_at,
        csrf_token=request.cookies.get(CSRF_COOKIE, ""),
    )
