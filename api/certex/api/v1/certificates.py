"""The register: entries, their documents, and the duplicates they raise.

An entry here is what the office holds, as distinct from an extraction, which is
what one upload of one file appeared to say. Reading is open to any member of the
workspace; writing needs an operator. Two rules are enforced in this file rather
than left to the caller:

* a certificate number that is already held produces a conflict naming the entry
  that holds it, never an overwrite and never a silent merge;
* every route is scoped to the caller's workspace in the same statement that looks
  the row up, so another office's entry reads as absent rather than forbidden.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import AsyncIterator
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from certex.core.audit import record_audit
from certex.core.deps import AuditContextDep, SessionDep, WorkspaceScopeDep
from certex.core.errors import BadRequestError, NotFoundError, ValidationFailedError
from certex.db.models import Certificate, CertificateTypeRecord, Document
from certex.enums import AuditAction, CertificateSource, CertificateType, UserRole
from certex.export.columns import Column, ColumnPlan
from certex.export.rows import ExportRow
from certex.export.writers import DELIMITERS, write_csv_stream
from certex.fields import FieldSchema, builtin_schema
from certex.logging_setup import get_logger
from certex.schemas.certificates import (
    CertificateCreate,
    CertificateDetail,
    CertificateSummary,
    DocumentLink,
    DocumentLinkCreate,
    DuplicateCandidate,
)
from certex.schemas.common import Cursor, Page
from certex.services import certificate_service, schema_service

__all__ = ["router"]

logger = get_logger(__name__)
router = APIRouter(prefix="/certificates", tags=["certificates"])

CursorParam = Annotated[str | None, Query(description="Opaque cursor from a previous page.")]
LimitParam = Annotated[int, Query(ge=1, le=200, description="Maximum entries to return.")]

_EXPORT_PAGE = 500
"""Entries per database round trip while streaming an export."""


def _detail(certificate: Certificate) -> CertificateDetail:
    """Response model for one entry, with the JSONB columns given their real names."""
    return CertificateDetail.model_validate(certificate).model_copy(
        update={
            "values": dict(certificate.values_jsonb),
            "confidences": dict(certificate.confidences_jsonb),
            "provenance": dict(certificate.provenance_jsonb),
        }
    )


async def _schema_for(session: AsyncSession, certificate: Certificate) -> FieldSchema:
    """The schema an existing entry should be read back through.

    The pinned version first, because that is what its field names meant when it
    was written. An entry from before schemas existed falls back to the built-in
    definition for its classifier type, which is the same thing the pipeline used.
    """
    if certificate.schema_version_id is not None:
        return await schema_service.resolve_schema(session, certificate.schema_version_id)
    return builtin_schema(certificate.certificate_type.classifier_key or CertificateType.OTHER)


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------
@router.get(
    "",
    response_model=Page[CertificateSummary],
    summary="List register entries",
)
async def list_certificates(
    session: SessionDep,
    scope: WorkspaceScopeDep,
    certificate_type_id: Annotated[
        uuid.UUID | None, Query(description="Only entries of this certificate type.")
    ] = None,
    needs_review: Annotated[
        bool | None, Query(description="Only entries waiting for review, or only settled ones.")
    ] = None,
    duplicates_only: Annotated[
        bool, Query(description="Only entries with an unresolved duplicate question.")
    ] = False,
    limit: LimitParam = 50,
    cursor: CursorParam = None,
) -> Page[CertificateSummary]:
    rows, next_cursor = await certificate_service.list_certificates(
        session,
        workspace_id=scope.workspace_id,
        certificate_type_id=certificate_type_id,
        needs_review=needs_review,
        duplicates_only=duplicates_only,
        limit=limit,
        cursor=Cursor.decode(cursor) if cursor else None,
    )
    return Page.build(
        [CertificateSummary.model_validate(row) for row in rows],
        limit=limit,
        next_cursor=next_cursor,
    )


def _register_plan(schema: FieldSchema) -> ColumnPlan:
    """The columns of a register export: the schema's own fields, in its own order.

    No confidence or provenance columns. This file is the register as the office
    asserts it, not a report on how well a machine read it - that is what the batch
    export is for.
    """
    return ColumnPlan(
        columns=[
            Column(key=spec.name, header=spec.label, field=spec.name) for spec in schema.fields
        ]
    )


def _register_row(certificate: Certificate, schema: FieldSchema) -> ExportRow:
    stored = certificate.values_jsonb
    return ExportRow(
        cells={
            spec.name: value if isinstance(value := stored.get(spec.name), str) else ""
            for spec in schema.fields
        },
        flagged=certificate.needs_review,
    )


async def _stream_register(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    certificate_type_id: uuid.UUID,
    schema: FieldSchema,
    needs_review: bool | None,
) -> AsyncIterator[ExportRow]:
    """Every matching entry, a page at a time.

    Keyset paged rather than one big query: a register of a million entries has to
    leave the database a page at a time, and the download starts before the last page
    has been read.
    """
    cursor: Cursor | None = None
    while True:
        rows, cursor = await certificate_service.list_certificates(
            session,
            workspace_id=workspace_id,
            certificate_type_id=certificate_type_id,
            needs_review=needs_review,
            limit=_EXPORT_PAGE,
            cursor=cursor,
        )
        for row in rows:
            yield _register_row(row, schema)
        if cursor is None:
            return


@router.get(
    "/export",
    response_class=StreamingResponse,
    summary="Download the register as a CSV",
    responses={
        200: {"content": {"text/csv": {}}},
        404: {"description": "No such certificate type in this workspace."},
    },
)
async def export_register(
    session: SessionDep,
    scope: WorkspaceScopeDep,
    audit: AuditContextDep,
    certificate_type_id: Annotated[
        uuid.UUID, Query(description="Which register to export. One type per file.")
    ],
    needs_review: Annotated[
        bool | None, Query(description="Only entries waiting for review, or only settled ones.")
    ] = None,
    delimiter: Annotated[str, Query(description="comma, semicolon, tab or pipe.")] = "comma",
    include_bom: Annotated[
        bool, Query(description="Byte-order mark, so Excel on Windows reads Urdu correctly.")
    ] = True,
) -> StreamingResponse:
    """One certificate type per file, because their columns differ.

    The response begins before the query has finished: rows go from the database into
    the download a page at a time, so the file size is bounded by the register and the
    memory used is not.
    """
    separator = DELIMITERS.get(delimiter)
    if separator is None:
        raise BadRequestError(
            f"{delimiter!r} is not a delimiter this system writes.",
            remediation="Choose comma, semicolon, tab or pipe.",
        )

    type_record = await session.get(CertificateTypeRecord, certificate_type_id)
    if type_record is None or type_record.workspace_id != scope.workspace_id:
        raise NotFoundError("That certificate type does not exist in this workspace.")

    version = await schema_service.default_version_for_type(
        session, scope=scope, certificate_type_id=certificate_type_id
    )
    schema = (
        await schema_service.resolve_schema(session, version.id)
        if version is not None
        else builtin_schema(type_record.classifier_key or CertificateType.OTHER)
    )
    plan = _register_plan(schema)

    await record_audit(
        session,
        AuditAction.EXPORT_REQUESTED,
        audit,
        entity_type="certificate_type",
        entity_id=certificate_type_id,
    )
    await session.commit()

    stamp = dt.datetime.now(tz=dt.UTC).strftime("%Y%m%d-%H%M%S")
    filename = f"{type_record.key.lower()}-register-{stamp}.csv"
    rows = _stream_register(
        session,
        workspace_id=scope.workspace_id,
        certificate_type_id=certificate_type_id,
        schema=schema,
        needs_review=needs_review,
    )
    return StreamingResponse(
        write_csv_stream(plan, rows, delimiter=separator, include_bom=include_bom),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": (
                f"attachment; filename=\"{filename}\"; filename*=UTF-8''{quote(filename)}"
            )
        },
    )


@router.get(
    "/{certificate_id}",
    response_model=CertificateDetail,
    summary="One entry in full",
    responses={404: {"description": "No such entry in this register."}},
)
async def get_certificate(
    certificate_id: uuid.UUID,
    session: SessionDep,
    scope: WorkspaceScopeDep,
    audit: AuditContextDep,
) -> CertificateDetail:
    """Audited on read: an entry is personal data, so who looked is worth knowing."""
    certificate = await certificate_service.load_detail(
        session, workspace_id=scope.workspace_id, certificate_id=certificate_id
    )
    await record_audit(
        session,
        AuditAction.CERTIFICATE_VIEWED,
        audit,
        entity_type="certificate",
        entity_id=certificate.id,
    )
    await session.commit()
    return _detail(certificate)


@router.get(
    "/{certificate_id}/duplicates",
    response_model=list[DuplicateCandidate],
    summary="Other entries this one may repeat",
)
async def list_duplicates(
    certificate_id: uuid.UUID,
    session: SessionDep,
    scope: WorkspaceScopeDep,
) -> list[DuplicateCandidate]:
    """Recomputed rather than read from a column.

    The register changes under an entry: something that looked unique when it was
    written may have a twin by the time anyone looks, and the reverse happens when a
    number is corrected.
    """
    certificate = await certificate_service.load_detail(
        session, workspace_id=scope.workspace_id, certificate_id=certificate_id
    )
    schema = await _schema_for(session, certificate)
    draft = certificate_service.build_write(
        schema, certificate_service.text_values(certificate.values_jsonb)
    )
    matches = await certificate_service.find_duplicates(
        session,
        workspace_id=scope.workspace_id,
        certificate_type_id=certificate.certificate_type_id,
        draft=draft,
        exclude_id=certificate.id,
    )
    return [
        DuplicateCandidate(
            certificate_id=match.certificate_id,
            certificate_number=match.certificate_number,
            reason=match.reason,
            primary_name=match.primary_name,
            event_date=match.event_date,
            same_type=match.same_type,
        )
        for match in matches
    ]


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------
@router.post(
    "",
    response_model=CertificateDetail,
    status_code=status.HTTP_201_CREATED,
    summary="Record a certificate",
    responses={
        403: {"description": "Reading the register is open; writing to it is not."},
        409: {"description": "That certificate number is already in the register."},
        422: {"description": "The values do not fit the schema."},
    },
)
async def create_certificate(
    payload: CertificateCreate,
    session: SessionDep,
    scope: WorkspaceScopeDep,
    audit: AuditContextDep,
) -> CertificateDetail:
    scope.require(UserRole.OPERATOR)

    schema_version_id = payload.schema_version_id
    if schema_version_id is None:
        version = await schema_service.default_version_for_type(
            session, scope=scope, certificate_type_id=payload.certificate_type_id
        )
        if version is None:
            raise NotFoundError(
                "That certificate type has no published schema to record against.",
                remediation="Publish a schema version for the type, then try again.",
            )
        schema_version_id = version.id

    schema = await schema_service.resolve_schema(session, schema_version_id)
    unknown = sorted(name for name in payload.values if name not in schema)
    if unknown:
        raise ValidationFailedError(
            f"The schema has no field called {unknown[0]!r}.",
            remediation=(
                "Use the field names from the schema version, or add a new version "
                "that defines them."
            ),
        )

    certificate = await certificate_service.record_certificate(
        session,
        workspace_id=scope.workspace_id,
        certificate_type_id=payload.certificate_type_id,
        schema=schema,
        values=dict(payload.values),
        source=CertificateSource.MANUAL,
        actor_id=scope.user_id,
        allow_duplicate=payload.allow_duplicate,
    )
    await record_audit(
        session,
        AuditAction.CERTIFICATE_CREATED,
        audit,
        entity_type="certificate",
        entity_id=certificate.id,
        metadata={"count": len(payload.values)},
    )
    await session.commit()

    return _detail(
        await certificate_service.load_detail(
            session, workspace_id=scope.workspace_id, certificate_id=certificate.id
        )
    )


@router.post(
    "/{certificate_id}/documents",
    response_model=DocumentLink,
    status_code=status.HTTP_201_CREATED,
    summary="Attach a document to an entry",
    responses={
        403: {"description": "Attaching a document is an operator's action."},
        404: {"description": "No such entry, or no such document, in this workspace."},
    },
)
async def link_document(
    certificate_id: uuid.UUID,
    payload: DocumentLinkCreate,
    session: SessionDep,
    scope: WorkspaceScopeDep,
    audit: AuditContextDep,
) -> DocumentLink:
    """Linked by identity. The document is looked up inside this workspace, so a
    document id from elsewhere cannot be attached by guessing it."""
    scope.require(UserRole.OPERATOR)

    certificate = await certificate_service.get_certificate(
        session, workspace_id=scope.workspace_id, certificate_id=certificate_id
    )
    document = await session.get(Document, payload.document_id)
    if document is None or document.workspace_id != scope.workspace_id:
        raise NotFoundError("That document is not in this workspace.")

    link = await certificate_service.link_document(
        session,
        certificate=certificate,
        document_id=document.id,
        unit_id=payload.unit_id,
        kind=payload.kind,
        page_start=payload.page_start,
        page_end=payload.page_end,
        note=payload.note,
        actor_id=scope.user_id,
    )
    await record_audit(
        session,
        AuditAction.CERTIFICATE_DOCUMENT_LINKED,
        audit,
        entity_type="certificate",
        entity_id=certificate.id,
    )
    await session.commit()
    return DocumentLink.model_validate(link)
