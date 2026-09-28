"""Validating a row and closing the document, against Postgres.

By this point a certificate has been read; what these stages decide is whether anyone
can use the result. So the assertions are about what the review screen and the export
read afterwards: the flags and their per-field detail, the score, the routing, and the
counters the progress screen shows.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import uuid
from pathlib import Path

import pytest
from sqlalchemy import select

from certex.db.models import Batch, CertificateUnit, Document, Extraction
from certex.db.session import session_scope
from certex.enums import (
    BatchStatus,
    CertificateType,
    DocumentStatus,
    ReviewStatus,
    UnitStatus,
    ValidationFlag,
)
from certex.pipeline.dispatch import TASK_FINALIZE_DOCUMENT
from certex.pipeline.filetypes import MIME_PDF
from certex.pipeline.stages.classify_stage import run_classify_unit_stage
from certex.pipeline.stages.extract_stage import run_extract_unit_stage
from certex.pipeline.stages.finalize_stage import run_finalize_document_stage
from certex.pipeline.stages.split_stage import run_split_stage
from certex.pipeline.stages.text_stage import run_text_stage
from certex.pipeline.stages.validate_stage import ValidateStageResult, run_validate_unit_stage
from certex.storage.s3 import StorageKeys
from tests.fixtures.builders import build_multi_certificate_pdf, build_text_pdf
from tests.integration.conftest import CommittedBatch, Recorder

pytestmark = pytest.mark.integration


async def run_validate(unit_id: uuid.UUID, recorder: Recorder) -> ValidateStageResult:
    return await asyncio.to_thread(run_validate_unit_stage, unit_id, dispatch=recorder)


def read_row(unit_id: uuid.UUID) -> Extraction:
    with session_scope() as session:
        row = session.scalar(select(Extraction).where(Extraction.unit_id == unit_id))
        assert row is not None
        session.expunge(row)
        return row


def read_document(document_id: uuid.UUID) -> Document:
    with session_scope() as session:
        document = session.get(Document, document_id)
        assert document is not None
        session.expunge(document)
        return document


def read_batch(batch_id: uuid.UUID) -> Batch:
    with session_scope() as session:
        batch = session.get(Batch, batch_id)
        assert batch is not None
        session.expunge(batch)
        return batch


def units_of(document_id: uuid.UUID) -> list[CertificateUnit]:
    with session_scope() as session:
        units = list(
            session.scalars(
                select(CertificateUnit)
                .where(CertificateUnit.document_id == document_id)
                .order_by(CertificateUnit.ordinal)
            )
        )
        for unit in units:
            session.expunge(unit)
        return units


async def extracted_document(
    committed_batch: CommittedBatch, recorder: Recorder, path: Path
) -> uuid.UUID:
    """A document carried all the way to extracted rows, ready to be validated."""
    document_id = committed_batch.add_document(path, mime_type=MIME_PDF)
    await asyncio.to_thread(run_text_stage, document_id, dispatch=recorder)
    await asyncio.to_thread(run_split_stage, document_id, dispatch=recorder)
    for unit in units_of(document_id):
        await asyncio.to_thread(run_classify_unit_stage, unit.id, dispatch=recorder)
        await asyncio.to_thread(run_extract_unit_stage, unit.id, dispatch=recorder)
    recorder.calls.clear()
    return document_id


@pytest.fixture
async def birth_document(
    committed_batch: CommittedBatch, recorder: Recorder, tmp_path: Path
) -> uuid.UUID:
    return await extracted_document(
        committed_batch, recorder, build_text_pdf(tmp_path / "birth.pdf")
    )


class TestValidatingAGoodRow:
    async def test_a_clean_certificate_carries_no_flags(
        self, birth_document: uuid.UUID, recorder: Recorder
    ) -> None:
        (unit,) = units_of(birth_document)

        result = await run_validate(unit.id, recorder)

        assert result.ran is True
        assert result.flags == []
        row = read_row(unit.id)
        assert row.flags_jsonb == []
        assert row.field_issues_jsonb == []

    async def test_the_row_is_scored(self, birth_document: uuid.UUID, recorder: Recorder) -> None:
        (unit,) = units_of(birth_document)

        result = await run_validate(unit.id, recorder)

        assert result.confidence > 0.9
        assert read_row(unit.id).row_confidence == result.confidence

    async def test_nothing_is_approved_without_a_person(
        self, birth_document: uuid.UUID, recorder: Recorder
    ) -> None:
        # This office reviews every row: the threshold is 1.00 and no automated reading
        # can reach it.
        (unit,) = units_of(birth_document)

        result = await run_validate(unit.id, recorder)

        assert result.review_status is ReviewStatus.NEEDS_REVIEW
        assert read_row(unit.id).review_status is ReviewStatus.NEEDS_REVIEW

    async def test_the_unit_is_complete_and_the_document_is_finished(
        self, birth_document: uuid.UUID, recorder: Recorder
    ) -> None:
        (unit,) = units_of(birth_document)

        result = await run_validate(unit.id, recorder)

        assert units_of(birth_document)[0].status is UnitStatus.COMPLETED
        assert result.document_finished is True
        assert recorder.calls == [(TASK_FINALIZE_DOCUMENT, {"document_id": str(birth_document)})]


class TestValidatingABadRow:
    async def test_a_contradictory_record_is_flagged_and_explained(
        self, birth_document: uuid.UUID, recorder: Recorder
    ) -> None:
        (unit,) = units_of(birth_document)
        with session_scope() as session:
            row = session.scalar(select(Extraction).where(Extraction.unit_id == unit.id))
            assert row is not None
            fields = dict(row.fields_jsonb)
            fields["registration_date"] = "1980-01-01"
            row.fields_jsonb = fields

        await run_validate(unit.id, recorder)

        row = read_row(unit.id)
        assert ValidationFlag.REGISTRATION_BEFORE_EVENT.value in row.flags_jsonb
        issue = next(
            item
            for item in row.field_issues_jsonb
            if isinstance(item, dict) and item.get("field") == "registration_date"
        )
        assert "before the event" in str(issue["detail"])

    async def test_a_flagged_row_scores_lower_than_a_clean_one(
        self, committed_batch: CommittedBatch, recorder: Recorder, tmp_path: Path
    ) -> None:
        clean_document = await extracted_document(
            committed_batch, recorder, build_text_pdf(tmp_path / "clean.pdf")
        )
        (clean_unit,) = units_of(clean_document)
        clean = await run_validate(clean_unit.id, recorder)

        broken_document = await extracted_document(
            committed_batch, recorder, build_text_pdf(tmp_path / "broken.pdf", "death_karachi")
        )
        (broken_unit,) = units_of(broken_document)
        with session_scope() as session:
            row = session.scalar(select(Extraction).where(Extraction.unit_id == broken_unit.id))
            assert row is not None
            fields = dict(row.fields_jsonb)
            fields["date_of_death"] = "1900-01-01"
            row.fields_jsonb = fields

        broken = await run_validate(broken_unit.id, recorder)

        assert broken.confidence < clean.confidence

    async def test_a_row_that_read_nothing_fails_rather_than_waiting_for_review(
        self, birth_document: uuid.UUID, recorder: Recorder
    ) -> None:
        (unit,) = units_of(birth_document)
        with session_scope() as session:
            row = session.scalar(select(Extraction).where(Extraction.unit_id == unit.id))
            assert row is not None
            row.fields_jsonb = {}
            row.field_confidences_jsonb = {}
            row.field_methods_jsonb = {}

        result = await run_validate(unit.id, recorder)

        assert result.review_status is ReviewStatus.FAILED
        assert ValidationFlag.NO_FIELDS_EXTRACTED.value in read_row(unit.id).flags_jsonb


class TestRunningTwice:
    async def test_validating_again_re_scores_rather_than_duplicating(
        self, birth_document: uuid.UUID, recorder: Recorder
    ) -> None:
        (unit,) = units_of(birth_document)

        first = await run_validate(unit.id, recorder)
        second = await run_validate(unit.id, recorder)

        assert first.ran is True
        assert second.ran is False, "the unit has already been through validation"
        assert read_row(unit.id).row_confidence == first.confidence

    async def test_a_unit_with_no_row_is_not_an_error(
        self, birth_document: uuid.UUID, recorder: Recorder
    ) -> None:
        (unit,) = units_of(birth_document)
        with session_scope() as session:
            row = session.scalar(select(Extraction).where(Extraction.unit_id == unit.id))
            assert row is not None
            session.delete(row)

        result = await run_validate(unit.id, recorder)

        assert result.ran is False


class TestManyCertificatesInOneFile:
    async def test_the_document_finishes_only_when_every_row_is_done(
        self, committed_batch: CommittedBatch, recorder: Recorder, tmp_path: Path
    ) -> None:
        document_id = await extracted_document(
            committed_batch,
            recorder,
            build_multi_certificate_pdf(tmp_path / "many.pdf", copies=3),
        )
        units = units_of(document_id)
        assert len(units) == 3

        results = [await run_validate(unit.id, recorder) for unit in units]

        assert [result.document_finished for result in results] == [False, False, True]
        assert recorder.calls == [(TASK_FINALIZE_DOCUMENT, {"document_id": str(document_id)})]


class TestFinishing:
    async def test_the_document_and_its_batch_are_marked_complete(
        self, birth_document: uuid.UUID, recorder: Recorder, committed_batch: CommittedBatch
    ) -> None:
        (unit,) = units_of(birth_document)
        await run_validate(unit.id, recorder)

        result = await asyncio.to_thread(run_finalize_document_stage, birth_document)

        assert result.ran is True
        assert result.unit_count == 1
        document = read_document(birth_document)
        assert document.status is DocumentStatus.COMPLETED
        assert document.processing_completed_at is not None
        batch = read_batch(committed_batch.batch_id)
        assert batch.status is BatchStatus.COMPLETED
        assert batch.processed_count == 1
        assert batch.unit_count == 1

    async def test_finishing_twice_changes_nothing(
        self, birth_document: uuid.UUID, recorder: Recorder
    ) -> None:
        (unit,) = units_of(birth_document)
        await run_validate(unit.id, recorder)

        first = await asyncio.to_thread(run_finalize_document_stage, birth_document)
        second = await asyncio.to_thread(run_finalize_document_stage, birth_document)

        assert first.ran is True
        assert second.ran is False

    async def test_a_document_still_being_read_is_not_finished(
        self, committed_batch: CommittedBatch, recorder: Recorder, tmp_path: Path
    ) -> None:
        document_id = await extracted_document(
            committed_batch,
            recorder,
            build_multi_certificate_pdf(tmp_path / "many.pdf", copies=2),
        )
        units = units_of(document_id)
        await run_validate(units[0].id, recorder)

        result = await asyncio.to_thread(run_finalize_document_stage, document_id)

        assert result.ran is False
        assert read_document(document_id).status is not DocumentStatus.COMPLETED


class TestDuplicateFiles:
    async def test_a_duplicate_upload_gets_its_own_row(
        self, committed_batch: CommittedBatch, recorder: Recorder, tmp_path: Path
    ) -> None:
        # The same certificate uploaded twice is read once, but the CSV is a record of
        # files handed over, so both files need a row.
        path = build_text_pdf(tmp_path / "birth.pdf")
        original_id = await extracted_document(committed_batch, recorder, path)
        duplicate_id = _add_duplicate(committed_batch, path, original_id)
        (unit,) = units_of(original_id)
        await run_validate(unit.id, recorder)

        result = await asyncio.to_thread(run_finalize_document_stage, original_id)

        assert result.duplicates_filled == 1
        (copied,) = units_of(duplicate_id)
        assert copied.status is UnitStatus.COMPLETED
        assert copied.certificate_type is CertificateType.BIRTH
        assert read_row(copied.id).fields_jsonb == read_row(unit.id).fields_jsonb

    async def test_the_copy_is_its_own_row_not_a_reference(
        self, committed_batch: CommittedBatch, recorder: Recorder, tmp_path: Path
    ) -> None:
        # Correcting the original must not silently rewrite another file's row.
        path = build_text_pdf(tmp_path / "birth.pdf")
        original_id = await extracted_document(committed_batch, recorder, path)
        duplicate_id = _add_duplicate(committed_batch, path, original_id)
        (unit,) = units_of(original_id)
        await run_validate(unit.id, recorder)
        await asyncio.to_thread(run_finalize_document_stage, original_id)

        with session_scope() as session:
            row = session.scalar(select(Extraction).where(Extraction.unit_id == unit.id))
            assert row is not None
            row.fields_jsonb = {**row.fields_jsonb, "child_full_name": "Corrected Name"}

        (copied,) = units_of(duplicate_id)
        assert read_row(copied.id).fields_jsonb["child_full_name"] == "Ayesha Noor Malik"

    async def test_filling_duplicates_twice_does_not_double_them(
        self, committed_batch: CommittedBatch, recorder: Recorder, tmp_path: Path
    ) -> None:
        path = build_text_pdf(tmp_path / "birth.pdf")
        original_id = await extracted_document(committed_batch, recorder, path)
        duplicate_id = _add_duplicate(committed_batch, path, original_id)
        (unit,) = units_of(original_id)
        await run_validate(unit.id, recorder)

        await asyncio.to_thread(run_finalize_document_stage, original_id)
        with session_scope() as session:
            document = session.get(Document, original_id)
            assert document is not None
            document.status = DocumentStatus.VALIDATING  # force a second run
        await asyncio.to_thread(run_finalize_document_stage, original_id)

        assert len(units_of(duplicate_id)) == 1


def _add_duplicate(
    committed_batch: CommittedBatch, path: Path, original_id: uuid.UUID
) -> uuid.UUID:
    """A second upload of the same bytes, recorded as a duplicate of the first."""
    duplicate_id = uuid.uuid4()
    payload = path.read_bytes()
    now = dt.datetime.now(tz=dt.UTC)
    with session_scope() as session:
        session.add(
            Document(
                id=duplicate_id,
                batch_id=committed_batch.batch_id,
                workspace_id=committed_batch.workspace_id,
                original_filename="birth-copy.pdf",
                mime_type=MIME_PDF,
                byte_size=len(payload),
                sha256=hashlib.sha256(payload).hexdigest(),
                storage_key=StorageKeys.document(
                    committed_batch.workspace_id, duplicate_id, year=now.year, month=now.month
                ),
                status=DocumentStatus.DUPLICATE,
                is_duplicate_of=original_id,
            )
        )
    return duplicate_id
