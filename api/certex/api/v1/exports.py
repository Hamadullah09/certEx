"""Download routes: the file the whole system exists to produce."""

from __future__ import annotations

import io
import uuid
import zipfile
from collections.abc import AsyncIterator
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Query, Response
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from certex.core.audit import record_audit
from certex.core.deps import AuditContextDep, SessionDep, WorkspaceScope, WorkspaceScopeDep
from certex.db.models import Batch
from certex.enums import AuditAction, CertificateType, ExportFormat, ReviewStatus
from certex.export.columns import build_column_plan
from certex.export.rows import render_row
from certex.export.writers import (
    DELIMITERS,
    write_csv,
    write_csv_stream,
    write_json,
    write_json_stream,
    write_xlsx,
)
from certex.logging_setup import get_logger
from certex.schemas.exports import ExportPreview
from certex.services import batch_service, export_service, row_service
from certex.services.schema_service import schema_for_batch

__all__ = ["router"]

logger = get_logger(__name__)
router = APIRouter(prefix="/batches", tags=["exports"])

ReviewStatusParam = Annotated[ReviewStatus | None, Query(alias="filter[status]")]
TypeParam = Annotated[CertificateType | None, Query(alias="filter[type]")]
FlagParam = Annotated[str | None, Query(alias="filter[flag]", max_length=64)]
SearchParam = Annotated[str | None, Query(max_length=200)]
DelimiterParam = Annotated[str, Query(description="comma, semicolon, tab or pipe.")]
ExtrasParam = Annotated[
    bool,
    Query(
        description=(
            "Also include every other labelled value the forms carried, as (extra) "
            "columns. Always on for a batch that defined no columns of its own, since "
            "there is nothing else to export."
        )
    ),
]

_MEDIA_TYPES = {
    ExportFormat.CSV: "text/csv; charset=utf-8",
    ExportFormat.JSON: "application/json; charset=utf-8",
    ExportFormat.XLSX: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}


def _disposition(filename: str) -> str:
    """Attachment header that survives a non-ASCII batch name."""
    return f"attachment; filename=\"{filename}\"; filename*=UTF-8''{quote(filename)}"


@router.get(
    "/{batch_id}/export/preview",
    response_model=ExportPreview,
    summary="What a download would contain, before downloading it",
)
async def preview_export(
    batch_id: uuid.UUID,
    session: SessionDep,
    scope: WorkspaceScopeDep,
    review_status: ReviewStatusParam = None,
    certificate_type: TypeParam = None,
    flag: FlagParam = None,
    search: SearchParam = None,
    include_confidence: Annotated[bool, Query()] = False,
    include_snippet: Annotated[bool, Query()] = False,
    include_extras: ExtrasParam = False,
) -> ExportPreview:
    """The row and column counts, and how many rows nobody has reviewed yet.

    The download bar shows this first, so an operator is never surprised by a file that
    turns out to hold two hundred unchecked rows.
    """
    batch = await batch_service.get_batch(session, scope=scope, batch_id=batch_id)
    filters = export_service.ExportFilters(
        review_status=review_status,
        certificate_type=certificate_type,
        flag=flag,
        search=search,
    )
    plan = await export_service.plan_export(
        session,
        scope=scope,
        batch=batch,
        filters=filters,
        include_confidence=include_confidence,
        include_snippet=include_snippet,
        include_extras=include_extras,
    )
    return ExportPreview(
        batch_id=batch.id,
        row_count=plan.row_count,
        column_count=len(plan.columns),
        unreviewed_count=plan.unreviewed_count,
        certificate_types=plan.certificate_types,
        columns=plan.columns.headers,
        filename=export_service.export_filename(batch, extension="csv"),
    )


@router.get(
    "/{batch_id}/export",
    summary="Download the batch as CSV, Excel or JSON",
    responses={
        200: {"description": "The export, streamed."},
        404: {"description": "No such batch in this workspace."},
    },
)
async def export_batch(
    batch_id: uuid.UUID,
    session: SessionDep,
    scope: WorkspaceScopeDep,
    audit: AuditContextDep,
    export_format: Annotated[ExportFormat, Query(alias="format")] = ExportFormat.CSV,
    review_status: ReviewStatusParam = None,
    certificate_type: TypeParam = None,
    flag: FlagParam = None,
    search: SearchParam = None,
    include_confidence: Annotated[bool, Query()] = False,
    include_snippet: Annotated[bool, Query()] = False,
    include_extras: ExtrasParam = False,
    delimiter: DelimiterParam = "comma",
    include_bom: Annotated[
        bool, Query(description="Byte-order mark, so Excel on Windows reads Urdu correctly.")
    ] = True,
    per_type: Annotated[
        bool, Query(description="One file per certificate type, delivered as a zip.")
    ] = False,
) -> Response:
    """Stream the export, and record that certificate details left the system."""
    batch = await batch_service.get_batch(session, scope=scope, batch_id=batch_id)
    filters = export_service.ExportFilters(
        review_status=review_status,
        certificate_type=certificate_type,
        flag=flag,
        search=search,
    )
    plan = await export_service.plan_export(
        session,
        scope=scope,
        batch=batch,
        filters=filters,
        include_confidence=include_confidence,
        include_snippet=include_snippet,
        include_extras=include_extras,
    )
    separator = DELIMITERS.get(delimiter, ",")

    await record_audit(
        session,
        AuditAction.EXPORT_REQUESTED,
        audit,
        entity_type="batch",
        entity_id=batch.id,
        metadata={
            "format": export_format.value,
            "count": plan.row_count,
            "column_count": len(plan.columns),
            "review_status": review_status.value if review_status else None,
        },
    )
    await export_service.record_export(
        session,
        scope=scope,
        batch=batch,
        export_format=export_format,
        plan=plan,
        filters=filters,
    )
    await session.commit()

    if per_type:
        return await _per_type_zip(
            session,
            scope=scope,
            batch=batch,
            plan=plan,
            filters=filters,
            export_format=export_format,
            separator=separator,
            include_bom=include_bom,
            include_confidence=include_confidence,
            include_snippet=include_snippet,
            include_extras=include_extras,
        )

    extension = export_format.value
    filename = export_service.export_filename(batch, extension=extension)
    headers = {"Content-Disposition": _disposition(filename), "X-Row-Count": str(plan.row_count)}

    if export_format is ExportFormat.XLSX:
        rows = [
            row
            async for row in export_service.stream_rows(
                session, scope=scope, batch_id=batch_id, plan=plan, filters=filters
            )
        ]
        payload = write_xlsx(plan.columns, rows)
        return Response(content=payload, media_type=_MEDIA_TYPES[export_format], headers=headers)

    def chunks() -> AsyncIterator[str]:
        """Rows go straight from the database into the response.

        Nothing is collected first: a batch can hold hundreds of thousands of rows,
        and materialising them to hand to the synchronous writer would hold the lot
        in memory for the length of the download.
        """
        rows = export_service.stream_rows(
            session, scope=scope, batch_id=batch_id, plan=plan, filters=filters
        )
        if export_format is ExportFormat.CSV:
            return write_csv_stream(
                plan.columns, rows, delimiter=separator, include_bom=include_bom
            )
        return write_json_stream(plan.columns, rows)

    return StreamingResponse(chunks(), media_type=_MEDIA_TYPES[export_format], headers=headers)


async def _per_type_zip(
    session: AsyncSession,
    *,
    scope: WorkspaceScope,
    batch: Batch,
    plan: export_service.ExportPlan,
    filters: export_service.ExportFilters,
    export_format: ExportFormat,
    separator: str,
    include_bom: bool,
    include_confidence: bool,
    include_snippet: bool,
    include_extras: bool,
) -> Response:
    """One file per certificate type, zipped - for offices that file them separately."""
    archive = io.BytesIO()
    batch_id = batch.id
    # The same column rules as the single-file export: a batch that defined its own
    # columns gets those in every file of the zip, not the built-in ones.
    configured = batch.schema_version_id is not None
    schemas = [await schema_for_batch(session, batch)] if configured else []
    with_extras = include_extras or not configured
    extras = (
        await export_service.extra_field_names(session, batch_id=batch_id) if with_extras else []
    )

    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
        for certificate_type in export_service.types_in(plan):
            narrowed = export_service.ExportFilters(
                review_status=filters.review_status,
                certificate_type=certificate_type,
                flag=filters.flag,
                search=filters.search,
            )
            columns = build_column_plan(
                [certificate_type],
                schemas=schemas,
                extra_field_names=extras,
                include_extras=with_extras,
                include_confidence=include_confidence,
                include_snippet=include_snippet,
            )
            records, _cursor, _total = await row_service.list_rows(
                session,
                scope=scope,
                batch_id=batch_id,
                review_status=narrowed.review_status,
                certificate_type=certificate_type,
                flag=narrowed.flag,
                search=narrowed.search,
                limit=10_000,
            )
            rows = [render_row(record, columns) for record in records]
            name = export_service.export_filename(batch, extension=export_format.value).replace(
                "certificates_", f"certificates_{certificate_type.value.lower()}_"
            )

            if export_format is ExportFormat.XLSX:
                bundle.writestr(name, write_xlsx(columns, rows))
            elif export_format is ExportFormat.JSON:
                bundle.writestr(name, "".join(write_json(columns, rows)))
            else:
                text = "".join(
                    write_csv(columns, rows, delimiter=separator, include_bom=include_bom)
                )
                bundle.writestr(name, text.encode("utf-8"))

    filename = export_service.export_filename(batch, extension="zip")
    return Response(
        content=archive.getvalue(),
        media_type="application/zip",
        headers={
            "Content-Disposition": _disposition(filename),
            "X-Row-Count": str(plan.row_count),
        },
    )
