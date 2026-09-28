"""Stage 5b - decide what kind of certificate each unit is.

The type decides which fields the next stage looks for, so a wrong answer here shows up
as a row of empty columns rather than as an error. The classifier is built to abstain,
and an abstention is carried forward honestly: the unit stays OTHER with a confidence of
zero, which routes its row to review instead of quietly producing nothing.

One task per unit, with the same fan-in as OCR: the unit that finds no siblings left to
classify moves the document on, once, by compare-and-set.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import func, select

from certex.config import Settings
from certex.db.models import CertificateUnit, PageText
from certex.db.session import session_scope
from certex.enums import CertificateType, ClassificationMethod, DocumentStatus, UnitStatus
from certex.logging_setup import get_logger
from certex.pipeline.classify.classifier import Classification, classify_text
from certex.pipeline.dispatch import TASK_EXTRACT_UNIT, Dispatch, enqueue
from certex.pipeline.state import claim_document

__all__ = ["ClassifyStageResult", "run_classify_unit_stage", "unit_text"]

logger = get_logger(__name__)

_CLASSIFIABLE: tuple[UnitStatus, ...] = (UnitStatus.PENDING, UnitStatus.TEXT_READY)


@dataclass(frozen=True, slots=True)
class ClassifyStageResult:
    unit_id: uuid.UUID
    ran: bool
    certificate_type: CertificateType | None = None
    confidence: float = 0.0
    advanced: bool = False
    """True when this unit was the last one and the document moved on."""


def unit_text(unit_id: uuid.UUID) -> str:
    """The text of every page in a unit's range, in page order."""
    with session_scope() as session:
        unit = session.get(CertificateUnit, unit_id)
        if unit is None:
            return ""
        pages = session.scalars(
            select(PageText.raw_text)
            .where(
                PageText.document_id == unit.document_id,
                PageText.page_number >= unit.page_start,
                PageText.page_number <= unit.page_end,
            )
            .order_by(PageText.page_number)
        )
        return "\n".join(text for text in pages if text)


def run_classify_unit_stage(
    unit_id: uuid.UUID,
    *,
    settings: Settings | None = None,
    dispatch: Dispatch = enqueue,
) -> ClassifyStageResult:
    """Classify one unit, then move the document on if it was the last."""
    del settings  # the keyword policy has no settings of its own yet

    with session_scope() as session:
        unit = session.get(CertificateUnit, unit_id)
        if unit is None or unit.status not in _CLASSIFIABLE:
            return ClassifyStageResult(unit_id=unit_id, ran=False)
        document_id = unit.document_id

    classification = classify_text(unit_text(unit_id))

    with session_scope() as session:
        unit = session.get(CertificateUnit, unit_id)
        if unit is None or unit.status not in _CLASSIFIABLE:
            # Another delivery of this task classified it while this one was reading.
            return ClassifyStageResult(unit_id=unit_id, ran=False)
        _apply(unit, classification)

    advanced = _advance_document_if_ready(document_id)

    logger.info(
        "pipeline.unit_classified",
        unit_id=str(unit_id),
        certificate_type=classification.certificate_type.value,
        method=classification.method.value,
    )
    dispatch(TASK_EXTRACT_UNIT, {"unit_id": str(unit_id)})
    return ClassifyStageResult(
        unit_id=unit_id,
        ran=True,
        certificate_type=classification.certificate_type,
        confidence=classification.confidence,
        advanced=advanced,
    )


def _apply(unit: CertificateUnit, classification: Classification) -> None:
    unit.certificate_type = classification.certificate_type
    unit.type_confidence = classification.confidence
    unit.classification_method = classification.method
    unit.status = UnitStatus.CLASSIFIED
    if classification.method is ClassificationMethod.DEFAULT:
        # Not a failure: the row is produced and routed to review, where a person can
        # set the type in one click. Saying so here is what makes that possible.
        unit.error_code = None
        unit.error_message = None


def _advance_document_if_ready(document_id: uuid.UUID) -> bool:
    """Move the document to field extraction once no unit is waiting to be classified."""
    with session_scope() as session:
        pending = session.scalar(
            select(func.count())
            .select_from(CertificateUnit)
            .where(
                CertificateUnit.document_id == document_id,
                CertificateUnit.status.in_(_CLASSIFIABLE),
            )
        )
        if pending:
            return False
        return claim_document(
            session,
            document_id,
            from_statuses=(DocumentStatus.CLASSIFYING,),
            to_status=DocumentStatus.EXTRACTING_FIELDS,
        )
