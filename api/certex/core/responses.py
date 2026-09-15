"""Problem-document response construction.

Kept separate from :mod:`certex.main` so middleware can build an error response
without importing the application, and separate from :mod:`certex.core.errors` so
the error taxonomy stays free of web-framework imports.
"""

from __future__ import annotations

from fastapi.responses import JSONResponse
from starlette.requests import Request

from certex.core.errors import AppError, ProblemDetail

__all__ = ["PROBLEM_MEDIA_TYPE", "problem_response"]

PROBLEM_MEDIA_TYPE = "application/problem+json"


def problem_response(error: AppError, request: Request | None = None) -> JSONResponse:
    """Render an :class:`AppError` as an RFC 7807 response."""
    request_id: str | None = None
    instance: str | None = None
    if request is not None:
        instance = request.url.path
        request_id = getattr(request.state, "request_id", None)

    problem: ProblemDetail = error.to_problem(instance=instance, request_id=request_id)
    headers: dict[str, str] = {}
    if error.retry_after_seconds is not None:
        headers["Retry-After"] = str(error.retry_after_seconds)

    return JSONResponse(
        status_code=problem.status,
        content=problem.model_dump(mode="json", exclude_none=True),
        media_type=PROBLEM_MEDIA_TYPE,
        headers=headers or None,
    )
