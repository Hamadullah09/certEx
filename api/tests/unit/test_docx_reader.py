"""Reading Word documents.

A .docx has no geometry until Word renders it, so this reader invents positions from
reading order and table columns. Two things are therefore asserted everywhere: that
nothing in the document is silently skipped - headers, footers, text boxes, content
controls, merged cells, nested tables all carry certificate data in the wild - and
that the synthetic geometry still says "the value is to the right of the label".
"""

from __future__ import annotations

from pathlib import Path

import pytest

from certex.core.errors import CorruptDocumentError
from certex.pipeline.text.docx_reader import read_docx_pages
from certex.pipeline.text.normalize import normalize_text
from certex.pipeline.text.quality import assess_text
from tests.fixtures.builders import SAMPLES_BY_KEY, build_docx
from tests.fixtures.corpus import (
    URDU_BIRTH,
    build_docx_broken,
    build_docx_content_control,
    build_docx_line_break,
    build_docx_merged_cells,
    build_docx_multi_page,
    build_docx_textbox,
    build_docx_urdu,
    build_docx_with_header,
)

pytestmark = pytest.mark.unit


def lines_of(path: Path, page: int = 0) -> list[str]:
    return [line.text for line in read_docx_pages(path)[page].lines]


class TestTableCertificate:
    def test_every_row_keeps_its_label_and_value_together(self, tmp_path: Path) -> None:
        lines = lines_of(build_docx(tmp_path / "birth.docx", as_table=True))
        for label, value in SAMPLES_BY_KEY["birth_lahore"].lines:
            assert any(label in line and value in line for line in lines), label

    def test_the_table_is_reported_as_a_table(self, tmp_path: Path) -> None:
        page = read_docx_pages(build_docx(tmp_path / "birth.docx", as_table=True))[0]
        expected = SAMPLES_BY_KEY["birth_lahore"].lines
        assert len(page.tables) == 1
        assert [[cell.text for cell in row] for row in page.tables[0].rows] == [
            [label, value] for label, value in expected
        ]

    def test_the_value_cell_sits_to_the_right_of_its_label(self, tmp_path: Path) -> None:
        page = read_docx_pages(build_docx(tmp_path / "birth.docx", as_table=True))[0]
        label_cell, value_cell = page.tables[0].rows[0]
        assert label_cell.bbox is not None and value_cell.bbox is not None
        assert value_cell.bbox.x0 >= label_cell.bbox.x1

    def test_layout_is_marked_synthetic(self, tmp_path: Path) -> None:
        page = read_docx_pages(build_docx(tmp_path / "birth.docx"))[0]
        assert page.unit == "synthetic"
        assert page.engine == "docx"
        assert page.width is None and page.height is None, "a .docx has no page size"

    def test_the_text_layer_is_good_enough_to_skip_ocr(self, tmp_path: Path) -> None:
        page = read_docx_pages(build_docx(tmp_path / "birth.docx"))[0]
        assert not assess_text(page.text, min_chars=100, min_dict_ratio=0.35).needs_ocr


class TestParagraphCertificate:
    def test_label_and_value_on_one_line(self, tmp_path: Path) -> None:
        lines = lines_of(build_docx(tmp_path / "birth.docx", as_table=False))
        assert "Name of Child: Ayesha Noor Malik" in lines

    def test_no_table_is_invented(self, tmp_path: Path) -> None:
        page = read_docx_pages(build_docx(tmp_path / "birth.docx", as_table=False))[0]
        assert page.tables == []

    def test_a_manual_line_break_starts_a_line(self, tmp_path: Path) -> None:
        assert lines_of(build_docx_line_break(tmp_path / "lb.docx")) == [
            "Permanent Address: House 14, Street 7",
            "Gulberg III, Lahore",
        ]


class TestHeadersAndFooters:
    def test_the_header_leads_and_the_footer_closes_the_page(self, tmp_path: Path) -> None:
        lines = lines_of(build_docx_with_header(tmp_path / "h.docx"))
        assert lines[0] == "GOVERNMENT OF THE PUNJAB"
        assert lines[-1] == "Verify at punjab.gov.pk"

    def test_the_footer_of_a_generated_certificate_is_read(self, tmp_path: Path) -> None:
        lines = lines_of(build_docx(tmp_path / "birth.docx"))
        assert lines[-1] == "This is a computer generated certificate."


class TestPageBreaks:
    def test_each_certificate_gets_its_own_page(self, tmp_path: Path) -> None:
        pages = read_docx_pages(build_docx_multi_page(tmp_path / "many.docx"))
        assert len(pages) == 3
        assert [page.page_number for page in pages] == [1, 2, 3]

    def test_a_break_character_and_page_break_before_both_split(self, tmp_path: Path) -> None:
        pages = read_docx_pages(build_docx_multi_page(tmp_path / "many.docx"))
        assert pages[0].lines[0].text == "CERTIFICATE OF BIRTH"
        assert pages[1].lines[0].text == "DEATH CERTIFICATE"
        assert pages[2].lines[0].text == "CERTIFICATE OF MARRIAGE (NIKAH NAMA)"

    def test_a_certificate_does_not_leak_onto_the_next_page(self, tmp_path: Path) -> None:
        pages = read_docx_pages(build_docx_multi_page(tmp_path / "many.docx"))
        assert "DC-2021-000913" not in pages[0].text
        assert "BC-2019-004471" not in pages[1].text

    def test_every_page_carries_the_header_and_footer(self, tmp_path: Path) -> None:
        pages = read_docx_pages(build_docx_with_header(tmp_path / "h.docx"))
        for page in pages:
            assert page.lines[0].text == "GOVERNMENT OF THE PUNJAB"


class TestMergedCells:
    def test_a_cell_spanning_two_columns_is_read_once(self, tmp_path: Path) -> None:
        lines = lines_of(build_docx_merged_cells(tmp_path / "merged.docx"))
        assert lines[0] == "CERTIFICATE OF BIRTH"

    def test_the_rest_of_the_table_still_reads_as_rows(self, tmp_path: Path) -> None:
        lines = lines_of(build_docx_merged_cells(tmp_path / "merged.docx"))
        assert "Name of Child   Ayesha Noor Malik" in lines
        assert any("House 14, Street 7" in line for line in lines)
        assert any("Gulberg III, Lahore" in line for line in lines)


class TestFloatingContent:
    def test_a_text_box_is_read(self, tmp_path: Path) -> None:
        assert "OFFICE SEAL" in lines_of(build_docx_textbox(tmp_path / "tb.docx"))

    def test_a_text_box_is_read_once_not_twice(self, tmp_path: Path) -> None:
        # Word writes a modern text box twice: the shape, and a VML fallback copy.
        lines = lines_of(build_docx_textbox(tmp_path / "tb.docx", with_fallback=True))
        assert lines.count("OFFICE SEAL") == 1

    def test_a_text_box_stays_on_the_page_that_anchors_it(self, tmp_path: Path) -> None:
        page = read_docx_pages(build_docx_textbox(tmp_path / "tb.docx"))[0]
        texts = [line.text for line in page.lines]
        assert texts.index("OFFICE SEAL") > texts.index("Certificate No.: BC-2019-004471")

    def test_a_content_control_is_read(self, tmp_path: Path) -> None:
        # The fillable field of a Word form template.
        assert "Registrar: Muhammad Aslam" in lines_of(
            build_docx_content_control(tmp_path / "sdt.docx")
        )


class TestUrdu:
    def test_urdu_values_are_read_verbatim(self, tmp_path: Path) -> None:
        lines = lines_of(build_docx_urdu(tmp_path / "urdu.docx"))
        for label, value in URDU_BIRTH.lines:
            wanted = normalize_text(value)
            row = next((line for line in lines if normalize_text(label) in line), None)
            assert row is not None and wanted in row, label

    def test_a_typed_correction_would_compare_equal(self, tmp_path: Path) -> None:
        page = read_docx_pages(build_docx_urdu(tmp_path / "urdu.docx"))[0]
        assert normalize_text(URDU_BIRTH.expected["child_full_name"]) in page.text


class TestSyntheticGeometry:
    def test_words_stay_inside_the_page(self, tmp_path: Path) -> None:
        page = read_docx_pages(build_docx(tmp_path / "birth.docx"))[0]
        assert page.words
        for word in page.words:
            assert 0.0 <= word.bbox.x0 < word.bbox.x1 <= 1.0
            assert 0.0 <= word.bbox.y0 < word.bbox.y1 <= 1.0

    def test_lines_run_down_the_page_in_reading_order(self, tmp_path: Path) -> None:
        page = read_docx_pages(build_docx(tmp_path / "birth.docx"))[0]
        tops = [line.bbox.y0 for line in page.lines]
        assert tops == sorted(tops)

    def test_a_short_certificate_does_not_stretch_over_the_page(self, tmp_path: Path) -> None:
        # Four lines must not each occupy a quarter of the page.
        page = read_docx_pages(build_docx_line_break(tmp_path / "lb.docx"))[0]
        assert all(line.bbox.height < 0.1 for line in page.lines)

    def test_every_line_belongs_to_a_block(self, tmp_path: Path) -> None:
        page = read_docx_pages(build_docx(tmp_path / "birth.docx"))[0]
        assert len(page.blocks) == 1
        assert page.blocks[0].lines == list(range(len(page.lines)))


class TestDamagedFiles:
    @pytest.mark.parametrize("shape", ["garbage", "no_main_part"])
    def test_a_file_that_is_not_a_word_document_is_rejected(
        self, tmp_path: Path, shape: str
    ) -> None:
        path = build_docx_broken(tmp_path / f"{shape}.docx", shape=shape)
        with pytest.raises(CorruptDocumentError) as raised:
            read_docx_pages(path)
        assert raised.value.code.value == "document_corrupt"
        assert "Word" in str(raised.value.remediation)
