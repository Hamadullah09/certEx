"""Turning pages into pixels.

Resolution is the point of this module: OCR accuracy falls off sharply below about
300 DPI, so a PDF page must come back at the size that was asked for and an uploaded
scan must have its own resolution believed - except when the file lies about it, which
phone photographs and screenshots routinely do.
"""

from __future__ import annotations

import io
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from certex.core.errors import CorruptDocumentError
from certex.pipeline.ocr.rasterize import (
    encode_jpeg,
    rasterize_image,
    rasterize_pdf_page,
)
from tests.fixtures.builders import build_corrupt_pdf, build_multi_certificate_pdf, build_text_pdf
from tests.fixtures.corpus import build_scanned_image

pytestmark = pytest.mark.unit

A4_WIDTH_INCHES = 8.27
A4_HEIGHT_INCHES = 11.69


class TestPdfPages:
    @pytest.mark.parametrize("dpi", [150, 300, 600])
    def test_the_page_comes_back_at_the_requested_resolution(
        self, tmp_path: Path, dpi: int
    ) -> None:
        page = rasterize_pdf_page(build_text_pdf(tmp_path / "birth.pdf"), 1, dpi=dpi)
        assert page.dpi == dpi
        assert page.width == pytest.approx(A4_WIDTH_INCHES * dpi, rel=0.02)
        assert page.height == pytest.approx(A4_HEIGHT_INCHES * dpi, rel=0.02)

    def test_the_image_is_single_channel_bytes(self, tmp_path: Path) -> None:
        page = rasterize_pdf_page(build_text_pdf(tmp_path / "birth.pdf"), 1, dpi=150)
        assert page.image.ndim == 2, "OCR reads one channel; colour would be wasted work"
        assert page.image.dtype == np.uint8
        assert page.source == "pdf"

    def test_the_page_has_ink_on_it(self, tmp_path: Path) -> None:
        # A stride bug would produce a skewed or blank image; ink and paper both
        # being present is the cheapest guard against that.
        page = rasterize_pdf_page(build_text_pdf(tmp_path / "birth.pdf"), 1, dpi=150)
        assert int(page.image.min()) < 64, "no dark pixels: the page rendered blank"
        assert int(page.image.max()) > 200, "no light pixels: the page rendered black"

    def test_rows_are_not_shifted_by_the_renderer_stride(self, tmp_path: Path) -> None:
        # MuPDF pads each row; reading the padding as pixels shears the page. A sheared
        # render turns the wide white margin at the top into a diagonal.
        page = rasterize_pdf_page(build_text_pdf(tmp_path / "birth.pdf"), 1, dpi=150)
        top_margin = page.image[:40, :]
        assert int(top_margin.min()) > 200, "the top margin should be blank paper"

    def test_a_later_page_can_be_rendered(self, tmp_path: Path) -> None:
        path = build_multi_certificate_pdf(tmp_path / "many.pdf", copies=3)
        page = rasterize_pdf_page(path, 3, dpi=100)
        assert page.width > 0

    def test_asking_for_a_page_that_is_not_there(self, tmp_path: Path) -> None:
        with pytest.raises(CorruptDocumentError):
            rasterize_pdf_page(build_text_pdf(tmp_path / "birth.pdf"), 2, dpi=100)

    def test_a_damaged_file_is_reported(self, tmp_path: Path) -> None:
        with pytest.raises(CorruptDocumentError):
            rasterize_pdf_page(build_corrupt_pdf(tmp_path / "bad.pdf"), 1, dpi=100)


class TestImages:
    def test_a_scan_keeps_its_own_resolution(self, tmp_path: Path) -> None:
        path = build_scanned_image(tmp_path / "scan.png", dpi=300)
        page = rasterize_image(path)
        assert page.dpi == 300
        assert page.source == "image"
        assert page.image.ndim == 2

    def test_a_resolution_the_file_does_not_state_is_estimated(self, tmp_path: Path) -> None:
        # A screenshot or a phone photograph carries no DPI tag at all.
        source = build_scanned_image(tmp_path / "scan.png", dpi=300)
        stripped = tmp_path / "untagged.png"
        with Image.open(source) as image:
            image.save(stripped)  # no dpi= argument, so no tag is written

        page = rasterize_image(stripped)
        assert 250 <= page.dpi <= 350, "a page-width image should be read as around 300 DPI"

    def test_a_resolution_the_file_lies_about_is_ignored(self, tmp_path: Path) -> None:
        # Some scanners tag a 300 DPI page as 72 DPI. Believing that would skip the
        # upscale decision and read a page at the wrong scale.
        source = build_scanned_image(tmp_path / "scan.png", dpi=300)
        lying = tmp_path / "lying.png"
        with Image.open(source) as image:
            image.save(lying, dpi=(72, 72))

        page = rasterize_image(lying)
        assert page.dpi >= 250

    def test_a_frame_of_a_multi_page_scan(self, tmp_path: Path) -> None:
        first = Image.open(build_scanned_image(tmp_path / "a.png", dpi=150)).convert("L")
        second = first.rotate(180)
        multipage = tmp_path / "both.tiff"
        first.save(multipage, save_all=True, append_images=[second], dpi=(150, 150))

        page_one = rasterize_image(multipage, frame=1)
        page_two = rasterize_image(multipage, frame=2)
        assert page_one.image.shape == page_two.image.shape
        assert not np.array_equal(page_one.image, page_two.image)

    def test_a_damaged_image_is_reported(self, tmp_path: Path) -> None:
        broken = tmp_path / "broken.png"
        broken.write_bytes(b"\x89PNG\r\n\x1a\n" + b"rubbish" * 20)
        with pytest.raises(CorruptDocumentError):
            rasterize_image(broken)


class TestPageImages:
    def test_the_review_image_is_a_readable_jpeg(self, tmp_path: Path) -> None:
        page = rasterize_pdf_page(build_text_pdf(tmp_path / "birth.pdf"), 1, dpi=150)
        payload = encode_jpeg(page.image)

        assert payload[:2] == b"\xff\xd8", "JPEG magic"
        with Image.open(io.BytesIO(payload)) as decoded:
            assert decoded.size == (page.width, page.height)
            assert decoded.mode == "L"
