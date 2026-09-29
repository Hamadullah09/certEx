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

**The rows become register entries.** Until this point the work is a reading of a
file; from here it is a record the office holds, found by certificate number and by
the names on it, linked to the pages it was read from. Publishing happens once the
whole document is settled, and is idempotent, so a retried task adds nothing.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from certex.config import Settings
from certex.core.errors import ValidationFailedError
from certex.db.base import utcnow
from certex.db.models import Batch, Certificate, CertificateUnit, Document, Extraction
from certex.db.session import session_scope
from certex.enums import (
    BoundaryMethod,
    CertificateSource,
    DocumentLinkKind,
    DocumentStatus,
    UnitStatus,
)
from certex.fields import FieldSchema, schema_or_builtin
from certex.logging_setup import get_logger
from certex.pipeline.state import refresh_batch_progress
from certex.services import certificate_service, schema_service

__all__ = ["FinalizeStageResult", "run_finalize_document_stage"]

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class FinalizeStageResult:
    document_id: uuid.UUID
    ran: bool
    unit_count: int = 0
    duplicates_filled: int = 0
    certificates_written: int = 0


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
    registered = _publish_to_register(document_id)

    with session_scope() as session:
        refresh_batch_progress(session, batch_id)

    logger.info(
        "pipeline.document_finished",
        document_id=str(document_id),
        count=unit_count,
        duplicate_count=duplicates,
        certificate_count=registered,
    )
    return FinalizeStageResult(
        document_id=document_id,
        ran=True,
        unit_count=unit_count,
        duplicates_filled=duplicates,
        certificates_written=registered,
    )


def _publish_to_register(document_id: uuid.UUID) -> int:
    """Write this document's finished rows into the register.

    An extraction is what one document appeared to say; a register entry is what the
    office holds. Publishing here rather than in the extract stage is deliberate: a
    document is only settled once every unit in it has finished, and until then a
    later unit could still fail and take the document with it.

    Idempotent by ``source_extraction_id``, so a retried task adds nothing. A row the
    register cannot file - no certificate number, or a type this workspace does not
    hold - is left as an extraction and reported, because failing to register one row
    must not fail the document.
    """
    written = 0
    with session_scope() as session:
        document = session.get(Document, document_id)
        if document is None:  # pragma: no cover - defensive
            return 0

        batch = session.get(Batch, document.batch_id)
        pinned_schema = schema_service.schema_for_batch_sync(session, document.batch_id)

        rows = list(
            session.scalars(
                select(Extraction)
                .join(CertificateUnit, CertificateUnit.id == Extraction.unit_id)
                .where(CertificateUnit.document_id == document_id)
                .order_by(CertificateUnit.ordinal)
            )
        )
        for row in rows:
            already = session.scalar(
                select(Certificate.id).where(Certificate.source_extraction_id == row.id)
            )
            if already is not None:
                continue

            target = _register_target(
                session,
                workspace_id=document.workspace_id,
                batch=batch,
                row=row,
                pinned_schema=pinned_schema,
            )
            if target is None:
                continue

            type_id, schema = target
            try:
                certificate = certificate_service.record_certificate_sync(
                    session,
                    workspace_id=document.workspace_id,
                    certificate_type_id=type_id,
                    schema=schema,
                    values=certificate_service.text_values(row.fields_jsonb),
                    source=CertificateSource.EXTRACTION,
                    confidences=_confidences_of(row),
                    provenance=_provenance_of(row),
                    row_confidence=row.row_confidence,
                    needs_review=not row.review_status.is_approved,
                    source_extraction_id=row.id,
                    source_batch_id=row.batch_id,
                )
            except ValidationFailedError as exc:
                # Almost always a missing certificate number. The row stays where it
                # is, exports as it always did, and the reviewer sees why.
                logger.info(
                    "register.row_not_filed",
                    document_id=str(document_id),
                    entity_id=str(row.id),
                    reason=exc.code.value,
                )
                continue

            certificate_service.link_document_sync(
                session,
                certificate=certificate,
                document_id=document_id,
                unit_id=row.unit_id,
                kind=DocumentLinkKind.PRIMARY,
                page_start=row.unit.page_start,
                page_end=row.unit.page_end,
            )
            written += 1
    return written


def _register_target(
    session: Session,
    *,
    workspace_id: uuid.UUID,
    batch: Batch | None,
    row: Extraction,
    pinned_schema: FieldSchema | None,
) -> tuple[uuid.UUID, FieldSchema] | None:
    """Which registry type and schema this row should be filed under.

    The batch decides when it was created for a type - which is the normal path, since
    an operator uploads into Birth or Death rather than into nothing. Otherwise the
    classifier's answer is mapped onto the workspace's types, which is what lets a
    batch created before any of this existed still reach the register.
    """
    if batch is not None and batch.certificate_type_id is not None:
        return batch.certificate_type_id, schema_or_builtin(row.certificate_type, pinned_schema)

    type_record = schema_service.type_record_for_sync(
        session, workspace_id=workspace_id, classifier_key=row.certificate_type
    )
    if type_record is None:
        logger.info(
            "register.no_type_for_row",
            entity_id=str(row.id),
            certificate_type=row.certificate_type.value,
        )
        return None

    schema = pinned_schema
    if schema is None:
        version = schema_service.default_version_for_type_sync(
            session, workspace_id=workspace_id, certificate_type_id=type_record.id
        )
        schema = (
            schema_service.resolve_schema_sync(session, version.id) if version is not None else None
        )
    return type_record.id, schema_or_builtin(row.certificate_type, schema)


def _confidences_of(row: Extraction) -> dict[str, float]:
    return {
        name: float(value)
        for name, value in row.field_confidences_jsonb.items()
        if isinstance(value, int | float)
    }


def _provenance_of(row: Extraction) -> dict[str, object]:
    """Method and source snippet per field, carried onto the entry.

    The entry outlives its batch, and "why does it say that" has to stay answerable
    after the documents behind it are archived.
    """
    provenance: dict[str, object] = {}
    for name, method in row.field_methods_jsonb.items():
        entry: dict[str, object] = {"method": method}
        source = row.field_sources_jsonb.get(name)
        if isinstance(source, dict):
            entry.update(source)
        provenance[name] = entry
    return provenance


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
