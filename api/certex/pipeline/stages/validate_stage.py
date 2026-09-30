"""Stage 8 - check a row, score it, and route it.

This is the last thing that happens to a row before a person sees it, and the first
point at which the system commits to an opinion: is this record usable as it stands?

The answer is written where the review screen and the export both read it - the flags,
the score, and the review status - and the unit is marked complete. When the last unit of
a document is done, the document is finished too, which is what advances the batch.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select

from certex.config import Settings, get_settings
from certex.db.base import utcnow
from certex.db.models import CertificateUnit, Extraction
from certex.db.session import session_scope
from certex.enums import (
    ReviewStatus,
    UnitStatus,
    ValidationFlag,
)
from certex.logging_setup import get_logger
from certex.pipeline.dispatch import TASK_FINALIZE_DOCUMENT, Dispatch, enqueue
from certex.pipeline.validate.confidence import route_row, score_row
from certex.pipeline.validate.rules import RowFacts, validate_row
from certex.services.schema_service import schema_for_batch_sync

__all__ = ["ValidateStageResult", "run_validate_unit_stage"]

logger = get_logger(__name__)

_VALIDATABLE: tuple[UnitStatus, ...] = (UnitStatus.EXTRACTED, UnitStatus.VALIDATED)
"""VALIDATED is included so a re-run after a correction re-scores the row."""


@dataclass(frozen=True, slots=True)
class ValidateStageResult:
    unit_id: uuid.UUID
    ran: bool
    confidence: float = 0.0
    review_status: ReviewStatus | None = None
    flags: list[str] | None = None
    document_finished: bool = False


def _printed_values(sources: dict[str, Any]) -> dict[str, str]:
    """What each field looked like on the page, from the provenance extraction kept."""
    printed: dict[str, str] = {}
    for name, source in sources.items():
        if isinstance(source, dict):
            snippet = source.get("snippet")
            if isinstance(snippet, str):
                printed[name] = snippet
    return printed


_READER_FLAGS: frozenset[str] = frozenset(
    {ValidationFlag.VALUE_UNVERIFIED.value, ValidationFlag.LLM_UNAVAILABLE.value}
)
"""Flags the extract stage raises that validation cannot work out for itself.

Named explicitly rather than "keep whatever was there": validation is the authority on
everything it checks, so a stale flag from a previous run of its own must not survive a
re-run - only the reader's observations about how a value was obtained.
"""


def _reader_flag(value: object) -> bool:
    return isinstance(value, str) and value in _READER_FLAGS


def run_validate_unit_stage(
    unit_id: uuid.UUID,
    *,
    settings: Settings | None = None,
    dispatch: Dispatch = enqueue,
) -> ValidateStageResult:
    """Validate, score and route one row."""
    active = settings or get_settings()

    with session_scope() as session:
        unit = session.get(CertificateUnit, unit_id)
        if unit is None or unit.status not in _VALIDATABLE:
            return ValidateStageResult(unit_id=unit_id, ran=False)
        row = session.scalar(select(Extraction).where(Extraction.unit_id == unit_id))
        if row is None:
            return ValidateStageResult(unit_id=unit_id, ran=False)

        values = {name: str(value) for name, value in row.fields_jsonb.items()}
        confidences = {
            name: float(value)
            for name, value in row.field_confidences_jsonb.items()
            if isinstance(value, int | float)
        }
        methods = {name: str(value) for name, value in row.field_methods_jsonb.items()}

        schema = schema_for_batch_sync(session, unit.batch_id)

        outcome = validate_row(
            RowFacts(
                certificate_type=row.certificate_type,
                field_schema=schema,
                values=values,
                printed=_printed_values(dict(row.field_sources_jsonb)),
                field_confidences=confidences,
                type_confidence=unit.type_confidence,
                boundary_confidence=unit.boundary_confidence,
                ocr_used=row.ocr_used,
                ocr_mean_confidence=row.ocr_mean_confidence,
            ),
            min_field_confidence=active.confidence_review_floor,
        )
        confidence = score_row(
            certificate_type=row.certificate_type,
            values=values,
            field_confidences=confidences,
            field_methods=methods,
            outcome=outcome,
            type_confidence=unit.type_confidence,
            schema=schema,
        )
        status = route_row(
            confidence=confidence,
            outcome=outcome,
            auto_approve_at=active.confidence_auto_approve,
            review_floor=active.confidence_review_floor,
        )

        # Explicitly typed lists: the JSONB columns are list[object], and a narrower
        # comprehension does not satisfy them.
        #
        # Added to what the reader already flagged rather than replacing it. The extract
        # stage raises flags validation cannot re-derive - a value the model fallback
        # could not find in the document, a fallback that was unavailable - and
        # overwriting them here would drop exactly the warnings that say a value is not
        # to be trusted. Order is kept and duplicates are dropped, so re-running
        # validation converges instead of growing the list.
        reader_flags = [str(value) for value in row.flags_jsonb if _reader_flag(value)]
        flag_values: list[object] = list(
            dict.fromkeys([*reader_flags, *(flag.value for flag in outcome.flags)])
        )
        issues: list[object] = list(outcome.as_json())
        row.flags_jsonb = flag_values
        row.field_issues_jsonb = issues
        row.row_confidence = confidence
        row.review_status = status
        row.processed_at = utcnow()
        unit.status = UnitStatus.COMPLETED
        document_id = unit.document_id
        flags = [str(value) for value in row.flags_jsonb]

    logger.info(
        "pipeline.unit_validated",
        unit_id=str(unit_id),
        review_status=status.value,
        count=len(flags),
    )

    finished = _document_is_finished(document_id)
    if finished:
        dispatch(TASK_FINALIZE_DOCUMENT, {"document_id": str(document_id)})

    return ValidateStageResult(
        unit_id=unit_id,
        ran=True,
        confidence=confidence,
        review_status=status,
        flags=[str(flag) for flag in flags],
        document_finished=finished,
    )


def _document_is_finished(document_id: uuid.UUID) -> bool:
    """Whether every unit of this document has been through validation."""
    with session_scope() as session:
        pending = session.scalar(
            select(func.count())
            .select_from(CertificateUnit)
            .where(
                CertificateUnit.document_id == document_id,
                CertificateUnit.status != UnitStatus.COMPLETED,
                CertificateUnit.status != UnitStatus.FAILED,
            )
        )
        return not pending
