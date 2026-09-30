"""Bulk import: the CSV template, the upload, and what happened to each row.

An office that already keeps its registers in spreadsheets starts here. The request
does as little as possible - hash the file, write it to storage, record the request and
queue it - because reading three hundred thousand rows takes minutes and a request that
holds a connection open that long is a request that dies halfway through.
"""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, File, Form, Query, Response, UploadFile, status

from certex.core.audit import record_audit
from certex.core.deps import (
    AuditContextDep,
    SessionDep,
    SettingsDep,
    WorkspaceScopeDep,
)
from certex.core.errors import BadRequestError, NotFoundError, PayloadTooLargeError
from certex.core.ratelimit import get_rate_limiter
from certex.db.models import CertificateTypeRecord
from certex.enums import AuditAction, CertificateType, ImportDuplicatePolicy, UserRole
from certex.fields import FieldSchema, builtin_schema
from certex.imports.template import template_csv
from certex.logging_setup import get_logger
from certex.pipeline.dispatch import TASK_IMPORT_CSV, enqueue
from certex.schemas.common import Cursor, Page
from certex.schemas.imports import ImportErrorPage, ImportRowErrorOut, ImportSummary
from certex.services import import_service, schema_service
from certex.services.import_service import MAX_ERRORS_RECORDED
from certex.storage.s3 import StorageKeys, get_object_storage

__all__ = ["router"]

logger = get_logger(__name__)
router = APIRouter(tags=["imports"])

_CHUNK = 1024 * 1024
_ALLOWED_DELIMITERS = {"comma": ",", "semicolon": ";", "tab": "\t", "pipe": "|"}

CursorParam = Annotated[str | None, Query(description="Opaque cursor from a previous page.")]


def _disposition(filename: str) -> str:
    return f"attachment; filename=\"{filename}\"; filename*=UTF-8''{quote(filename)}"


async def _schema_for_type(
    session: SessionDep,
    scope: WorkspaceScopeDep,
    certificate_type_id: uuid.UUID,
) -> tuple[CertificateTypeRecord, FieldSchema, uuid.UUID | None]:
    """The type, the schema its columns are read against, and the version pinned.

    A type with no published schema falls back to the built-in definition for whatever
    the classifier calls it, so an import is possible on the day a workspace is created
    and before anyone has opened the schema builder.
    """
    record = await session.get(CertificateTypeRecord, certificate_type_id)
    if record is None or record.workspace_id != scope.workspace_id:
        raise NotFoundError("That certificate type does not exist in this workspace.")

    version = await schema_service.default_version_for_type(
        session, scope=scope, certificate_type_id=certificate_type_id
    )
    if version is None:
        return record, builtin_schema(record.classifier_key or CertificateType.OTHER), None
    return record, await schema_service.resolve_schema(session, version.id), version.id


@router.get(
    "/certificate-types/{certificate_type_id}/import-template",
    response_class=Response,
    summary="The CSV to fill in for this certificate type",
    responses={200: {"content": {"text/csv": {}}}},
)
async def download_template(
    certificate_type_id: uuid.UUID,
    session: SessionDep,
    scope: WorkspaceScopeDep,
    delimiter: Annotated[str, Query(description="comma, semicolon, tab or pipe.")] = "comma",
) -> Response:
    """Generated from the schema, so a field added today appears in it today."""
    separator = _ALLOWED_DELIMITERS.get(delimiter)
    if separator is None:
        raise BadRequestError(
            f"{delimiter!r} is not a delimiter this system writes.",
            remediation="Choose comma, semicolon, tab or pipe.",
        )

    record, schema, _version_id = await _schema_for_type(session, scope, certificate_type_id)
    payload = template_csv(schema, delimiter=separator)
    filename = f"{record.key.lower()}-import-template.csv"
    return Response(
        content=payload.encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": _disposition(filename)},
    )


@router.post(
    "/imports",
    response_model=ImportSummary,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Upload a CSV of existing records",
    responses={
        403: {"description": "Importing is an operator's action."},
        404: {"description": "No such certificate type in this workspace."},
        413: {"description": "The file is larger than this deployment accepts."},
    },
)
async def create_import(
    session: SessionDep,
    scope: WorkspaceScopeDep,
    settings: SettingsDep,
    audit: AuditContextDep,
    certificate_type_id: Annotated[uuid.UUID, Form(description="Which register to load into.")],
    file: Annotated[UploadFile, File(description="A CSV of existing records.")],
    duplicate_policy: Annotated[
        ImportDuplicatePolicy,
        Form(
            description=(
                "What to do with a certificate number the register already holds. "
                "Neither option replaces an existing entry."
            )
        ),
    ] = ImportDuplicatePolicy.SKIP,
    delimiter: Annotated[
        str, Form(description="comma, semicolon, tab or pipe. Detected when omitted.")
    ] = "",
) -> ImportSummary:
    """Accept the file and queue it. The response is the import to poll, not a result."""
    scope.require(UserRole.OPERATOR)

    # Each import is potentially hours of worker time, so what is bounded here is how
    # much work one account can queue, not how fast it can ask.
    decision = await get_rate_limiter().check(
        "import",
        str(scope.user_id),
        limit=settings.rate_limit_import_per_hour,
        window_seconds=3600,
    )
    decision.raise_if_denied(what="imports")

    _record, _schema, version_id = await _schema_for_type(session, scope, certificate_type_id)

    separator = ""
    if delimiter:
        chosen = _ALLOWED_DELIMITERS.get(delimiter)
        if chosen is None:
            raise BadRequestError(
                f"{delimiter!r} is not a delimiter this system reads.",
                remediation="Choose comma, semicolon, tab or pipe, or leave it out.",
            )
        separator = chosen

    import_id = uuid.uuid4()
    key = StorageKeys.import_file(scope.workspace_id, import_id)

    # Hashed and sized as it goes past, so neither the API nor the worker ever holds
    # the file. The name the operator gave it is kept for display and used for nothing.
    digest = hashlib.sha256()
    size = 0
    while chunk := await file.read(_CHUNK):
        size += len(chunk)
        if size > settings.max_file_bytes:
            raise PayloadTooLargeError(
                f"The file is larger than the {settings.max_file_bytes // 1_048_576} MB "
                "this deployment accepts.",
                remediation="Split the spreadsheet and import it in parts.",
            )
        digest.update(chunk)
    if size == 0:
        raise BadRequestError(
            "The uploaded file is empty.",
            remediation="Export the register from your spreadsheet and upload that.",
        )

    # Off the event loop: boto3 is synchronous, and a large file would otherwise
    # block every other request for the length of the upload.
    await file.seek(0)
    await asyncio.to_thread(
        get_object_storage().upload_stream, key, file.file, content_type="text/csv"
    )

    record = await import_service.create_import(
        session,
        scope=scope,
        import_id=import_id,
        certificate_type_id=certificate_type_id,
        schema_version_id=version_id,
        original_filename=(file.filename or "import.csv")[:255],
        storage_key=key,
        byte_size=size,
        sha256=digest.hexdigest(),
        delimiter=separator,
        duplicate_policy=duplicate_policy,
    )
    await record_audit(
        session,
        AuditAction.IMPORT_STARTED,
        audit,
        entity_type="certificate_import",
        entity_id=record.id,
        metadata={"byte_size": size},
    )
    await session.commit()

    enqueue(TASK_IMPORT_CSV, {"import_id": str(record.id)})
    return ImportSummary.model_validate(record)


@router.get("/imports", response_model=Page[ImportSummary], summary="Imports, newest first")
async def list_imports(
    session: SessionDep,
    scope: WorkspaceScopeDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    cursor: CursorParam = None,
) -> Page[ImportSummary]:
    rows, next_cursor = await import_service.list_imports(
        session,
        workspace_id=scope.workspace_id,
        limit=limit,
        cursor=Cursor.decode(cursor) if cursor else None,
    )
    return Page.build(
        [ImportSummary.model_validate(row) for row in rows],
        limit=limit,
        next_cursor=next_cursor,
    )


@router.get(
    "/imports/{import_id}",
    response_model=ImportSummary,
    summary="How an import is getting on",
)
async def get_import(
    import_id: uuid.UUID, session: SessionDep, scope: WorkspaceScopeDep
) -> ImportSummary:
    record = await import_service.get_import(
        session, workspace_id=scope.workspace_id, import_id=import_id
    )
    return ImportSummary.model_validate(record)


@router.get(
    "/imports/{import_id}/errors",
    response_model=ImportErrorPage,
    summary="The rows that could not be filed, and why",
)
async def list_import_errors(
    import_id: uuid.UUID,
    session: SessionDep,
    scope: WorkspaceScopeDep,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ImportErrorPage:
    """In row order, so a clerk can work down their spreadsheet."""
    rows, total = await import_service.list_errors(
        session,
        workspace_id=scope.workspace_id,
        import_id=import_id,
        limit=limit,
        offset=offset,
    )
    return ImportErrorPage(
        items=[ImportRowErrorOut.model_validate(row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
        truncated=total >= MAX_ERRORS_RECORDED,
    )


@router.post(
    "/imports/{import_id}/cancel",
    response_model=ImportSummary,
    summary="Cancel an import that has not started",
    responses={409: {"description": "It has already started, or already finished."}},
)
async def cancel_import(
    import_id: uuid.UUID,
    session: SessionDep,
    scope: WorkspaceScopeDep,
    audit: AuditContextDep,
) -> ImportSummary:
    record = await import_service.cancel_import(session, scope=scope, import_id=import_id)
    await record_audit(
        session,
        AuditAction.IMPORT_CANCELLED,
        audit,
        entity_type="certificate_import",
        entity_id=record.id,
    )
    await session.commit()
    return ImportSummary.model_validate(record)
