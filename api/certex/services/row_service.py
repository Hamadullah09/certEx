"""Reading and correcting extracted rows.

Two things happen here that decide whether review is worth doing.

**A correction is the last word.** When a person types a value, it is stored with method
``manual`` and full confidence, and it is never overwritten by a later automatic run.
Anything less and a reviewer's work is silently undone the next time a batch is
reprocessed - which is the fastest way to make people stop reviewing.

**A corrected row is re-checked, not just re-saved.** Changing a date of death can fix a
contradiction or create one, so validation, the score and the routing all run again on
the new values. A row a reviewer has fixed should stop being flagged; a row they have
broken should say so immediately.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import Select, Text, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from certex.config import Settings
from certex.core.deps import WorkspaceScope
from certex.core.errors import BadRequestError, NotFoundError
from certex.db.base import utcnow
from certex.db.models import CertificateUnit, Document, Extraction, FieldCorrection
from certex.enums import (
    CertificateType,
    ExtractionMethod,
    ReviewStatus,
    UnitStatus,
    UserRole,
)
from certex.fields import FieldSchema, builtin_schema
from certex.logging_setup import get_logger
from certex.pipeline.extract.values import normalize_field_value
from certex.pipeline.validate.confidence import route_row, score_row
from certex.pipeline.validate.rules import RowFacts, validate_row
from certex.schemas.common import Cursor
from certex.schemas.rows import FieldValue, RowCorrection, RowDetail, RowSummary

__all__ = [
    "RowRecord",
    "correct_row",
    "get_row",
    "list_rows",
    "mark_reprocessing",
    "to_detail",
    "to_summary",
]

logger = get_logger(__name__)


class RowRecord:
    """An extraction with the unit and file it belongs to."""

    __slots__ = ("document", "extraction", "serial_no", "unit")

    def __init__(
        self,
        extraction: Extraction,
        unit: CertificateUnit,
        document: Document,
        serial_no: int = 0,
    ) -> None:
        self.extraction = extraction
        self.unit = unit
        self.document = document
        self.serial_no = serial_no


def _scoped(scope: WorkspaceScope) -> Select[tuple[Extraction, CertificateUnit, Document]]:
    return (
        select(Extraction, CertificateUnit, Document)
        .join(CertificateUnit, CertificateUnit.id == Extraction.unit_id)
        .join(Document, Document.id == CertificateUnit.document_id)
        .where(Extraction.workspace_id == scope.workspace_id)
    )


def _filtered(
    query: Select[tuple[Extraction, CertificateUnit, Document]],
    *,
    batch_id: uuid.UUID | None,
    document_id: uuid.UUID | None,
    review_status: ReviewStatus | None,
    certificate_type: CertificateType | None,
    flag: str | None,
    search: str | None,
) -> Select[tuple[Extraction, CertificateUnit, Document]]:
    if batch_id is not None:
        query = query.where(Extraction.batch_id == batch_id)
    if document_id is not None:
        query = query.where(CertificateUnit.document_id == document_id)
    if review_status is not None:
        query = query.where(Extraction.review_status == review_status)
    if certificate_type is not None:
        query = query.where(Extraction.certificate_type == certificate_type)
    if flag:
        # Containment over the JSONB array, which the GIN index serves.
        query = query.where(Extraction.flags_jsonb.contains([flag]))
    if search:
        needle = f"%{search.strip()}%"
        query = query.where(
            or_(
                Extraction.fields_jsonb.cast(Text).ilike(needle),
                Document.original_filename.ilike(needle),
            )
        )
    return query


async def list_rows(
    session: AsyncSession,
    *,
    scope: WorkspaceScope,
    batch_id: uuid.UUID | None = None,
    document_id: uuid.UUID | None = None,
    review_status: ReviewStatus | None = None,
    certificate_type: CertificateType | None = None,
    flag: str | None = None,
    search: str | None = None,
    limit: int = 50,
    cursor: Cursor | None = None,
    include_total: bool = False,
) -> tuple[list[RowRecord], Cursor | None, int | None]:
    """Rows in the order they were produced: by file, then by page."""
    query = _filtered(
        _scoped(scope),
        batch_id=batch_id,
        document_id=document_id,
        review_status=review_status,
        certificate_type=certificate_type,
        flag=flag,
        search=search,
    )

    total: int | None = None
    if include_total:
        counting = query.with_only_columns(func.count()).order_by(None)
        total = int(await session.scalar(counting) or 0)

    if cursor is not None:
        query = query.where(
            or_(
                Extraction.created_at > cursor.created_at,
                (Extraction.created_at == cursor.created_at) & (Extraction.id > cursor.id),
            )
        )

    result = await session.execute(
        query.order_by(Extraction.created_at, Extraction.id).limit(limit + 1)
    )
    rows = [RowRecord(extraction, unit, document) for extraction, unit, document in result.all()]

    next_cursor: Cursor | None = None
    if len(rows) > limit:
        rows = rows[:limit]
        last = rows[-1].extraction
        next_cursor = Cursor(created_at=last.created_at, id=last.id)

    await _number_rows(session, rows, batch_id=batch_id)
    return rows, next_cursor, total


async def _number_rows(
    session: AsyncSession, rows: Sequence[RowRecord], *, batch_id: uuid.UUID | None
) -> None:
    """Give each row its serial number within the batch.

    The serial is the row's position in the batch, counting from one - the first column
    of the CSV, and how an operator refers to a row when they telephone about it. It is
    computed rather than stored so that it stays correct when a unit is split or merged.
    """
    if not rows:
        return
    scope_batch = batch_id or rows[0].extraction.batch_id
    ordered = await session.scalars(
        select(Extraction.id)
        .where(Extraction.batch_id == scope_batch)
        .order_by(Extraction.created_at, Extraction.id)
    )
    positions = {row_id: index + 1 for index, row_id in enumerate(ordered)}
    for row in rows:
        row.serial_no = positions.get(row.extraction.id, 0)


async def get_row(session: AsyncSession, *, scope: WorkspaceScope, row_id: uuid.UUID) -> RowRecord:
    result = await session.execute(_scoped(scope).where(Extraction.id == row_id))
    found = result.first()
    if found is None:
        raise NotFoundError(
            "No such row in this workspace.",
            remediation="Refresh the results to see what still exists.",
        )
    extraction, unit, document = found
    record = RowRecord(extraction, unit, document)
    await _number_rows(session, [record], batch_id=extraction.batch_id)
    return record


def _schema_of(row: Extraction) -> FieldSchema:
    """The fields a stored row should be read against.

    Rows carry their certificate type; a batch may also pin a schema version. The
    pinned version is threaded in by the caller where it matters - here the
    built-in definition is the safe default, because a row's own keys are what
    actually decide what is displayed.
    """
    return builtin_schema(row.certificate_type)


def to_summary(record: RowRecord) -> RowSummary:
    row = record.extraction
    return RowSummary(
        id=row.id,
        unit_id=record.unit.id,
        document_id=record.document.id,
        batch_id=row.batch_id,
        serial_no=record.serial_no,
        file_name=record.document.original_filename,
        page_start=record.unit.page_start,
        page_end=record.unit.page_end,
        certificate_type=row.certificate_type,
        review_status=row.review_status,
        row_confidence=row.row_confidence,
        flags=[str(flag) for flag in row.flags_jsonb],
        fields={name: _as_text(value) for name, value in row.fields_jsonb.items()},
        field_confidences={
            name: float(value)
            for name, value in row.field_confidences_jsonb.items()
            if isinstance(value, int | float)
        },
        extra_fields={name: str(value) for name, value in row.extra_fields_jsonb.items()},
        ocr_used=row.ocr_used,
        detected_language=row.detected_language,
        reviewed_at=row.reviewed_at,
        updated_at=row.updated_at,
    )


def to_detail(record: RowRecord) -> RowDetail:
    """The row with every field of its type, whether or not it was found."""
    row = record.extraction
    summary = to_summary(record)
    issues = [
        {str(key): str(value) for key, value in issue.items()}
        for issue in row.field_issues_jsonb
        if isinstance(issue, dict)
    ]
    by_field: dict[str, list[dict[str, str]]] = {}
    for issue in issues:
        by_field.setdefault(issue.get("field", ""), []).append(issue)

    values: list[FieldValue] = []
    for spec in _schema_of(row).fields:
        source = row.field_sources_jsonb.get(spec.name)
        source_map = source if isinstance(source, dict) else {}
        field_issues = by_field.get(spec.name, [])
        values.append(
            FieldValue(
                name=spec.name,
                value=_as_text(row.fields_jsonb.get(spec.name)),
                confidence=_as_float(row.field_confidences_jsonb.get(spec.name)),
                method=_as_text(row.field_methods_jsonb.get(spec.name)),
                page_number=_as_int(source_map.get("page_number")),
                bbox=_as_bbox(source_map.get("bbox")),
                snippet=_as_text(source_map.get("snippet")),
                label=_as_text(source_map.get("label")),
                flags=[issue["flag"] for issue in field_issues if "flag" in issue],
                issues=[issue["detail"] for issue in field_issues if "detail" in issue],
            )
        )

    return RowDetail(
        **summary.model_dump(),
        values=values,
        issues=issues,
        page_numbers=list(range(record.unit.page_start, record.unit.page_end + 1)),
    )


def _as_text(value: object) -> str | None:
    return None if value is None else str(value)


def _as_float(value: object) -> float | None:
    return float(value) if isinstance(value, int | float) else None


def _as_int(value: object) -> int | None:
    return int(value) if isinstance(value, int) else None


def _as_bbox(value: object) -> dict[str, float] | None:
    if not isinstance(value, dict):
        return None
    try:
        return {key: float(value[key]) for key in ("x0", "y0", "x1", "y1")}
    except (KeyError, TypeError, ValueError):  # pragma: no cover - written by this system
        return None


async def correct_row(
    session: AsyncSession,
    *,
    scope: WorkspaceScope,
    row_id: uuid.UUID,
    correction: RowCorrection,
    settings: Settings,
) -> RowRecord:
    """Apply a reviewer's edits, record them, and re-check the row."""
    scope.require(UserRole.OPERATOR)
    record = await get_row(session, scope=scope, row_id=row_id)
    row = record.extraction

    known = _schema_of(row).by_name
    edits = correction.cleaned()
    unknown = sorted(set(edits) - set(known) - set(row.extra_fields_jsonb))
    if unknown:
        raise BadRequestError(
            f"This certificate has no field called {unknown[0]!r}.",
            title="Unknown field",
            remediation="Refresh the row: its certificate type may have changed.",
        )

    fields = dict(row.fields_jsonb)
    methods = dict(row.field_methods_jsonb)
    confidences = dict(row.field_confidences_jsonb)
    extras = dict(row.extra_fields_jsonb)

    for name, raw in edits.items():
        spec = known.get(name)
        if spec is None:
            # An extra field: stored as typed, since the schema has no kind for it.
            _record_correction(session, row, name, _as_text(extras.get(name)), raw, scope)
            if raw is None:
                extras.pop(name, None)
            else:
                extras[name] = raw
            continue

        value = normalize_field_value(raw, spec.kind)[0] if raw else None
        _record_correction(session, row, name, _as_text(fields.get(name)), value, scope)
        if value is None:
            fields.pop(name, None)
            methods.pop(name, None)
            confidences.pop(name, None)
            continue
        fields[name] = value
        # A person's word, and never overwritten by a later automatic run.
        methods[name] = ExtractionMethod.MANUAL.value
        confidences[name] = 1.0

    row.fields_jsonb = fields
    row.field_methods_jsonb = methods
    row.field_confidences_jsonb = confidences
    row.extra_fields_jsonb = extras

    _revalidate(row, record.unit, settings=settings)
    if correction.approve:
        row.review_status = ReviewStatus.MANUALLY_APPROVED
    if edits or correction.approve:
        row.reviewed_by = scope.user_id
        row.reviewed_at = utcnow()

    await session.flush()
    logger.info(
        "rows.corrected",
        row_id=str(row_id),
        count=len(edits),
        review_status=row.review_status.value,
    )
    return record


def _record_correction(
    session: AsyncSession,
    row: Extraction,
    field_name: str,
    old_value: str | None,
    new_value: str | None,
    scope: WorkspaceScope,
) -> None:
    """Keep every edit, so templates can be learned from them and mistakes traced."""
    if old_value == new_value:
        return
    session.add(
        FieldCorrection(
            extraction_id=row.id,
            field_name=field_name,
            old_value=old_value,
            new_value=new_value,
            corrected_by=scope.user_id,
        )
    )


def _revalidate(row: Extraction, unit: CertificateUnit, *, settings: Settings) -> None:
    """Re-check, re-score and re-route a row after its values changed."""
    values = {name: str(value) for name, value in row.fields_jsonb.items()}
    confidences = {
        name: float(value)
        for name, value in row.field_confidences_jsonb.items()
        if isinstance(value, int | float)
    }
    methods = {name: str(value) for name, value in row.field_methods_jsonb.items()}
    printed = {
        name: str(source.get("snippet", ""))
        for name, source in row.field_sources_jsonb.items()
        if isinstance(source, dict)
    }

    outcome = validate_row(
        RowFacts(
            certificate_type=row.certificate_type,
            values=values,
            printed=printed,
            field_confidences=confidences,
            type_confidence=unit.type_confidence,
            boundary_confidence=unit.boundary_confidence,
            ocr_used=row.ocr_used,
            ocr_mean_confidence=row.ocr_mean_confidence,
        ),
        min_field_confidence=settings.confidence_review_floor,
    )
    confidence = score_row(
        certificate_type=row.certificate_type,
        values=values,
        field_confidences=confidences,
        field_methods=methods,
        outcome=outcome,
        type_confidence=unit.type_confidence,
    )
    flag_values: list[object] = [flag.value for flag in outcome.flags]
    issues: list[object] = list(outcome.as_json())
    row.flags_jsonb = flag_values
    row.field_issues_jsonb = issues
    row.row_confidence = confidence
    row.review_status = route_row(
        confidence=confidence,
        outcome=outcome,
        auto_approve_at=settings.confidence_auto_approve,
        review_floor=settings.confidence_review_floor,
    )


async def mark_reprocessing(
    session: AsyncSession, *, scope: WorkspaceScope, row_id: uuid.UUID
) -> RowRecord:
    """Send one row back to be read again, keeping the corrections a person made."""
    scope.require(UserRole.OPERATOR)
    record = await get_row(session, scope=scope, row_id=row_id)
    record.unit.status = UnitStatus.CLASSIFIED
    await session.flush()
    logger.info("rows.reprocess_requested", row_id=str(row_id), unit_id=str(record.unit.id))
    return record


def manual_fields(row: Extraction) -> dict[str, str]:
    """Values a person typed, which an automatic re-run must not overwrite."""
    return {
        name: str(row.fields_jsonb.get(name, ""))
        for name, method in row.field_methods_jsonb.items()
        if method == ExtractionMethod.MANUAL.value
    }
