"""HTTP middleware: request context, security headers and CSRF defence."""

from __future__ import annotations

import secrets
import time
import uuid
from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

from certex.config import Settings
from certex.core.cookies import CSRF_COOKIE
from certex.core.errors import ForbiddenError
from certex.core.responses import problem_response
from certex.logging_setup import bind_request_context, clear_request_context, get_logger

__all__ = [
    "CSRF_HEADER",
    "REQUEST_ID_HEADER",
    "CSRFMiddleware",
    "RequestContextMiddleware",
    "SecurityHeadersMiddleware",
]

logger = get_logger(__name__)

REQUEST_ID_HEADER = "X-Request-ID"
CSRF_HEADER = "X-CertEx-CSRF"

_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})

Dispatch = Callable[[Request], Awaitable[Response]]


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Tag every request with an id and emit one access record per response.

    The id is echoed in ``X-Request-ID`` and embedded in problem documents, so a
    user reporting "export failed" gives support an exact key into the logs
    without anyone having to quote document contents.
    """

    async def dispatch(self, request: Request, call_next: Dispatch) -> Response:
        incoming = request.headers.get(REQUEST_ID_HEADER, "")
        request_id = incoming.strip()[:64] or uuid.uuid4().hex

        clear_request_context()
        bind_request_context(
            request_id=request_id,
            method=request.method,
            path=request.url.path,
        )
        request.state.request_id = request_id

        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            # The exception handlers build the response; record the failure here so
            # the timing of a crashed request is still visible.
            logger.exception(
                "http.request_failed",
                duration_ms=round((time.perf_counter() - started) * 1000, 2),
            )
            clear_request_context()
            raise

        duration_ms = round((time.perf_counter() - started) * 1000, 2)
        response.headers[REQUEST_ID_HEADER] = request_id

        # Health probes fire constantly; logging each one buries real traffic.
        if not request.url.path.endswith(("/live", "/ready")):
            logger.info(
                "http.request",
                status_code=response.status_code,
                duration_ms=duration_ms,
            )
        clear_request_context()
        return response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Conservative response headers for a JSON API.

    The API serves no HTML, so the CSP is a deny-all: if a response is ever coaxed
    into rendering in a browser context, nothing in it can execute or load.
    """

    def __init__(self, app: ASGIApp, *, settings: Settings) -> None:
        super().__init__(app)
        self._settings = settings

    async def dispatch(self, request: Request, call_next: Dispatch) -> Response:
        response = await call_next(request)
        headers = response.headers
        headers.setdefault("X-Content-Type-Options", "nosniff")
        headers.setdefault("X-Frame-Options", "DENY")
        headers.setdefault("Referrer-Policy", "no-referrer")
        headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        headers.setdefault("Cross-Origin-Resource-Policy", "same-site")
        headers.setdefault("Permissions-Policy", "geolocation=(), camera=(), microphone=()")
        headers.setdefault(
            "Content-Security-Policy",
            "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'",
        )
        # Responses carry personal data; intermediaries must not retain them.
        headers.setdefault("Cache-Control", "no-store")

        if self._settings.cookie_secure:
            headers.setdefault("Strict-Transport-Security", "max-age=63072000; includeSubDomains")
        return response


class CSRFMiddleware(BaseHTTPMiddleware):
    """Double-submit CSRF check for cookie-authenticated state changes.

    ``SameSite`` already blocks the classic cross-site form post, but it is a
    browser-side control with historical gaps. A request that mutates state using
    the session *cookie* must additionally echo the readable CSRF cookie in a
    header, which a cross-origin page cannot do without a successful CORS
    preflight - and the allowlist refuses that.

    Requests authenticated with a bearer header are exempt: a bearer token is not
    attached ambiently by the browser, so there is nothing to forge.
    """

    def __init__(self, app: ASGIApp, *, exempt_paths: frozenset[str] | None = None) -> None:
        super().__init__(app)
        self._exempt = exempt_paths or frozenset()

    async def dispatch(self, request: Request, call_next: Dispatch) -> Response:
        if request.method in _SAFE_METHODS or request.url.path in self._exempt:
            return await call_next(request)

        authorization = request.headers.get("authorization", "")
        if authorization.lower().startswith("bearer "):
            return await call_next(request)

        cookie_token = request.cookies.get(CSRF_COOKIE)
        if cookie_token is None:
            # No session cookie at all: the route's own auth guard will answer 401.
            return await call_next(request)

        header_token = request.headers.get(CSRF_HEADER, "")
        if not header_token or not secrets.compare_digest(header_token, cookie_token):
            logger.warning("http.csrf_rejected", method=request.method, path=request.url.path)
            # Returned rather than raised: an exception escaping a BaseHTTPMiddleware
            # bypasses the app exception handlers and would surface as a bare 500.
            return problem_response(
                ForbiddenError(
                    "CSRF token missing or mismatched.",
                    title="Request blocked",
                    remediation=(
                        f"Send the value of the {CSRF_COOKIE} cookie in the "
                        f"{CSRF_HEADER} header. Reload the page to obtain a fresh token."
                    ),
                ),
                request,
            )
        return await call_next(request)
