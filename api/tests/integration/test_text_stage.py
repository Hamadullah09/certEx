"""The text-extraction stage, end to end against Postgres and object storage.

The stage is the pipeline's first real worker step: it claims a document, downloads
it, writes one row per page, and hands the document to OCR or to splitting. What is
asserted here is everything a later stage or an operator depends on: the document's
status, the page rows, the quality signals that drove the OCR decision, the error and
remediation left behind by a failure, and the fact that running the same task twice
changes nothing.
"""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

import pytest
from sqlalchemy import select

from certex.db.models import Batch, Document, PageText
from certex.db.session import session_scope
from certex.enums import BatchStatus, DocumentStatus, OcrEngine, PageExtractionSource
from certex.pipeline.dispatch import TASK_OCR_PAGE, TASK_SPLIT_DOCUMENT
from certex.pipeline.filetypes import MIME_DOC, MIME_DOCX, MIME_PDF
from certex.pipeline.stages.text_stage import TextStageResult, run_text_stage
from certex.schemas.layout import PageLayout
from tests.fixtures.builders import (
    build_docx,
    build_image,
    build_multi_certificate_pdf,
    build_text_pdf,
)
from tests.fixtures.corpus import (
    URDU_BIRTH,
    arabic_font,
    build_bilingual_pdf,
    build_docx_broken,
    build_scanned_image,
    build_scanned_pdf,
)
from tests.integration.conftest import CommittedBatch, Recorder

pytestmark = [pytest.mark.integration]


async def run_stage(document_id: uuid.UUID, recorder: Recorder) -> TextStageResult:
    """Run the stage off the event loop, as a worker thread would."""
    return await asyncio.to_thread(run_text_stage, document_id, dispatch=recorder)


def read_document(document_id: uuid.UUID) -> Document:
    with session_scope() as session:
        document = session.get(Document, document_id)
        assert document is not None
        session.expunge(document)
        return document


def read_pages(document_id: uuid.UUID) -> list[PageText]:
    with session_scope() as session:
        rows = list(
            session.scalars(
                select(PageText)
                .where(PageText.document_id == document_id)
                .order_by(PageText.page_number)
            )
        )
        for row in rows:
            session.expunge(row)
        return rows


def read_batch(batch_id: uuid.UUID) -> Batch:
    with session_scope() as session:
        batch = session.get(Batch, batch_id)
        assert batch is not None
        session.expunge(batch)
        return batch


class TestTextPdf:
    async def test_the_document_moves_on_to_splitting(
        self, committed_batch: CommittedBatch, recorder: Recorder, tmp_path: Path
    ) -> None:
        document_id = committed_batch.add_document(
            build_text_pdf(tmp_path / "birth.pdf"), mime_type=MIME_PDF
        )

        result = await run_stage(document_id, recorder)

        assert result.ran is True
        assert result.page_count == 1
        assert result.ocr_pages == []
        assert result.failed_code is None
        assert read_document(document_id).status is DocumentStatus.SPLITTING

    async def test_splitting_is_enqueued_once_the_pages_are_written(
        self, committed_batch: CommittedBatch, recorder: Recorder, tmp_path: Path
    ) -> None:
        document_id = committed_batch.add_document(
            build_text_pdf(tmp_path / "birth.pdf"), mime_type=MIME_PDF
        )

        await run_stage(document_id, recorder)

        assert recorder.calls == [(TASK_SPLIT_DOCUMENT, {"document_id": str(document_id)})]

    async def test_the_page_row_holds_the_text_and_its_provenance(
        self, committed_batch: CommittedBatch, recorder: Recorder, tmp_path: Path
    ) -> None:
        document_id = committed_batch.add_document(
            build_text_pdf(tmp_path / "birth.pdf"), mime_type=MIME_PDF
        )

        await run_stage(document_id, recorder)

        (page,) = read_pages(document_id)
        assert page.page_number == 1
        assert page.extraction_source is PageExtractionSource.NATIVE_PDF
        assert page.ocr_engine is OcrEngine.NONE
        assert page.ocr_mean_confidence is None
        assert page.needed_ocr is False
        assert page.language == "eng"
        assert "BC-2019-004471" in page.raw_text
        assert "Ayesha Noor Malik" in page.raw_text

    async def test_the_page_row_holds_the_quality_signals_behind_the_decision(
        self, committed_batch: CommittedBatch, recorder: Recorder, tmp_path: Path
    ) -> None:
        document_id = committed_batch.add_document(
            build_text_pdf(tmp_path / "birth.pdf"), mime_type=MIME_PDF
        )

        await run_stage(document_id, recorder)

        (page,) = read_pages(document_id)
        assert page.char_count > 300
        assert page.alnum_ratio > 0.85
        assert page.dict_hit_rate > 0.9

    async def test_the_stored_layout_is_a_valid_layout(
        self, committed_batch: CommittedBatch, recorder: Recorder, tmp_path: Path
    ) -> None:
        document_id = committed_batch.add_document(
            build_text_pdf(tmp_path / "birth.pdf"), mime_type=MIME_PDF
        )

        await run_stage(document_id, recorder)

        (page,) = read_pages(document_id)
        layout = PageLayout.model_validate(page.layout_blocks_jsonb)
        assert layout.engine == "pdfplumber"
        assert layout.words and layout.lines
        assert page.width == pytest.approx(layout.width or 0.0)
        assert page.rotation == 0.0

    async def test_the_document_records_when_processing_began(
        self, committed_batch: CommittedBatch, recorder: Recorder, tmp_path: Path
    ) -> None:
        document_id = committed_batch.add_document(
            build_text_pdf(tmp_path / "birth.pdf"), mime_type=MIME_PDF
        )

        await run_stage(document_id, recorder)

        document = read_document(document_id)
        assert document.processing_started_at is not None
        assert document.page_count == 1


class TestManyPages:
    async def test_one_row_per_page(
        self, committed_batch: CommittedBatch, recorder: Recorder, tmp_path: Path
    ) -> None:
        document_id = committed_batch.add_document(
            build_multi_certificate_pdf(tmp_path / "many.pdf", copies=4), mime_type=MIME_PDF
        )

        result = await run_stage(document_id, recorder)

        assert result.page_count == 4
        assert [page.page_number for page in read_pages(document_id)] == [1, 2, 3, 4]

    async def test_splitting_is_enqueued_once_for_the_document(
        self, committed_batch: CommittedBatch, recorder: Recorder, tmp_path: Path
    ) -> None:
        document_id = committed_batch.add_document(
            build_multi_certificate_pdf(tmp_path / "many.pdf", copies=4), mime_type=MIME_PDF
        )

        await run_stage(document_id, recorder)

        assert recorder.task_names == [TASK_SPLIT_DOCUMENT]


class TestWordDocuments:
    async def test_a_docx_is_read_without_ocr(
        self, committed_batch: CommittedBatch, recorder: Recorder, tmp_path: Path
    ) -> None:
        document_id = committed_batch.add_document(
            build_docx(tmp_path / "birth.docx"), mime_type=MIME_DOCX
        )

        result = await run_stage(document_id, recorder)

        assert result.ocr_pages == []
        (page,) = read_pages(document_id)
        assert page.extraction_source is PageExtractionSource.DOCX
        assert page.needed_ocr is False
        assert "Ayesha Noor Malik" in page.raw_text
        assert read_document(document_id).status is DocumentStatus.SPLITTING

    async def test_a_damaged_docx_fails_that_document_alone(
        self, committed_batch: CommittedBatch, recorder: Recorder, tmp_path: Path
    ) -> None:
        document_id = committed_batch.add_document(
            build_docx_broken(tmp_path / "broken.docx"), mime_type=MIME_DOCX
        )

        result = await run_stage(document_id, recorder)

        assert result.failed_code == "document_corrupt"
        document = read_document(document_id)
        assert document.status is DocumentStatus.FAILED
        assert document.error_code == "document_corrupt"
        assert document.error_message
        assert recorder.calls == []

    async def test_a_legacy_doc_without_libreoffice_says_so(
        self, committed_batch: CommittedBatch, recorder: Recorder, tmp_path: Path
    ) -> None:
        # This deployment has no LibreOffice, which must be a clear failure on that
        # document rather than a crash or a silent empty extraction.
        legacy = tmp_path / "legacy.doc"
        legacy.write_bytes(bytes.fromhex("d0cf11e0a1b11ae1") + bytes(2048))
        document_id = committed_batch.add_document(legacy, mime_type=MIME_DOC)

        result = await run_stage(document_id, recorder)

        assert result.failed_code in {"conversion_unavailable", "document_corrupt"}
        document = read_document(document_id)
        assert document.status is DocumentStatus.FAILED
        assert document.error_code == result.failed_code

    async def test_a_failure_updates_the_batch_counters(
        self, committed_batch: CommittedBatch, recorder: Recorder, tmp_path: Path
    ) -> None:
        document_id = committed_batch.add_document(
            build_docx_broken(tmp_path / "broken.docx"), mime_type=MIME_DOCX
        )

        await run_stage(document_id, recorder)

        batch = read_batch(committed_batch.batch_id)
        assert batch.failed_count == 1
        assert batch.file_count == 1
        # The only document is finished, so the batch is finished too - with errors.
        assert batch.status is BatchStatus.COMPLETED_WITH_ERRORS
        del document_id


class TestScansAndImages:
    async def test_an_image_only_pdf_is_routed_to_ocr(
        self, committed_batch: CommittedBatch, recorder: Recorder, tmp_path: Path
    ) -> None:
        document_id = committed_batch.add_document(
            build_scanned_pdf(tmp_path / "scan.pdf", skew_degrees=1.5, noise=0.02),
            mime_type=MIME_PDF,
        )

        result = await run_stage(document_id, recorder)

        assert result.ocr_pages == [1]
        assert read_document(document_id).status is DocumentStatus.OCR
        assert recorder.calls == [
            (TASK_OCR_PAGE, {"document_id": str(document_id), "page_number": 1})
        ]

    async def test_a_page_awaiting_ocr_claims_no_extraction_source(
        self, committed_batch: CommittedBatch, recorder: Recorder, tmp_path: Path
    ) -> None:
        document_id = committed_batch.add_document(
            build_scanned_pdf(tmp_path / "scan.pdf"), mime_type=MIME_PDF
        )

        await run_stage(document_id, recorder)

        (page,) = read_pages(document_id)
        assert page.extraction_source is PageExtractionSource.NONE
        assert page.needed_ocr is True
        assert page.raw_text == ""
        assert page.width is not None and page.height is not None

    async def test_a_standalone_image_becomes_one_ocr_page(
        self, committed_batch: CommittedBatch, recorder: Recorder, tmp_path: Path
    ) -> None:
        document_id = committed_batch.add_document(
            build_scanned_image(tmp_path / "scan.png"), mime_type="image/png"
        )

        result = await run_stage(document_id, recorder)

        assert result.page_count == 1
        assert result.ocr_pages == [1]
        (page,) = read_pages(document_id)
        assert page.extraction_source is PageExtractionSource.NONE
        assert page.needed_ocr is True
        layout = PageLayout.model_validate(page.layout_blocks_jsonb)
        assert layout.unit == "px"
        assert layout.engine == "none"

    async def test_a_jpeg_scan_is_accepted(
        self, committed_batch: CommittedBatch, recorder: Recorder, tmp_path: Path
    ) -> None:
        document_id = committed_batch.add_document(
            build_image(tmp_path / "page.jpg"), mime_type="image/jpeg"
        )

        result = await run_stage(document_id, recorder)

        assert result.ocr_pages == [1]
        assert read_document(document_id).status is DocumentStatus.OCR


class TestUrdu:
    @pytest.mark.skipif(
        arabic_font() is None, reason="no Arabic-script font is installed on this machine"
    )
    async def test_a_bilingual_certificate_keeps_its_urdu_values(
        self, committed_batch: CommittedBatch, recorder: Recorder, tmp_path: Path
    ) -> None:
        document_id = committed_batch.add_document(
            build_bilingual_pdf(tmp_path / "urdu.pdf"), mime_type=MIME_PDF
        )

        await run_stage(document_id, recorder)

        (page,) = read_pages(document_id)
        assert page.language == "eng+urd"
        assert page.needed_ocr is False
        for value in URDU_BIRTH.expected.values():
            if value in {"F", "2019-03-14"}:
                continue  # normalised values, not printed strings
            assert value in page.raw_text, value


class TestRunningTwice:
    async def test_a_redelivered_task_does_nothing_the_second_time(
        self, committed_batch: CommittedBatch, recorder: Recorder, tmp_path: Path
    ) -> None:
        document_id = committed_batch.add_document(
            build_text_pdf(tmp_path / "birth.pdf"), mime_type=MIME_PDF
        )

        first = await run_stage(document_id, recorder)
        second = await run_stage(document_id, recorder)

        assert first.ran is True
        assert second.ran is False, "the document had already left this stage"
        assert len(read_pages(document_id)) == 1
        assert recorder.task_names == [TASK_SPLIT_DOCUMENT]

    async def test_a_crashed_run_is_resumed_without_duplicating_pages(
        self, committed_batch: CommittedBatch, recorder: Recorder, tmp_path: Path
    ) -> None:
        # A worker that died mid-stage leaves the document in EXTRACTING_TEXT; the
        # redelivered task must be able to claim it again and overwrite its pages.
        document_id = committed_batch.add_document(
            build_multi_certificate_pdf(tmp_path / "many.pdf", copies=2),
            mime_type=MIME_PDF,
            status=DocumentStatus.EXTRACTING_TEXT,
        )

        result = await run_stage(document_id, recorder)

        assert result.ran is True
        assert [page.page_number for page in read_pages(document_id)] == [1, 2]

    async def test_a_document_that_no_longer_exists_is_not_an_error(
        self, committed_batch: CommittedBatch, recorder: Recorder
    ) -> None:
        result = await run_stage(uuid.uuid4(), recorder)

        assert result.ran is False
        assert recorder.calls == []
        del committed_batch


class TestMissingBytes:
    async def test_a_document_whose_bytes_are_gone_fails_with_guidance(
        self, committed_batch: CommittedBatch, recorder: Recorder, tmp_path: Path
    ) -> None:
        # The retention sweep, or a bucket someone emptied: permanent, not worth a
        # retry, and the operator needs to know to upload the file again.
        document_id = committed_batch.add_document(
            build_text_pdf(tmp_path / "birth.pdf"), mime_type=MIME_PDF, upload=False
        )

        result = await run_stage(document_id, recorder)

        assert result.failed_code == "not_found"
        document = read_document(document_id)
        assert document.status is DocumentStatus.FAILED
        assert document.error_code == "not_found"
        assert recorder.calls == []
