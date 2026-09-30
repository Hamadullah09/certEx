"""Stage 6 - read one certificate's fields and write the row.

This is where a unit becomes data. The reading itself lives in
:mod:`certex.pipeline.extract.extractor`, so it can be measured against hand-labelled
documents without a database; this stage does the bookkeeping around it - load the
pages, find the office's template, write the row, hand the unit to validation.

Every stored value carries three things besides itself: how confident that reading is,
which layer produced it, and where on the page it came from. Without those, a reviewer
facing a wrong value has nothing to check it against, and a corrected value teaches
nothing. Values found under labels this schema has no field for are kept too, in
``extra_fields``, because an office that prints "Blood Group" on its certificates
should not lose it.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select

from certex.config import Settings
from certex.db.base import utcnow
from certex.db.models import CertificateUnit, Document, Extraction, PageText, Template
from certex.db.session import session_scope
from certex.enums import (
    CertificateType,
    ExtractionMethod,
    ReviewStatus,
    UnitStatus,
    ValidationFlag,
)
from certex.fields import FIELD_SCHEMA_VERSION
from certex.logging_setup import get_logger, safe_error
from certex.pipeline.dispatch import TASK_VALIDATE_UNIT, Dispatch, enqueue
from certex.pipeline.extract.candidates import Candidate
from certex.pipeline.extract.extractor import extract_fields
from certex.pipeline.extract.rules import UnitPage
from certex.pipeline.extract.templates import fingerprint_pages
from certex.schemas.layout import PageLayout
from certex.schemas.template import TemplateRules
from certex.services.row_service import manual_fields
from certex.services.schema_service import schema_for_batch_sync

__all__ = ["ExtractStageResult", "run_extract_unit_stage"]

logger = get_logger(__name__)

_EXTRACTABLE: tuple[UnitStatus, ...] = (UnitStatus.CLASSIFIED, UnitStatus.EXTRACTED)
"""EXTRACTED is included so a re-run after a correction replaces the row."""


@dataclass(frozen=True, slots=True)
class ExtractStageResult:
    unit_id: uuid.UUID
    ran: bool
    field_count: int = 0
    extra_field_count: int = 0
    template_used: bool = False


@dataclass(slots=True)
class _UnitInput:
    """Everything the extraction layers read, loaded in one transaction."""

    workspace_id: uuid.UUID
    batch_id: uuid.UUID
    certificate_type: CertificateType
    pages: list[UnitPage] = field(default_factory=list)
    ocr_used: bool = False
    ocr_mean_confidence: float | None = None
    language: str | None = None


def _load(unit_id: uuid.UUID) -> _UnitInput | None:
    with session_scope() as session:
        unit = session.get(CertificateUnit, unit_id)
        if unit is None or unit.status not in _EXTRACTABLE:
            return None
        document = session.get(Document, unit.document_id)
        if document is None:  # pragma: no cover - the cascade deletes units with it
            return None
        rows = list(
            session.scalars(
                select(PageText)
                .where(
                    PageText.document_id == unit.document_id,
                    PageText.page_number >= unit.page_start,
                    PageText.page_number <= unit.page_end,
                )
                .order_by(PageText.page_number)
            )
        )

        pages: list[UnitPage] = []
        confidences: list[float] = []
        languages: list[str] = []
        for row in rows:
            try:
                layout = PageLayout.model_validate(row.layout_blocks_jsonb)
            except ValueError as exc:
                logger.warning(
                    "extract.layout_unreadable",
                    page_number=row.page_number,
                    error_type=safe_error(exc),
                )
                continue
            pages.append(UnitPage(page_number=row.page_number, layout=layout))
            if row.ocr_mean_confidence is not None:
                confidences.append(row.ocr_mean_confidence)
            if row.language:
                languages.append(row.language)

        return _UnitInput(
            workspace_id=document.workspace_id,
            batch_id=unit.batch_id,
            certificate_type=unit.certificate_type,
            pages=pages,
            ocr_used=any(row.needed_ocr for row in rows),
            ocr_mean_confidence=(
                round(sum(confidences) / len(confidences), 2) if confidences else None
            ),
            language=languages[0] if languages else None,
        )


def _template_for(
    workspace_id: uuid.UUID, fingerprint: str
) -> tuple[uuid.UUID, TemplateRules] | None:
    """The active template for this form, if this office has taught us one."""
    with session_scope() as session:
        template = session.scalar(
            select(Template).where(
                Template.workspace_id == workspace_id,
                Template.fingerprint == fingerprint,
                Template.is_active.is_(True),
            )
        )
        if template is None:
            return None
        try:
            rules = TemplateRules.model_validate(template.rules_jsonb)
        except ValueError as exc:
            logger.warning("extract.template_unreadable", error_type=safe_error(exc))
            return None
        return template.id, rules


def _write_row(
    unit_id: uuid.UUID,
    data: _UnitInput,
    merged: dict[str, Candidate],
    extras: dict[str, Candidate],
    *,
    template_id: uuid.UUID | None,
    flags: tuple[ValidationFlag, ...] = (),
) -> bool:
    """Replace this unit's row. False when another run claimed the unit first."""
    fields: dict[str, Any] = {name: candidate.value for name, candidate in merged.items()}
    confidences: dict[str, Any] = {name: candidate.confidence for name, candidate in merged.items()}
    methods: dict[str, Any] = {name: candidate.method.value for name, candidate in merged.items()}
    sources: dict[str, Any] = {
        name: candidate.source.as_json() for name, candidate in merged.items()
    }
    extra_values: dict[str, Any] = {name: candidate.value for name, candidate in extras.items()}

    with session_scope() as session:
        unit = session.get(CertificateUnit, unit_id)
        if unit is None or unit.status not in _EXTRACTABLE:
            return False
        row = session.scalar(select(Extraction).where(Extraction.unit_id == unit_id))
        if row is None:
            row = Extraction(
                unit_id=unit_id, batch_id=data.batch_id, workspace_id=data.workspace_id
            )
            session.add(row)
        else:
            # Whatever a person typed survives a re-run: a reviewer whose corrections are
            # silently undone stops reviewing.
            for name, typed in manual_fields(row).items():
                fields[name] = typed
                confidences[name] = 1.0
                methods[name] = ExtractionMethod.MANUAL.value
                previous = row.field_sources_jsonb.get(name)
                sources.setdefault(name, previous if isinstance(previous, dict) else {})
        row.schema_version = FIELD_SCHEMA_VERSION
        row.certificate_type = data.certificate_type
        row.fields_jsonb = fields
        row.field_confidences_jsonb = confidences
        row.field_methods_jsonb = methods
        row.field_sources_jsonb = sources
        row.extra_fields_jsonb = extra_values
        # What the reader itself wants said - a value the fallback could not verify, a
        # fallback that was unavailable. Validation adds its own flags on top of these
        # rather than replacing them, so a warning raised here is not lost.
        row.flags_jsonb = [flag.value for flag in flags]
        # Confidence and routing are decided by validation, which reads these values
        # and the flags they raise; until then the row is explicitly unreviewed.
        row.row_confidence = 0.0
        row.review_status = ReviewStatus.NEEDS_REVIEW
        row.ocr_used = data.ocr_used
        row.ocr_mean_confidence = data.ocr_mean_confidence
        row.detected_language = data.language
        row.template_id = template_id
        row.processed_at = utcnow()
        unit.status = UnitStatus.EXTRACTED

        if template_id is not None:
            template = session.get(Template, template_id)
            if template is not None:
                template.hit_count += 1
    return True


def run_extract_unit_stage(
    unit_id: uuid.UUID,
    *,
    settings: Settings | None = None,
    dispatch: Dispatch = enqueue,
) -> ExtractStageResult:
    """Read one certificate's fields and write its row."""
    del settings  # the extraction layers have no settings of their own yet

    data = _load(unit_id)
    if data is None:
        return ExtractStageResult(unit_id=unit_id, ran=False)

    template_rules: TemplateRules | None = None
    template_id: uuid.UUID | None = None
    if data.pages:
        match = _template_for(data.workspace_id, fingerprint_pages(data.pages))
        if match is not None:
            template_id, template_rules = match

    # The batch pinned the schema when it was created, so re-running this stage
    # months later reads the same fields it read the first time.
    with session_scope() as session:
        schema = schema_for_batch_sync(session, data.batch_id)

    extracted = extract_fields(
        data.pages,
        certificate_type=data.certificate_type,
        template_rules=template_rules,
        schema=schema,
        # Passing the workspace is what lets the model fallback run: it scopes the
        # answer cache, and a layer that costs money should not be reachable from a
        # code path that has no office to bill it to.
        workspace_id=data.workspace_id,
    )
    if not _write_row(
        unit_id,
        data,
        extracted.fields,
        extracted.extras,
        template_id=template_id if extracted.template_used else None,
        flags=extracted.flags,
    ):
        return ExtractStageResult(unit_id=unit_id, ran=False)

    logger.info(
        "pipeline.unit_extracted",
        unit_id=str(unit_id),
        count=len(extracted.fields),
        certificate_type=data.certificate_type.value,
        template=extracted.template_used,
    )
    dispatch(TASK_VALIDATE_UNIT, {"unit_id": str(unit_id)})
    return ExtractStageResult(
        unit_id=unit_id,
        ran=True,
        field_count=len(extracted.fields),
        extra_field_count=len(extracted.extras),
        template_used=extracted.template_used,
    )
