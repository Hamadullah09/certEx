"""Reading a PDF's own text layer.

Real generated PDFs throughout: a reportlab form, an Urdu certificate laid out by
MuPDF's HTML engine, a ruled table, a scanner's image-only page, and files damaged in
the ways exports really are damaged. Which engine read a page is asserted, because
the choice between pdfplumber and MuPDF is the part most likely to regress.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import fitz
import pytest
from pdfminer.psparser import PSException

from certex.core.errors import CorruptDocumentError
from certex.pipeline.text import pdf_native
from certex.pipeline.text.normalize import normalize_text
from certex.pipeline.text.pdf_native import iter_pdf_layouts, pdf_page_count
from certex.pipeline.text.quality import assess_text
from tests.fixtures.builders import (
    SAMPLES_BY_KEY,
    build_corrupt_pdf,
    build_multi_certificate_pdf,
    build_text_pdf,
    build_zero_page_pdf,
)
from tests.fixtures.corpus import (
    URDU_BIRTH,
    arabic_font,
    build_bilingual_pdf,
    build_scanned_pdf,
    build_table_pdf,
)

pytestmark = pytest.mark.unit

needs_arabic_font = pytest.mark.skipif(
    arabic_font() is None, reason="no Arabic-script font is installed on this machine"
)


def first_page(path: Path) -> Any:
    return next(iter_pdf_layouts(path))


class TestEnglishTextLayer:
    def test_read_with_pdfplumber(self, tmp_path: Path) -> None:
        layout = first_page(build_text_pdf(tmp_path / "birth.pdf"))
        assert layout.engine == "pdfplumber"
        assert layout.unit == "pt"
        assert layout.page_number == 1

    def test_every_label_and_value_land_on_one_line(self, tmp_path: Path) -> None:
        layout = first_page(build_text_pdf(tmp_path / "birth.pdf"))
        lines = [line.text for line in layout.lines]
        for label, value in SAMPLES_BY_KEY["birth_lahore"].lines:
            assert any(f"{label}:" in line and value in line for line in lines), label

    def test_the_heading_is_the_first_line(self, tmp_path: Path) -> None:
        layout = first_page(build_text_pdf(tmp_path / "birth.pdf"))
        assert layout.lines[0].text == "CERTIFICATE OF BIRTH"

    def test_page_size_and_word_boxes_are_recorded(self, tmp_path: Path) -> None:
        layout = first_page(build_text_pdf(tmp_path / "birth.pdf"))
        assert layout.width == pytest.approx(595.27, abs=0.5)  # A4 in points
        assert layout.height == pytest.approx(841.89, abs=0.5)
        assert layout.words
        for word in layout.words:
            assert 0.0 <= word.bbox.x0 < word.bbox.x1 <= 1.0
            assert 0.0 <= word.bbox.y0 < word.bbox.y1 <= 1.0
            assert word.confidence is None, "native text has no confidence"

    def test_every_word_points_at_its_line(self, tmp_path: Path) -> None:
        layout = first_page(build_text_pdf(tmp_path / "birth.pdf"))
        for index, word in enumerate(layout.words):
            assert word.line < len(layout.lines)
            assert index in layout.lines[word.line].words

    def test_the_text_layer_is_good_enough_to_skip_ocr(self, tmp_path: Path) -> None:
        layout = first_page(build_text_pdf(tmp_path / "birth.pdf"))
        assert not assess_text(layout.text, min_chars=100, min_dict_ratio=0.35).needs_ocr


class TestUrduTextLayer:
    @needs_arabic_font
    def test_read_with_mupdf_because_pdfplumber_reverses_urdu(self, tmp_path: Path) -> None:
        layout = first_page(build_bilingual_pdf(tmp_path / "urdu.pdf"))
        assert layout.engine == "pymupdf"

    @needs_arabic_font
    def test_every_row_reads_label_then_value(self, tmp_path: Path) -> None:
        layout = first_page(build_bilingual_pdf(tmp_path / "urdu.pdf"))
        lines = [line.text for line in layout.lines]
        for label, value in URDU_BIRTH.lines:
            wanted_label, wanted_value = normalize_text(label), normalize_text(value)
            row = next((line for line in lines if wanted_label in line), None)
            assert row is not None, f"label missing: {label}"
            assert wanted_value in row, f"value missing from {row!r}"

    @needs_arabic_font
    def test_urdu_values_are_canonical_so_a_typed_correction_matches(self, tmp_path: Path) -> None:
        layout = first_page(build_bilingual_pdf(tmp_path / "urdu.pdf"))
        # A reviewer typing the child's name must produce the same characters.
        assert normalize_text(URDU_BIRTH.expected["child_full_name"]) in layout.text

    @needs_arabic_font
    def test_a_cnic_survives_the_shared_hyphen_glyph(self, tmp_path: Path) -> None:
        layout = first_page(build_bilingual_pdf(tmp_path / "urdu.pdf"))
        assert "35201-1234567-1" in layout.text


class TestTables:
    def test_a_ruled_certificate_is_read_as_a_table(self, tmp_path: Path) -> None:
        layout = first_page(build_table_pdf(tmp_path / "table.pdf"))
        assert len(layout.tables) == 1
        table = layout.tables[0]
        expected = SAMPLES_BY_KEY["death_karachi"].lines
        assert len(table.rows) == len(expected)
        assert [[cell.text for cell in row] for row in table.rows] == [
            [label, value] for label, value in expected
        ]

    def test_table_cells_carry_boxes(self, tmp_path: Path) -> None:
        layout = first_page(build_table_pdf(tmp_path / "table.pdf"))
        cell = layout.tables[0].rows[0][1]
        assert cell.bbox is not None
        assert cell.bbox.x0 > layout.tables[0].rows[0][0].bbox.x0  # type: ignore[union-attr]

    def test_a_form_without_rules_has_no_tables(self, tmp_path: Path) -> None:
        assert first_page(build_text_pdf(tmp_path / "birth.pdf")).tables == []


class TestScans:
    def test_an_image_only_page_yields_an_empty_layout(self, tmp_path: Path) -> None:
        layout = first_page(build_scanned_pdf(tmp_path / "scan.pdf"))
        assert layout.words == []
        assert layout.text == ""

    def test_an_image_only_page_is_routed_to_ocr(self, tmp_path: Path) -> None:
        layout = first_page(build_scanned_pdf(tmp_path / "scan.pdf"))
        quality = assess_text(layout.text, min_chars=100, min_dict_ratio=0.35)
        assert quality.needs_ocr
        assert quality.reason == "too_few_chars"

    def test_the_page_size_still_comes_back(self, tmp_path: Path) -> None:
        # OCR needs it to rasterise at a known scale.
        layout = first_page(build_scanned_pdf(tmp_path / "scan.pdf", dpi=200))
        assert layout.width is not None and layout.width > 0
        assert layout.height is not None and layout.height > 0


class TestManyPages:
    def test_one_layout_per_page(self, tmp_path: Path) -> None:
        path = build_multi_certificate_pdf(tmp_path / "many.pdf", copies=5)
        layouts = list(iter_pdf_layouts(path))
        assert [layout.page_number for layout in layouts] == [1, 2, 3, 4, 5]
        assert pdf_page_count(path) == 5

    def test_reading_can_start_at_a_later_page(self, tmp_path: Path) -> None:
        path = build_multi_certificate_pdf(tmp_path / "many.pdf", copies=5)
        layouts = list(iter_pdf_layouts(path, first_page=4))
        assert [layout.page_number for layout in layouts] == [4, 5]

    def test_pages_are_read_one_at_a_time(self, tmp_path: Path, monkeypatch: Any) -> None:
        # A 500-page file must not be parsed up front just to look at page one.
        path = build_multi_certificate_pdf(tmp_path / "many.pdf", copies=20)
        calls: list[int] = []
        original = pdf_native._read_page

        def counting(plumber: Any, mupdf: Any, index: int) -> Any:
            calls.append(index)
            return original(plumber, mupdf, index)

        monkeypatch.setattr(pdf_native, "_read_page", counting)
        pages: Iterator[Any] = iter_pdf_layouts(path)
        next(pages)
        assert calls == [0]
        next(pages)
        assert calls == [0, 1]

    def test_each_page_holds_its_own_certificate(self, tmp_path: Path) -> None:
        path = build_multi_certificate_pdf(tmp_path / "many.pdf", copies=3)
        for layout in iter_pdf_layouts(path):
            assert "CERTIFICATE" in layout.text.upper()


class TestDamagedFiles:
    def test_a_truncated_pdf_is_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(CorruptDocumentError):
            list(iter_pdf_layouts(build_corrupt_pdf(tmp_path / "bad.pdf")))

    def test_a_pdf_with_no_pages_yields_nothing(self, tmp_path: Path) -> None:
        assert list(iter_pdf_layouts(build_zero_page_pdf(tmp_path / "empty.pdf"))) == []

    def test_text_inside_a_form_xobject_is_still_found(self, tmp_path: Path) -> None:
        # pdfminer silently returns no characters for text nested in a form XObject,
        # which is how some producers write it; MuPDF reads it correctly.
        source_path = build_text_pdf(tmp_path / "source.pdf")
        wrapped_path = tmp_path / "wrapped.pdf"
        with fitz.open(str(source_path)) as source, fitz.open() as wrapped:
            page = wrapped.new_page(width=source[0].rect.width, height=source[0].rect.height)
            page.show_pdf_page(page.rect, source, 0)
            wrapped.save(str(wrapped_path))

        layout = first_page(wrapped_path)
        assert layout.engine == "pymupdf"
        assert "CERTIFICATE OF BIRTH" in layout.text
        assert "BC-2019-004471" in layout.text

    def test_a_page_pdfminer_cannot_parse_falls_back_to_mupdf(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        def explode(page: Any, *, page_number: int) -> Any:
            raise PSException("malformed content stream")

        monkeypatch.setattr(pdf_native, "_read_with_plumber", explode)
        layout = first_page(build_text_pdf(tmp_path / "birth.pdf"))
        assert layout.engine == "pymupdf"
        assert "BC-2019-004471" in layout.text

    def test_a_file_pdfminer_cannot_open_is_read_by_mupdf(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # _open_plumber returns None when pdfminer rejects the file outright.
        monkeypatch.setattr(pdf_native, "_open_plumber", lambda path: None)
        layout = first_page(build_text_pdf(tmp_path / "birth.pdf"))
        assert layout.engine == "pymupdf"
        assert "BC-2019-004471" in layout.text

    def test_a_page_neither_engine_can_read_becomes_an_ocr_candidate(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # Each engine fails in its own vocabulary: pdfminer's exception family, and
        # MuPDF's RuntimeError.
        def plumber_explodes(page: Any, *, page_number: int) -> Any:
            raise PSException("malformed content stream")

        def mupdf_explodes(page: Any, *, page_number: int) -> Any:
            raise RuntimeError("cannot load page")

        monkeypatch.setattr(pdf_native, "_read_with_plumber", plumber_explodes)
        monkeypatch.setattr(pdf_native, "_read_with_mupdf", mupdf_explodes)
        layout = first_page(build_text_pdf(tmp_path / "birth.pdf"))
        assert layout.engine == "none"
        assert layout.text == ""
        assert assess_text(layout.text, min_chars=100, min_dict_ratio=0.35).needs_ocr
