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
import re
import uuid
from collections.abc import AsyncIterator, Iterator
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from certex.core.audit import record_audit
from certex.core.deps import (
    AuditContextDep,
    SessionDep,
    SettingsDep,
    WorkspaceScopeDep,
)
from certex.core.errors import BadRequestError, NotFoundError, ValidationFailedError
from certex.core.ratelimit import get_rate_limiter
from certex.db.models import Certificate, CertificateDocument, CertificateTypeRecord, Document
from certex.enums import (
    AuditAction,
    CertificateSource,
    CertificateType,
    RevisionAction,
    UserRole,
)
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
    DocumentReplacement,
    DuplicateCandidate,
)
from certex.schemas.common import Cursor, Page
from certex.schemas.review import (
    ApproveRequest,
    CorrectionRequest,
    ResolveDuplicateRequest,
    ReviewSummary,
    RevisionOut,
    VoidRequest,
)
from certex.schemas.search import SearchHitOut, SearchResponse
from certex.services import (
    certificate_service,
    review_service,
    schema_service,
    search_service,
    workspace_service,
)
from certex.storage.s3 import get_object_storage

__all__ = ["router"]

logger = get_logger(__name__)
router = APIRouter(prefix="/certificates", tags=["certificates"])

CursorParam = Annotated[str | None, Query(description="Opaque cursor from a previous page.")]
LimitParam = Annotated[int, Query(ge=1, le=200, description="Maximum entries to return.")]

_EXPORT_PAGE = 500
"""Entries per database round trip while streaming an export."""

_DOWNLOAD_CHUNK = 256 * 1024
"""Bytes per block when streaming a scan back to the browser."""

_FILENAME_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")

_EXTENSIONS: dict[str, str] = {
    "application/pdf": ".pdf",
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/tiff": ".tif",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/msword": ".doc",
}


def _extension_for(mime_type: str) -> str:
    """A suffix a browser and an operating system will both recognise.

    From a fixed table rather than from the uploaded name, which is not trustworthy,
    and rather than from mimetypes.guess_extension, which answers ".jpe" for a JPEG.
    """
    return _EXTENSIONS.get(mime_type, "")


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
    settings: SettingsDep,
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
    decision = await get_rate_limiter().check(
        "export", str(scope.user_id), limit=settings.rate_limit_export_per_minute
    )
    decision.raise_if_denied(what="exports")

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
    "/search",
    response_model=SearchResponse,
    summary="Find a certificate by number or by name",
)
async def search_certificates(
    session: SessionDep,
    scope: WorkspaceScopeDep,
    settings: SettingsDep,
    q: Annotated[
        str | None,
        Query(
            max_length=200,
            description=(
                "A certificate number, part of one, or a name. Numbers are matched "
                "first, exactly and then by prefix; names exactly and then by "
                "similarity."
            ),
        ),
    ] = None,
    certificate_type_id: Annotated[uuid.UUID | None, Query()] = None,
    batch_id: Annotated[
        uuid.UUID | None,
        Query(description="Only entries read from this batch."),
    ] = None,
    field: Annotated[
        str | None,
        Query(
            max_length=64,
            description=(
                "A configured field to search by name - any column the batch defines, "
                "not only the number and the names. Needs `field_value`."
            ),
        ),
    ] = None,
    field_value: Annotated[str | None, Query(max_length=200)] = None,
    name: Annotated[str | None, Query(max_length=200, description="Anyone named.")] = None,
    father_name: Annotated[str | None, Query(max_length=200)] = None,
    event_date_from: Annotated[dt.date | None, Query()] = None,
    event_date_to: Annotated[dt.date | None, Query()] = None,
    needs_review: Annotated[bool | None, Query()] = None,
    duplicates_only: Annotated[bool, Query()] = False,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    offset: Annotated[int, Query(ge=0, le=search_service.MAX_OFFSET)] = 0,
) -> SearchResponse:
    """Answers in tiers and says which tier answered.

    The tier is what lets the screen be honest: "no certificate with that number, but
    here are three people with that name" is a different answer from "here is the
    certificate", and a clerk needs to know which one they are looking at.
    """
    decision = await get_rate_limiter().check(
        "search", str(scope.user_id), limit=settings.rate_limit_search_per_minute
    )
    decision.raise_if_denied(what="searches")

    results = await search_service.search(
        session,
        workspace_id=scope.workspace_id,
        query=search_service.SearchQuery(
            text=q,
            certificate_type_id=certificate_type_id,
            batch_id=batch_id,
            field=field,
            field_value=field_value,
            name=name,
            father_name=father_name,
            event_date_from=event_date_from,
            event_date_to=event_date_to,
            needs_review=needs_review,
            duplicates_only=duplicates_only,
            limit=limit,
            offset=offset,
        ),
    )
    return SearchResponse(
        items=[
            SearchHitOut(
                certificate=CertificateSummary.model_validate(hit.certificate),
                # Already loaded on the row; no extra query. Bounded by the page size,
                # which is why this is on the search and not on the register browse.
                values={
                    str(name): str(value)
                    for name, value in (hit.certificate.values_jsonb or {}).items()
                    if value is not None
                },
                match=hit.kind,
                same_name_count=hit.same_name_count,
            )
            for hit in results.hits
        ],
        match=results.kind,
        total=results.total,
        limit=results.limit,
        offset=results.offset,
        has_more=results.has_more,
    )


@router.get(
    "/review-summary",
    response_model=ReviewSummary,
    summary="How much is waiting for a reviewer",
)
async def get_review_summary(session: SessionDep, scope: WorkspaceScopeDep) -> ReviewSummary:
    counts = await review_service.review_counts(session, workspace_id=scope.workspace_id)
    return ReviewSummary(
        needs_review=counts.needs_review, suspected_duplicates=counts.suspected_duplicates
    )


@router.post(
    "/{certificate_id}/approve",
    response_model=CertificateDetail,
    summary="Accept an entry as it stands",
    responses={
        403: {"description": "Approving is an operator's action."},
        409: {"description": "An unsettled duplicate has to be resolved first."},
    },
)
async def approve_certificate(
    certificate_id: uuid.UUID,
    payload: ApproveRequest,
    session: SessionDep,
    scope: WorkspaceScopeDep,
    audit: AuditContextDep,
) -> CertificateDetail:
    certificate = await review_service.approve_certificate(
        session, scope=scope, certificate_id=certificate_id, note=payload.note
    )
    await record_audit(
        session,
        AuditAction.EXTRACTION_APPROVED,
        audit,
        entity_type="certificate",
        entity_id=certificate.id,
    )
    await session.commit()
    return _detail(
        await certificate_service.load_detail(
            session, workspace_id=scope.workspace_id, certificate_id=certificate_id
        )
    )


@router.patch(
    "/{certificate_id}",
    response_model=CertificateDetail,
    summary="Correct what an entry says",
    responses={
        403: {"description": "Correcting is an operator's action."},
        409: {"description": "A superseded entry cannot be corrected."},
        422: {"description": "A field the schema does not define, or no reason given."},
    },
)
async def correct_certificate(
    certificate_id: uuid.UUID,
    payload: CorrectionRequest,
    session: SessionDep,
    scope: WorkspaceScopeDep,
    audit: AuditContextDep,
) -> CertificateDetail:
    """The previous values are kept, with who changed them and why."""
    existing = await certificate_service.get_certificate(
        session, workspace_id=scope.workspace_id, certificate_id=certificate_id
    )
    schema = await _schema_for(session, existing)
    certificate = await review_service.correct_certificate(
        session,
        scope=scope,
        certificate_id=certificate_id,
        schema=schema,
        values=payload.values,
        note=payload.note,
        approve=payload.approve,
    )
    await record_audit(
        session,
        AuditAction.CERTIFICATE_UPDATED,
        audit,
        entity_type="certificate",
        entity_id=certificate.id,
        # Field names only. The values are the personal data this system exists to
        # protect, and an audit log is read by more people than the register is.
        metadata={
            "field_names": sorted(payload.values),
            "count": len(payload.values),
        },
    )
    await session.commit()
    return _detail(
        await certificate_service.load_detail(
            session, workspace_id=scope.workspace_id, certificate_id=certificate_id
        )
    )


@router.post(
    "/{certificate_id}/void",
    response_model=CertificateDetail,
    summary="Cancel an entry without removing it",
    responses={403: {"description": "Only an administrator may void an entry."}},
)
async def void_certificate(
    certificate_id: uuid.UUID,
    payload: VoidRequest,
    session: SessionDep,
    scope: WorkspaceScopeDep,
    audit: AuditContextDep,
) -> CertificateDetail:
    """A voided entry stays readable and stays findable by its number.

    Somebody holding the certificate has to be told it was cancelled, and a missing
    record cannot tell them anything.
    """
    certificate = await review_service.void_certificate(
        session, scope=scope, certificate_id=certificate_id, note=payload.note
    )
    await record_audit(
        session,
        AuditAction.CERTIFICATE_VOIDED,
        audit,
        entity_type="certificate",
        entity_id=certificate.id,
    )
    await session.commit()
    return _detail(
        await certificate_service.load_detail(
            session, workspace_id=scope.workspace_id, certificate_id=certificate_id
        )
    )


@router.post(
    "/{certificate_id}/resolve-duplicate",
    response_model=list[CertificateSummary],
    summary="Settle whether two entries are one certificate",
    responses={
        400: {"description": "An entry cannot be a duplicate of itself."},
        404: {"description": "One of the entries is not in this register."},
    },
)
async def resolve_duplicate(
    certificate_id: uuid.UUID,
    payload: ResolveDuplicateRequest,
    session: SessionDep,
    scope: WorkspaceScopeDep,
    audit: AuditContextDep,
) -> list[CertificateSummary]:
    """Returns both entries, earlier first. Neither is deleted and no values are merged."""
    earlier, later = await review_service.resolve_duplicate(
        session,
        scope=scope,
        certificate_id=certificate_id,
        other_id=payload.other_id,
        same_certificate=payload.same_certificate,
        note=payload.note,
    )
    await record_audit(
        session,
        AuditAction.CERTIFICATE_DUPLICATE_RESOLVED,
        audit,
        entity_type="certificate",
        entity_id=earlier.id,
        metadata={
            "related_id": str(later.id),
            "reason_code": (
                "same_certificate" if payload.same_certificate else "distinct_certificates"
            ),
        },
    )
    await session.commit()
    return [CertificateSummary.model_validate(earlier), CertificateSummary.model_validate(later)]


@router.get(
    "/{certificate_id}/history",
    response_model=list[RevisionOut],
    summary="Everything that has happened to an entry",
)
async def list_history(
    certificate_id: uuid.UUID, session: SessionDep, scope: WorkspaceScopeDep
) -> list[RevisionOut]:
    """Oldest first, which is how a person reads a history."""
    revisions = await review_service.list_revisions(
        session, workspace_id=scope.workspace_id, certificate_id=certificate_id
    )
    return [RevisionOut.model_validate(revision) for revision in revisions]


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
        # Whether a duplicate queues work is the office's setting, not this route's.
        review=(
            await workspace_service.read_settings(session, workspace_id=scope.workspace_id)
        ).review,
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


@router.get(
    "/{certificate_id}/documents/{link_id}/content",
    response_class=StreamingResponse,
    summary="The scan behind an entry",
    responses={
        200: {"content": {"application/pdf": {}}},
        404: {"description": "No such entry, or no such document attached to it."},
    },
)
async def get_document_content(
    certificate_id: uuid.UUID,
    link_id: uuid.UUID,
    session: SessionDep,
    scope: WorkspaceScopeDep,
    audit: AuditContextDep,
) -> StreamingResponse:
    """Served through the API, never from a public URL.

    The bucket is private and stays private: a link that works without a session is a
    link that works for whoever finds it, and these are identity documents. So the
    bytes are streamed through this route, which checks the session, checks that the
    document is attached to an entry in *this* workspace, and records who looked.

    ``inline`` rather than ``attachment``: a clerk comparing a scan against a typed
    value wants it in the viewer beside the form, not in their downloads folder.
    """
    certificate = await certificate_service.get_certificate(
        session, workspace_id=scope.workspace_id, certificate_id=certificate_id
    )
    link = await session.scalar(
        select(CertificateDocument).where(
            CertificateDocument.id == link_id,
            CertificateDocument.certificate_id == certificate.id,
            CertificateDocument.workspace_id == scope.workspace_id,
        )
    )
    if link is None:
        raise NotFoundError("That document is not attached to this certificate.")

    document = await session.get(Document, link.document_id)
    if document is None or document.workspace_id != scope.workspace_id:
        raise NotFoundError("The document behind this entry is no longer available.")

    await record_audit(
        session,
        AuditAction.CERTIFICATE_DOCUMENT_VIEWED,
        audit,
        entity_type="certificate",
        entity_id=certificate.id,
    )
    await session.commit()

    body = get_object_storage().download_stream(document.storage_key)

    def chunks() -> Iterator[bytes]:
        """Streamed in blocks: a scanned register can be tens of megabytes."""
        try:
            while block := body.read(_DOWNLOAD_CHUNK):
                yield block
        finally:
            body.close()

    # The stored filename is what someone uploaded, so it is not used to name the
    # download. The certificate number is, which is also what a clerk would call it.
    safe_number = _FILENAME_UNSAFE.sub("-", certificate.certificate_number).strip("-_.")
    filename = f"{safe_number or 'certificate'}{_extension_for(document.mime_type)}"
    return StreamingResponse(
        chunks(),
        media_type=document.mime_type,
        headers={
            "Content-Disposition": f'inline; filename="{filename}"',
            "Content-Length": str(document.byte_size),
            # Private: this is somebody's identity document, and a shared cache
            # holding it would serve it to the next person through the proxy.
            "Cache-Control": "private, max-age=0, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.post(
    "/{certificate_id}/documents/{link_id}/replace",
    response_model=list[DocumentLink],
    summary="Attach a better scan, keeping the old one",
    responses={
        403: {"description": "Replacing a scan is an operator's action."},
        404: {"description": "No such entry, link, or document in this workspace."},
        409: {"description": "That document is already attached to this entry."},
    },
)
async def replace_document(
    certificate_id: uuid.UUID,
    link_id: uuid.UUID,
    payload: DocumentReplacement,
    session: SessionDep,
    scope: WorkspaceScopeDep,
    audit: AuditContextDep,
) -> list[DocumentLink]:
    """A re-scan does not delete the scan it improves on.

    Registers get photographed twice: the first pass is crooked or the flash washed out
    half the page, and somebody scans it again properly. The new file becomes the
    certificate's scan and the old one is marked superseded rather than removed, because
    a value in the register was read from the old one and "why does it say that" has to
    stay answerable against the image it was actually read from.

    Returns both links, the new one first.
    """
    scope.require(UserRole.OPERATOR)

    certificate = await certificate_service.get_certificate(
        session, workspace_id=scope.workspace_id, certificate_id=certificate_id
    )
    replaced, added = await certificate_service.replace_document(
        session,
        certificate=certificate,
        link_id=link_id,
        document_id=payload.document_id,
        unit_id=payload.unit_id,
        page_start=payload.page_start,
        page_end=payload.page_end,
        note=payload.note,
        actor_id=scope.user_id,
    )
    certificate.record_version += 1
    certificate.updated_by = scope.user_id
    certificate_service.record_revision(
        session,
        certificate,
        action=RevisionAction.DOCUMENT_REPLACED,
        note=payload.note,
        actor_id=scope.user_id,
    )
    await record_audit(
        session,
        AuditAction.CERTIFICATE_DOCUMENT_LINKED,
        audit,
        entity_type="certificate",
        entity_id=certificate.id,
        metadata={"related_id": str(replaced.id), "reason_code": "document_replaced"},
    )
    await session.commit()
    return [DocumentLink.model_validate(added), DocumentLink.model_validate(replaced)]
