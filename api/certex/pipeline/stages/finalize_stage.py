"""Stage 8b - close a document, and the batch when it is the last one.

Finishing is not just a status change. Two things have to happen here or the batch is
wrong:

**Duplicates get their rows.** A clerk who uploads the same certificate twice - in two
archives, or once by mistake - has one file ingested and the other marked a duplicate,
because reading it again would cost the same work for the same answer. But the CSV is a
record of files handed over, so a duplicate still needs its row. Those rows are copied
from the original here, marked as duplicates, so the export has one line per file and
nobody has to explain a missing row.

**The batch learns where it stands.** The counters the progress screen reads are
recomputed from the documents themselves rather than incremented, so a task that ran
twice, or one that died halfway, cannot leave a batch that says 9 of 10 forever.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select

from certex.config import Settings
from certex.db.base import utcnow
from certex.db.models import CertificateUnit, Document, Extraction
from certex.db.session import session_scope
from certex.enums import BoundaryMethod, DocumentStatus, UnitStatus
from certex.logging_setup import get_logger
from certex.pipeline.state import refresh_batch_progress

__all__ = ["FinalizeStageResult", "run_finalize_document_stage"]

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class FinalizeStageResult:
    document_id: uuid.UUID
    ran: bool
    unit_count: int = 0
    duplicates_filled: int = 0


def run_finalize_document_stage(
    document_id: uuid.UUID, *, settings: Settings | None = None
) -> FinalizeStageResult:
    """Mark a document complete, give its duplicates their rows, update the batch."""
    del settings  # finalising has no settings of its own

    with session_scope() as session:
        document = session.get(Document, document_id)
        if document is None:
            return FinalizeStageResult(document_id=document_id, ran=False)
        if document.status in (DocumentStatus.COMPLETED, DocumentStatus.FAILED):
            return FinalizeStageResult(document_id=document_id, ran=False)

        units = list(
            session.scalars(
                select(CertificateUnit).where(CertificateUnit.document_id == document_id)
            )
        )
        if any(unit.status not in (UnitStatus.COMPLETED, UnitStatus.FAILED) for unit in units):
            # Another unit is still being read; whichever finishes last calls this again.
            return FinalizeStageResult(document_id=document_id, ran=False)

        document.status = DocumentStatus.COMPLETED
        document.processing_completed_at = utcnow()
        batch_id = document.batch_id
        unit_count = len(units)

    duplicates = _fill_duplicates(document_id)

    with session_scope() as session:
        refresh_batch_progress(session, batch_id)

    logger.info(
        "pipeline.document_finished",
        document_id=str(document_id),
        count=unit_count,
        duplicate_count=duplicates,
    )
    return FinalizeStageResult(
        document_id=document_id,
        ran=True,
        unit_count=unit_count,
        duplicates_filled=duplicates,
    )


def _fill_duplicates(original_id: uuid.UUID) -> int:
    """Copy this document's rows onto the duplicates of it that carry none.

    The copy is a real row rather than a reference: an export is a snapshot, and a
    reviewer correcting the original should not silently rewrite a row that belongs to a
    different uploaded file.
    """
    filled = 0
    with session_scope() as session:
        duplicates = list(
            session.scalars(
                select(Document).where(
                    Document.is_duplicate_of == original_id,
                    Document.status == DocumentStatus.DUPLICATE,
                )
            )
        )
        if not duplicates:
            return 0

        source_units = list(
            session.scalars(
                select(CertificateUnit)
                .where(CertificateUnit.document_id == original_id)
                .order_by(CertificateUnit.ordinal)
            )
        )
        if not source_units:
            return 0

        for duplicate in duplicates:
            existing = session.scalar(
                select(CertificateUnit).where(CertificateUnit.document_id == duplicate.id)
            )
            if existing is not None:
                continue  # already filled by an earlier run
            for source in source_units:
                copy = CertificateUnit(
                    document_id=duplicate.id,
                    batch_id=duplicate.batch_id,
                    ordinal=source.ordinal,
                    page_start=source.page_start,
                    page_end=source.page_end,
                    boundary_method=BoundaryMethod.SINGLE_DOCUMENT,
                    boundary_confidence=source.boundary_confidence,
                    certificate_type=source.certificate_type,
                    type_confidence=source.type_confidence,
                    classification_method=source.classification_method,
                    status=UnitStatus.COMPLETED,
                )
                session.add(copy)
                session.flush()
                original_row = session.scalar(
                    select(Extraction).where(Extraction.unit_id == source.id)
                )
                if original_row is None:
                    continue
                session.add(_copy_row(original_row, copy, duplicate))
                filled += 1
    return filled


def _copy_row(original: Extraction, unit: CertificateUnit, duplicate: Document) -> Extraction:
    """The original's values, attached to the duplicate.

    The row is not flagged: nothing about it is wrong. That this file was a duplicate is
    recorded on the document, which is where the export reads it from.
    """
    return Extraction(
        unit_id=unit.id,
        batch_id=duplicate.batch_id,
        workspace_id=duplicate.workspace_id,
        schema_version=original.schema_version,
        certificate_type=original.certificate_type,
        fields_jsonb=dict(original.fields_jsonb),
        field_confidences_jsonb=dict(original.field_confidences_jsonb),
        field_methods_jsonb=dict(original.field_methods_jsonb),
        field_sources_jsonb=dict(original.field_sources_jsonb),
        extra_fields_jsonb=dict(original.extra_fields_jsonb),
        flags_jsonb=[*original.flags_jsonb],
        field_issues_jsonb=[*original.field_issues_jsonb],
        row_confidence=original.row_confidence,
        review_status=original.review_status,
        ocr_used=original.ocr_used,
        ocr_mean_confidence=original.ocr_mean_confidence,
        detected_language=original.detected_language,
        template_id=original.template_id,
        processed_at=utcnow(),
    )
