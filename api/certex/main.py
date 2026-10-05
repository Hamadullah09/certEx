"""FastAPI application factory.

Exposes ``app`` for ``uvicorn certex.main:app`` and for the Gunicorn worker class
used in the container image.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from certex import __version__
from certex.api.v1 import auth as auth_routes
from certex.api.v1 import batches as batch_routes
from certex.api.v1 import certificates as certificate_routes
from certex.api.v1 import exports as export_routes
from certex.api.v1 import health as health_routes
from certex.api.v1 import imports as import_routes
from certex.api.v1 import progress as progress_routes
from certex.api.v1 import registry as registry_routes
from certex.api.v1 import rows as row_routes
from certex.api.v1 import templates as template_routes
from certex.api.v1 import units as unit_routes
from certex.api.v1 import users as user_routes
from certex.api.v1 import workspace as workspace_routes
from certex.config import Settings, get_settings
from certex.core.errors import (
    AppError,
    BadRequestError,
    ErrorCode,
    FieldError,
    NotFoundError,
    ValidationFailedError,
)
from certex.core.middleware import (
    CSRF_HEADER,
    REQUEST_ID_HEADER,
    CSRFMiddleware,
    RequestContextMiddleware,
    SecurityHeadersMiddleware,
)
from certex.core.responses import problem_response
from certex.db.session import dispose_engines
from certex.logging_setup import configure_logging, get_logger, safe_error

__all__ = ["app", "create_app"]

logger = get_logger(__name__)

API_V1_PREFIX = "/api/v1"

_DESCRIPTION = """
Batch extraction of structured fields from certificate documents.

Upload PDFs and Word files - including image-only scans - and the pipeline splits
multi-certificate files into individual certificates, reads them with a native text
layer or OCR, extracts typed fields with confidence scores and provenance, validates
them, and exports one row per certificate as CSV, XLSX or JSON.

Errors are [RFC 7807](https://www.rfc-editor.org/rfc/rfc7807) problem documents
carrying a stable `code` and a `remediation` sentence.
"""


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    settings: Settings = get_settings()
    configure_logging(settings)
    logger.info("api.startup", status=settings.app_env.value)
    try:
        yield
    finally:
        await dispose_engines()
        logger.info("api.shutdown")


def create_app(settings: Settings | None = None) -> FastAPI:
    active = settings or get_settings()
    configure_logging(active)

    application = FastAPI(
        title=f"{active.app_name} API",
        description=_DESCRIPTION,
        version=__version__,
        lifespan=lifespan,
        docs_url="/docs" if active.docs_enabled else None,
        redoc_url="/redoc" if active.docs_enabled else None,
        openapi_url="/openapi.json" if active.docs_enabled else None,
        swagger_ui_parameters={"persistAuthorization": True},
        contact={"name": "CertExtract"},
        openapi_tags=[
            {"name": "health", "description": "Liveness and readiness probes."},
            {"name": "auth", "description": "Session lifecycle."},
            {
                "name": "registry",
                "description": "Certificate types and the schemas that define their fields.",
            },
            {
                "name": "workspace",
                "description": "Settings for one records office.",
            },
            {
                "name": "imports",
                "description": "Loading an existing register in from a CSV.",
            },
            {
                "name": "certificates",
                "description": (
                    "The register itself: entries, the documents behind them, and "
                    "the duplicates they raise."
                ),
            },
            {
                "name": "batches",
                "description": "Create batches, upload documents, inspect progress.",
            },
        ],
    )

    _register_middleware(application, active)
    _register_exception_handlers(application)
    _register_routes(application)
    return application


def _register_middleware(application: FastAPI, settings: Settings) -> None:
    """Install middleware.

    Starlette runs the **last** registered middleware outermost, so these are
    added innermost-first: CSRF sits closest to the routes, and CORS is outermost
    so that preflights and error responses both carry the right headers.
    """
    application.add_middleware(
        CSRFMiddleware,
        exempt_paths=frozenset(
            {
                f"{API_V1_PREFIX}/auth/login",
                f"{API_V1_PREFIX}/auth/refresh",
                f"{API_V1_PREFIX}/auth/logout",
            }
        ),
    )
    application.add_middleware(SecurityHeadersMiddleware, settings=settings)
    application.add_middleware(RequestContextMiddleware)
    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "Authorization", CSRF_HEADER, REQUEST_ID_HEADER],
        expose_headers=[REQUEST_ID_HEADER, "Content-Disposition", "Retry-After"],
        max_age=600,
    )


def _register_exception_handlers(application: FastAPI) -> None:
    @application.exception_handler(AppError)
    async def _app_error(request: Request, exc: AppError) -> JSONResponse:
        if exc.status >= 500:
            logger.error("api.error", error_code=exc.code.value, status_code=exc.status)
        else:
            logger.info("api.client_error", error_code=exc.code.value, status_code=exc.status)
        return problem_response(exc, request)

    @application.exception_handler(RequestValidationError)
    async def _request_validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        """Translate FastAPI's validation report into field-level problem entries."""
        errors = [
            FieldError(
                field=".".join(str(part) for part in error["loc"][1:]) or str(error["loc"][0]),
                message=str(error["msg"]),
                code=str(error["type"]),
            )
            for error in exc.errors()
        ]
        return problem_response(
            ValidationFailedError(
                "One or more request fields are invalid.",
                errors=errors,
            ),
            request,
        )

    @application.exception_handler(ValidationError)
    async def _pydantic_validation(request: Request, exc: ValidationError) -> JSONResponse:
        """A model validated outside request parsing - treat as a server fault."""
        logger.error("api.response_validation_failed", count=exc.error_count())
        return problem_response(AppError("The server produced a malformed response."), request)

    @application.exception_handler(StarletteHTTPException)
    async def _http_exception(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        """Convert framework 404/405/etc. into problem documents for consistency."""
        detail = exc.detail if isinstance(exc.detail, str) else None
        if exc.status_code == 404:
            return problem_response(NotFoundError(detail or "No route matches this path."), request)
        if exc.status_code == 405:
            method_error = BadRequestError(detail or "That method is not allowed on this path.")
            method_error.title = "Method not allowed"
            method_error.status = 405
            return problem_response(method_error, request)

        generic = AppError(detail)
        generic.status = exc.status_code
        generic.title = detail or "Request failed"
        generic.code = ErrorCode.BAD_REQUEST if exc.status_code < 500 else ErrorCode.INTERNAL_ERROR
        return problem_response(generic, request)

    @application.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        """Last resort.

        The internal message is logged, never returned: an unexpected exception can
        embed a field value, and this response crosses a trust boundary.
        """
        logger.error("api.unhandled_exception", error_type=safe_error(exc), exc_info=exc)
        return problem_response(
            AppError(
                "An unexpected error occurred.",
                remediation=(
                    "Retry the request. If it keeps failing, quote the request id "
                    "from the X-Request-ID response header to support."
                ),
            ),
            request,
        )


def _register_routes(application: FastAPI) -> None:
    application.include_router(health_routes.router, prefix="/health")
    application.include_router(health_routes.router, prefix=f"{API_V1_PREFIX}/health")
    application.include_router(auth_routes.router, prefix=API_V1_PREFIX)
    application.include_router(batch_routes.router, prefix=API_V1_PREFIX)
    application.include_router(registry_routes.router, prefix=API_V1_PREFIX)
    application.include_router(certificate_routes.router, prefix=API_V1_PREFIX)
    application.include_router(import_routes.router, prefix=API_V1_PREFIX)
    application.include_router(workspace_routes.router, prefix=API_V1_PREFIX)
    application.include_router(unit_routes.router, prefix=API_V1_PREFIX)
    application.include_router(row_routes.router, prefix=API_V1_PREFIX)
    application.include_router(progress_routes.router, prefix=API_V1_PREFIX)
    application.include_router(template_routes.router, prefix=API_V1_PREFIX)
    application.include_router(user_routes.router, prefix=API_V1_PREFIX)
    application.include_router(export_routes.router, prefix=API_V1_PREFIX)


app = create_app()
