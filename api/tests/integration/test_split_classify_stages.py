"""Splitting a document into certificates, and classifying each one.

These two stages turn pages into rows-to-be, so what is asserted is the bookkeeping a
later stage and a reviewer depend on: the units that exist, their page ranges, the
method and confidence behind each boundary, the type on each unit, and the handover -
which must happen exactly once however many times a task is delivered.
"""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

import fitz
import pytest
from sqlalchemy import select

from certex.db.models import CertificateUnit, Document
from certex.db.session import session_scope
from certex.enums import (
    BoundaryMethod,
    CertificateType,
    ClassificationMethod,
    DocumentStatus,
    UnitStatus,
)
from certex.pipeline.dispatch import TASK_CLASSIFY_UNIT, TASK_EXTRACT_UNIT
from certex.pipeline.filetypes import MIME_DOCX, MIME_PDF
from certex.pipeline.stages.classify_stage import run_classify_unit_stage
from certex.pipeline.stages.split_stage import SplitStageResult, run_split_stage
from certex.pipeline.stages.text_stage import run_text_stage
from tests.fixtures.builders import build_docx, build_multi_certificate_pdf, build_text_pdf
from tests.fixtures.corpus import arabic_font, build_bilingual_pdf, build_docx_multi_page
from tests.integration.conftest import CommittedBatch, Recorder

pytestmark = pytest.mark.integration


async def run_text(document_id: uuid.UUID, recorder: Recorder) -> None:
    await asyncio.to_thread(run_text_stage, document_id, dispatch=recorder)


async def run_split(document_id: uuid.UUID, recorder: Recorder) -> SplitStageResult:
    return await asyncio.to_thread(run_split_stage, document_id, dispatch=recorder)


async def run_classify(unit_id: uuid.UUID, recorder: Recorder) -> object:
    return await asyncio.to_thread(run_classify_unit_stage, unit_id, dispatch=recorder)


def read_units(document_id: uuid.UUID) -> list[CertificateUnit]:
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


def read_document(document_id: uuid.UUID) -> Document:
    with session_scope() as session:
        document = session.get(Document, document_id)
        assert document is not None
        session.expunge(document)
        return document


async def prepared_document(
    committed_batch: CommittedBatch, recorder: Recorder, path: Path, *, mime_type: str = MIME_PDF
) -> uuid.UUID:
    """A document that has been through text extraction and now awaits splitting."""
    document_id = committed_batch.add_document(path, mime_type=mime_type)
    await run_text(document_id, recorder)
    recorder.calls.clear()
    return document_id


class TestOneCertificatePerFile:
    async def test_a_single_certificate_becomes_one_unit(
        self, committed_batch: CommittedBatch, recorder: Recorder, tmp_path: Path
    ) -> None:
        document_id = await prepared_document(
            committed_batch, recorder, build_text_pdf(tmp_path / "birth.pdf")
        )

        result = await run_split(document_id, recorder)

        assert result.ran is True
        assert result.unit_count == 1
        assert result.method is BoundaryMethod.SINGLE_DOCUMENT
        (unit,) = read_units(document_id)
        assert (unit.page_start, unit.page_end, unit.ordinal) == (1, 1, 0)
        assert unit.status is UnitStatus.TEXT_READY

    async def test_the_document_moves_on_and_queues_its_unit(
        self, committed_batch: CommittedBatch, recorder: Recorder, tmp_path: Path
    ) -> None:
        document_id = await prepared_document(
            committed_batch, recorder, build_text_pdf(tmp_path / "birth.pdf")
        )

        await run_split(document_id, recorder)

        assert read_document(document_id).status is DocumentStatus.CLASSIFYING
        (unit,) = read_units(document_id)
        assert recorder.calls == [(TASK_CLASSIFY_UNIT, {"unit_id": str(unit.id)})]

    async def test_a_word_document_splits_too(
        self, committed_batch: CommittedBatch, recorder: Recorder, tmp_path: Path
    ) -> None:
        document_id = await prepared_document(
            committed_batch, recorder, build_docx(tmp_path / "birth.docx"), mime_type=MIME_DOCX
        )

        result = await run_split(document_id, recorder)

        assert result.unit_count == 1


class TestManyCertificatesPerFile:
    async def test_a_file_of_certificates_is_split_by_their_headings(
        self, committed_batch: CommittedBatch, recorder: Recorder, tmp_path: Path
    ) -> None:
        path = build_multi_certificate_pdf(tmp_path / "many.pdf", copies=5)
        document_id = await prepared_document(committed_batch, recorder, path)

        result = await run_split(document_id, recorder)

        assert result.unit_count == 5
        assert result.confidence > 0.8
        units = read_units(document_id)
        assert [(unit.page_start, unit.page_end) for unit in units] == [
            (1, 1),
            (2, 2),
            (3, 3),
            (4, 4),
            (5, 5),
        ]
        assert [unit.ordinal for unit in units] == [0, 1, 2, 3, 4]

    async def test_every_unit_is_queued_for_classification(
        self, committed_batch: CommittedBatch, recorder: Recorder, tmp_path: Path
    ) -> None:
        path = build_multi_certificate_pdf(tmp_path / "many.pdf", copies=3)
        document_id = await prepared_document(committed_batch, recorder, path)

        await run_split(document_id, recorder)

        unit_ids = {str(unit.id) for unit in read_units(document_id)}
        assert recorder.task_names == [TASK_CLASSIFY_UNIT] * 3
        assert {call[1]["unit_id"] for call in recorder.calls} == unit_ids

    async def test_a_pdf_outline_decides_the_boundaries(
        self, committed_batch: CommittedBatch, recorder: Recorder, tmp_path: Path
    ) -> None:
        # The producer said where each certificate starts; that beats any heuristic.
        source = build_multi_certificate_pdf(tmp_path / "many.pdf", copies=6)
        with_outline = tmp_path / "outlined.pdf"
        with fitz.open(str(source)) as document:
            document.set_toc([[1, "Certificate 1", 1], [1, "Certificate 2", 4]])
            document.save(str(with_outline))
        document_id = await prepared_document(committed_batch, recorder, with_outline)

        result = await run_split(document_id, recorder)

        assert result.method is BoundaryMethod.BOOKMARK
        assert [(unit.page_start, unit.page_end) for unit in read_units(document_id)] == [
            (1, 3),
            (4, 6),
        ]

    async def test_a_word_document_splits_on_its_page_breaks(
        self, committed_batch: CommittedBatch, recorder: Recorder, tmp_path: Path
    ) -> None:
        document_id = await prepared_document(
            committed_batch,
            recorder,
            build_docx_multi_page(tmp_path / "many.docx"),
            mime_type=MIME_DOCX,
        )

        result = await run_split(document_id, recorder)

        assert result.unit_count == 3

    async def test_the_batch_learns_how_many_certificates_it_holds(
        self, committed_batch: CommittedBatch, recorder: Recorder, tmp_path: Path
    ) -> None:
        path = build_multi_certificate_pdf(tmp_path / "many.pdf", copies=4)
        document_id = await prepared_document(committed_batch, recorder, path)

        await run_split(document_id, recorder)

        from certex.db.models import Batch

        with session_scope() as session:
            batch = session.get(Batch, committed_batch.batch_id)
            assert batch is not None
            assert batch.unit_count == 4
        del document_id


class TestSplittingTwice:
    async def test_a_redelivered_task_does_not_produce_a_second_set_of_units(
        self, committed_batch: CommittedBatch, recorder: Recorder, tmp_path: Path
    ) -> None:
        path = build_multi_certificate_pdf(tmp_path / "many.pdf", copies=3)
        document_id = await prepared_document(committed_batch, recorder, path)

        first = await run_split(document_id, recorder)
        second = await run_split(document_id, recorder)

        assert first.ran is True
        assert second.ran is False
        assert len(read_units(document_id)) == 3
        assert len(recorder.calls) == 3, "classification is queued once per unit"

    async def test_a_document_that_has_not_reached_this_stage_is_left_alone(
        self, committed_batch: CommittedBatch, recorder: Recorder, tmp_path: Path
    ) -> None:
        document_id = committed_batch.add_document(
            build_text_pdf(tmp_path / "birth.pdf"), mime_type=MIME_PDF
        )

        result = await run_split(document_id, recorder)

        assert result.ran is False
        assert read_units(document_id) == []


class TestClassification:
    @pytest.mark.parametrize(
        ("sample_key", "expected"),
        [
            ("birth_lahore", CertificateType.BIRTH),
            ("death_karachi", CertificateType.DEATH),
            ("marriage_islamabad", CertificateType.MARRIAGE),
        ],
    )
    async def test_each_kind_of_certificate_is_recognised(
        self,
        committed_batch: CommittedBatch,
        recorder: Recorder,
        tmp_path: Path,
        sample_key: str,
        expected: CertificateType,
    ) -> None:
        path = build_text_pdf(tmp_path / f"{sample_key}.pdf", sample_key)
        document_id = await prepared_document(committed_batch, recorder, path)
        await run_split(document_id, recorder)
        (unit,) = read_units(document_id)
        recorder.calls.clear()

        await run_classify(unit.id, recorder)

        classified = read_units(document_id)[0]
        assert classified.certificate_type is expected
        assert classified.type_confidence > 0.8
        assert classified.classification_method is ClassificationMethod.KEYWORD
        assert classified.status is UnitStatus.CLASSIFIED

    @pytest.mark.skipif(
        arabic_font() is None, reason="no Arabic-script font is installed on this machine"
    )
    async def test_an_urdu_certificate_is_classified(
        self, committed_batch: CommittedBatch, recorder: Recorder, tmp_path: Path
    ) -> None:
        path = build_bilingual_pdf(tmp_path / "urdu.pdf")
        document_id = await prepared_document(committed_batch, recorder, path)
        await run_split(document_id, recorder)
        (unit,) = read_units(document_id)

        await run_classify(unit.id, recorder)

        assert read_units(document_id)[0].certificate_type is CertificateType.BIRTH

    async def test_the_unit_is_queued_for_field_extraction(
        self, committed_batch: CommittedBatch, recorder: Recorder, tmp_path: Path
    ) -> None:
        document_id = await prepared_document(
            committed_batch, recorder, build_text_pdf(tmp_path / "birth.pdf")
        )
        await run_split(document_id, recorder)
        (unit,) = read_units(document_id)
        recorder.calls.clear()

        await run_classify(unit.id, recorder)

        assert recorder.calls == [(TASK_EXTRACT_UNIT, {"unit_id": str(unit.id)})]

    async def test_the_last_unit_moves_the_document_on(
        self, committed_batch: CommittedBatch, recorder: Recorder, tmp_path: Path
    ) -> None:
        path = build_multi_certificate_pdf(tmp_path / "many.pdf", copies=3)
        document_id = await prepared_document(committed_batch, recorder, path)
        await run_split(document_id, recorder)
        units = read_units(document_id)

        results = [await run_classify(unit.id, recorder) for unit in units]

        assert [result.advanced for result in results] == [False, False, True]
        assert read_document(document_id).status is DocumentStatus.EXTRACTING_FIELDS

    async def test_classifying_twice_changes_nothing(
        self, committed_batch: CommittedBatch, recorder: Recorder, tmp_path: Path
    ) -> None:
        document_id = await prepared_document(
            committed_batch, recorder, build_text_pdf(tmp_path / "birth.pdf")
        )
        await run_split(document_id, recorder)
        (unit,) = read_units(document_id)
        recorder.calls.clear()

        first = await run_classify(unit.id, recorder)
        second = await run_classify(unit.id, recorder)

        assert first.ran is True
        assert second.ran is False
        assert len(recorder.calls) == 1, "field extraction is queued once"

    async def test_a_unit_that_no_longer_exists_is_not_an_error(
        self, committed_batch: CommittedBatch, recorder: Recorder
    ) -> None:
        result = await run_classify(uuid.uuid4(), recorder)
        assert result.ran is False
        assert recorder.calls == []
        del committed_batch
