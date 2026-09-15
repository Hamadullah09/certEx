"""Auth cookie names and the single place they are set or cleared."""

from __future__ import annotations

import datetime as dt

from fastapi import Response

from certex.config import Settings

__all__ = [
    "ACCESS_COOKIE",
    "CSRF_COOKIE",
    "REFRESH_COOKIE",
    "REFRESH_COOKIE_PATH",
    "clear_auth_cookies",
    "set_auth_cookies",
]

ACCESS_COOKIE = "certex_access"
REFRESH_COOKIE = "certex_refresh"
CSRF_COOKIE = "certex_csrf"

REFRESH_COOKIE_PATH = "/api/v1/auth"
"""The refresh cookie is scoped to the auth routes, so it is not attached to the
hundreds of ordinary API calls a results grid makes. Narrower blast radius if any
single response is ever logged or cached by an intermediary."""


def set_auth_cookies(
    response: Response,
    *,
    access_token: str,
    refresh_token: str,
    csrf_token: str,
    settings: Settings,
    access_expires_at: dt.datetime,
) -> None:
    """Attach the access, refresh and CSRF cookies to a response."""
    now = dt.datetime.now(dt.UTC)
    access_max_age = max(int((access_expires_at - now).total_seconds()), 0)

    response.set_cookie(
        ACCESS_COOKIE,
        access_token,
        max_age=access_max_age,
        httponly=True,
        secure=settings.cookie_secure,
        samesite=settings.cookie_samesite,
        domain=settings.cookie_domain,
        path="/",
    )
    response.set_cookie(
        REFRESH_COOKIE,
        refresh_token,
        max_age=settings.refresh_token_ttl_seconds,
        httponly=True,
        secure=settings.cookie_secure,
        samesite=settings.cookie_samesite,
        domain=settings.cookie_domain,
        path=REFRESH_COOKIE_PATH,
    )
    # Readable by JavaScript on purpose: the SPA echoes it back in a request
    # header so the server can prove the caller could read same-origin state.
    response.set_cookie(
        CSRF_COOKIE,
        csrf_token,
        max_age=settings.refresh_token_ttl_seconds,
        httponly=False,
        secure=settings.cookie_secure,
        samesite=settings.cookie_samesite,
        domain=settings.cookie_domain,
        path="/",
    )


def clear_auth_cookies(response: Response, *, settings: Settings) -> None:
    for name, path in (
        (ACCESS_COOKIE, "/"),
        (REFRESH_COOKIE, REFRESH_COOKIE_PATH),
        (CSRF_COOKIE, "/"),
    ):
        response.delete_cookie(
            name,
            path=path,
            domain=settings.cookie_domain,
            secure=settings.cookie_secure,
            httponly=name != CSRF_COOKIE,
            samesite=settings.cookie_samesite,
        )
