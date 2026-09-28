"""The OCR stage, end to end against Postgres, object storage and Tesseract.

The stage's own job is bookkeeping rather than reading: claim the page, write the row,
store the image the reviewer will see, and - if this page was the last one the document
was waiting on - move the document to splitting exactly once. Those are what is
asserted here; how well the page is read is covered in the unit tests.
"""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

import pytest
from sqlalchemy import select

from certex.config import get_settings
from certex.db.models import Document, PageText
from certex.db.session import session_scope
from certex.enums import DocumentStatus, OcrEngine, PageExtractionSource
from certex.pipeline.dispatch import TASK_SPLIT_DOCUMENT
from certex.pipeline.filetypes import MIME_PDF
from certex.pipeline.ocr.tesseract import resolve_binary
from certex.pipeline.stages.ocr_stage import OcrStageResult, run_ocr_page_stage
from certex.pipeline.stages.text_stage import run_text_stage
from certex.schemas.layout import PageLayout
from certex.storage.s3 import StorageKeys
from tests.fixtures.builders import SAMPLES_BY_KEY
from tests.fixtures.corpus import build_scanned_image, build_scanned_pdf
from tests.integration.conftest import CommittedBatch, Recorder

pytestmark = [
    pytest.mark.integration,
    pytest.mark.ocr,
    pytest.mark.skipif(
        resolve_binary(get_settings()) is None,
        reason="the tesseract binary is not installed on this machine",
    ),
]


async def run_text(document_id: uuid.UUID, recorder: Recorder) -> None:
    """Put a document through text extraction, which is what queues its OCR."""
    await asyncio.to_thread(run_text_stage, document_id, dispatch=recorder)


async def run_ocr(document_id: uuid.UUID, page: int, recorder: Recorder) -> OcrStageResult:
    return await asyncio.to_thread(run_ocr_page_stage, document_id, page, dispatch=recorder)


def read_document(document_id: uuid.UUID) -> Document:
    with session_scope() as session:
        document = session.get(Document, document_id)
        assert document is not None
        session.expunge(document)
        return document


def read_page(document_id: uuid.UUID, page_number: int = 1) -> PageText:
    with session_scope() as session:
        page = session.scalar(
            select(PageText).where(
                PageText.document_id == document_id, PageText.page_number == page_number
            )
        )
        assert page is not None
        session.expunge(page)
        return page


@pytest.fixture
async def scanned_document(
    committed_batch: CommittedBatch, recorder: Recorder, tmp_path: Path
) -> uuid.UUID:
    """An image-only scan that has been through text extraction and now awaits OCR."""
    document_id = committed_batch.add_document(
        build_scanned_pdf(tmp_path / "scan.pdf", skew_degrees=1.2), mime_type=MIME_PDF
    )
    await run_text(document_id, recorder)
    recorder.calls.clear()
    return document_id


class TestReadingAPage:
    async def test_the_page_row_holds_the_text_and_how_it_was_read(
        self, scanned_document: uuid.UUID, recorder: Recorder
    ) -> None:
        result = await run_ocr(scanned_document, 1, recorder)

        assert result.ran is True
        assert result.failed_code is None
        page = read_page(scanned_document)
        assert page.extraction_source is PageExtractionSource.OCR
        assert page.ocr_engine is OcrEngine.TESSERACT
        assert page.ocr_mean_confidence is not None and page.ocr_mean_confidence > 60.0
        assert page.needed_ocr is True, "the row still records that OCR was needed"
        assert "BC-2019-004471" in page.raw_text
        assert page.language == "eng"

    async def test_the_quality_signals_come_from_the_ocr_text(
        self, scanned_document: uuid.UUID, recorder: Recorder
    ) -> None:
        await run_ocr(scanned_document, 1, recorder)

        page = read_page(scanned_document)
        assert page.char_count > 300
        assert page.dict_hit_rate > 0.7
        assert page.alnum_ratio > 0.7

    async def test_the_stored_layout_is_a_valid_layout(
        self, scanned_document: uuid.UUID, recorder: Recorder
    ) -> None:
        await run_ocr(scanned_document, 1, recorder)

        page = read_page(scanned_document)
        layout = PageLayout.model_validate(page.layout_blocks_jsonb)
        assert layout.engine == "tesseract"
        assert layout.words and layout.lines
        assert page.width == layout.width and page.height == layout.height

    async def test_the_deskew_angle_is_recorded_with_the_page(
        self, scanned_document: uuid.UUID, recorder: Recorder
    ) -> None:
        # The review pane draws boxes over the straightened image, so it needs to know
        # the page was straightened and by how much.
        await run_ocr(scanned_document, 1, recorder)
        assert read_page(scanned_document).rotation == pytest.approx(-1.2, abs=0.4)

    async def test_most_of_the_certificate_is_read(
        self, scanned_document: uuid.UUID, recorder: Recorder
    ) -> None:
        await run_ocr(scanned_document, 1, recorder)

        text = read_page(scanned_document).raw_text
        expected = SAMPLES_BY_KEY["birth_lahore"].lines
        found = sum(1 for _label, value in expected if value in text)
        assert found >= len(expected) * 0.8, f"only {found}/{len(expected)} values read"


class TestTheReviewImage:
    async def test_the_page_image_is_stored_for_the_reviewer(
        self, scanned_document: uuid.UUID, recorder: Recorder, committed_batch: CommittedBatch
    ) -> None:
        await run_ocr(scanned_document, 1, recorder)

        key = StorageKeys.page_image(committed_batch.workspace_id, scanned_document, 1)
        committed_batch.storage_keys.append(key)
        assert committed_batch.storage.object_exists(key)
        payload = committed_batch.storage.get_bytes_if_exists(key)
        assert payload is not None and payload[:2] == b"\xff\xd8"


class TestFinishing:
    async def test_the_last_page_moves_the_document_on(
        self, scanned_document: uuid.UUID, recorder: Recorder
    ) -> None:
        result = await run_ocr(scanned_document, 1, recorder)

        assert result.advanced is True
        assert read_document(scanned_document).status is DocumentStatus.SPLITTING
        assert recorder.calls == [(TASK_SPLIT_DOCUMENT, {"document_id": str(scanned_document)})]

    async def test_a_document_waits_for_every_page(
        self, committed_batch: CommittedBatch, recorder: Recorder, tmp_path: Path
    ) -> None:
        page_one = build_scanned_image(tmp_path / "a.png", dpi=150)
        page_two = build_scanned_image(tmp_path / "b.png", dpi=150, sample_key="death_karachi")
        multipage = tmp_path / "both.tiff"
        from PIL import Image

        with Image.open(page_one) as first, Image.open(page_two) as second:
            first.save(multipage, save_all=True, append_images=[second], dpi=(150, 150))

        document_id = committed_batch.add_document(multipage, mime_type="image/tiff")
        await run_text(document_id, recorder)
        recorder.calls.clear()

        first_result = await run_ocr(document_id, 1, recorder)
        assert first_result.advanced is False
        assert read_document(document_id).status is DocumentStatus.OCR
        assert recorder.calls == []

        second_result = await run_ocr(document_id, 2, recorder)
        assert second_result.advanced is True
        assert read_document(document_id).status is DocumentStatus.SPLITTING
        assert recorder.task_names == [TASK_SPLIT_DOCUMENT]

    async def test_an_image_document_records_where_its_text_came_from(
        self, committed_batch: CommittedBatch, recorder: Recorder, tmp_path: Path
    ) -> None:
        document_id = committed_batch.add_document(
            build_scanned_image(tmp_path / "scan.png"), mime_type="image/png"
        )
        await run_text(document_id, recorder)
        recorder.calls.clear()

        await run_ocr(document_id, 1, recorder)

        assert read_page(document_id).extraction_source is PageExtractionSource.IMAGE_OCR


class TestRunningTwice:
    async def test_a_redelivered_task_does_not_read_the_page_again(
        self, scanned_document: uuid.UUID, recorder: Recorder
    ) -> None:
        first = await run_ocr(scanned_document, 1, recorder)
        second = await run_ocr(scanned_document, 1, recorder)

        assert first.ran is True
        assert second.ran is False
        assert recorder.task_names == [TASK_SPLIT_DOCUMENT], "splitting is enqueued once"

    async def test_the_same_page_is_not_read_twice_for_the_same_workspace(
        self, committed_batch: CommittedBatch, recorder: Recorder, tmp_path: Path
    ) -> None:
        # The same scan uploaded twice, or a batch re-processed: the second read comes
        # from the cache instead of spending the CPU again.
        scan = build_scanned_pdf(tmp_path / "scan.pdf")
        first_id = committed_batch.add_document(scan, mime_type=MIME_PDF)
        second_id = committed_batch.add_document(scan, mime_type=MIME_PDF)
        await run_text(first_id, recorder)
        await run_text(second_id, recorder)
        recorder.calls.clear()

        first = await run_ocr(first_id, 1, recorder)
        second = await run_ocr(second_id, 1, recorder)

        assert first.from_cache is False
        assert second.from_cache is True
        assert read_page(second_id).raw_text == read_page(first_id).raw_text
        assert read_page(second_id).content_hash == read_page(first_id).content_hash

    async def test_a_cached_page_still_gets_its_own_review_image(
        self, committed_batch: CommittedBatch, recorder: Recorder, tmp_path: Path
    ) -> None:
        scan = build_scanned_pdf(tmp_path / "scan.pdf")
        first_id = committed_batch.add_document(scan, mime_type=MIME_PDF)
        second_id = committed_batch.add_document(scan, mime_type=MIME_PDF)
        await run_text(first_id, recorder)
        await run_text(second_id, recorder)

        await run_ocr(first_id, 1, recorder)
        await run_ocr(second_id, 1, recorder)

        key = StorageKeys.page_image(committed_batch.workspace_id, second_id, 1)
        committed_batch.storage_keys.append(key)
        assert committed_batch.storage.object_exists(key), "the review pane needs this image"


class TestWhenItCannotRun:
    async def test_a_page_of_a_document_that_moved_on_is_skipped(
        self, scanned_document: uuid.UUID, recorder: Recorder
    ) -> None:
        with session_scope() as session:
            document = session.get(Document, scanned_document)
            assert document is not None
            document.status = DocumentStatus.COMPLETED

        result = await run_ocr(scanned_document, 1, recorder)

        assert result.ran is False
        assert recorder.calls == []

    async def test_a_page_that_does_not_exist_is_not_an_error(
        self, scanned_document: uuid.UUID, recorder: Recorder
    ) -> None:
        result = await run_ocr(scanned_document, 99, recorder)

        assert result.ran is False
        assert recorder.calls == []

    async def test_a_document_whose_bytes_are_gone_fails_with_guidance(
        self, scanned_document: uuid.UUID, recorder: Recorder, committed_batch: CommittedBatch
    ) -> None:
        for key in list(committed_batch.storage_keys):
            committed_batch.storage.delete(key)

        result = await run_ocr(scanned_document, 1, recorder)

        assert result.failed_code == "not_found"
        document = read_document(scanned_document)
        assert document.status is DocumentStatus.FAILED
        assert document.error_code == "not_found"
        assert recorder.calls == []
