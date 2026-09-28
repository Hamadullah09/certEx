"""Grouping positioned words into lines, columns and blocks, in reading order.

The builder is fed synthetic word boxes here, so each rule can be stated on its own:
what shares a line, where a column ends, which direction a column reads, and where a
space belongs. The real engines are exercised in test_pdf_native.py.

Geometry convention: page 600x800, origin top-left, a 12pt line whose glyphs are 6pt
wide - close enough to a real 12pt font that the builder's ratios behave as they do
on a page.
"""

from __future__ import annotations

import pytest

from certex.pipeline.text.layout_builder import PositionedWord, build_layout
from certex.schemas.layout import BBox, PageLayout, Table, TableCell

pytestmark = pytest.mark.unit

WIDTH = 600.0
HEIGHT = 800.0
SIZE = 12.0
CHAR = 6.0
SPACE = 5.0
"""A word space: wider than the builder's 0.15 x line-height threshold."""


def place(
    text: str,
    x0: float,
    y0: float,
    *,
    size: float = SIZE,
    line_key: tuple[int, ...] | None = None,
    order: int = 0,
    confidence: float | None = None,
) -> PositionedWord:
    return PositionedWord(
        text=text,
        x0=x0,
        y0=y0,
        x1=x0 + len(text) * CHAR,
        y1=y0 + size,
        confidence=confidence,
        line_key=line_key,
        order=order,
    )


def ltr_run(
    texts: list[str],
    *,
    x: float,
    y: float,
    space: float = SPACE,
    line_key: tuple[int, ...] | None = None,
    first_order: int = 0,
) -> list[PositionedWord]:
    """Words laid out left to right, in logical order."""
    words: list[PositionedWord] = []
    cursor = x
    for index, text in enumerate(texts):
        words.append(place(text, cursor, y, line_key=line_key, order=first_order + index))
        cursor += len(text) * CHAR + space
    return words


def rtl_run(
    texts: list[str],
    *,
    right: float,
    y: float,
    space: float = SPACE,
    line_key: tuple[int, ...] | None = None,
    first_order: int = 0,
) -> list[PositionedWord]:
    """Words laid out right to left - printed Urdu - given in logical order."""
    words: list[PositionedWord] = []
    cursor = right
    for index, text in enumerate(texts):
        width = len(text) * CHAR
        words.append(place(text, cursor - width, y, line_key=line_key, order=first_order + index))
        cursor -= width + space
    return words


def layout_of(
    words: list[PositionedWord],
    *,
    engine_order: bool = False,
    tables: list[Table] | None = None,
    width: float = WIDTH,
    height: float = HEIGHT,
) -> PageLayout:
    return build_layout(
        words,
        page_number=1,
        width=width,
        height=height,
        unit="pt",
        engine="pymupdf" if engine_order else "pdfplumber",
        tables=tables,
        engine_order=engine_order,
    )


class TestLines:
    def test_words_on_one_baseline_make_one_line(self) -> None:
        words = ltr_run(["Name", "of", "Child"], x=50, y=100)
        layout = layout_of(words)
        assert [line.text for line in layout.lines] == ["Name of Child"]
        assert [layout.words[index].text for index in layout.lines[0].words] == [
            "Name",
            "of",
            "Child",
        ]

    def test_input_order_does_not_matter(self) -> None:
        words = ltr_run(["Name", "of", "Child"], x=50, y=100)
        assert layout_of(list(reversed(words))).lines[0].text == "Name of Child"

    def test_lines_are_ordered_down_the_page(self) -> None:
        words = ltr_run(["third"], x=50, y=300) + ltr_run(["first"], x=50, y=100)
        words += ltr_run(["second"], x=50, y=200)
        assert [line.text for line in layout_of(words).lines] == ["first", "second", "third"]

    def test_a_taller_word_still_shares_the_line(self) -> None:
        # A heading-sized value beside a small label: the boxes overlap vertically.
        words = [place("Sex", 50, 100), place("Female", 100, 96, size=20)]
        assert len(layout_of(words).lines) == 1

    def test_a_word_on_the_next_baseline_starts_a_line(self) -> None:
        words = [place("one", 50, 100), place("two", 50, 118)]
        assert [line.text for line in layout_of(words).lines] == ["one", "two"]

    def test_empty_and_blank_words_are_dropped(self) -> None:
        words = [place("Sex", 50, 100), place("   ", 100, 100), place("", 150, 100)]
        layout = layout_of(words)
        assert [word.text for word in layout.words] == ["Sex"]

    def test_word_text_is_normalised(self) -> None:
        soft_hyphen = chr(0x00AD)
        words = [place(f"BC{soft_hyphen}2019", 50, 100)]
        assert layout_of(words).lines[0].text == "BC-2019"

    def test_page_text_joins_the_lines(self) -> None:
        words = ltr_run(["Sex:", "Female"], x=50, y=100) + ltr_run(["Registrar:"], x=50, y=140)
        assert layout_of(words).text == "Sex: Female\nRegistrar:"


class TestColumns:
    def test_a_wide_gap_separates_label_from_value(self) -> None:
        # 150pt of white space between the two cells of a form row.
        words = ltr_run(["Certificate", "No."], x=50, y=100) + ltr_run(
            ["BC-2019-004471"], x=300, y=100
        )
        layout = layout_of(words)
        assert [line.text for line in layout.lines] == ["Certificate No. BC-2019-004471"]

    def test_a_word_space_does_not_separate_columns(self) -> None:
        words = ltr_run(["Union", "Council", "42"], x=50, y=100)
        assert layout_of(words).lines[0].text == "Union Council 42"

    def test_touching_words_are_joined_without_a_space(self) -> None:
        # MuPDF hands back "42" and the Urdu comma as separate words with no gap.
        words = [place("42", 300, 100), place("،", 312, 100)]
        assert layout_of(words).lines[0].text == "42،"


class TestRightToLeft:
    def test_an_urdu_line_reads_from_the_right(self) -> None:
        words = rtl_run(["عائشہ", "نور", "ملک"], right=550, y=100)
        assert layout_of(words).lines[0].text == "عائشہ نور ملک"

    def test_an_urdu_row_reads_its_columns_from_the_right(self) -> None:
        label = rtl_run(["بچے", "کا", "نام"], right=550, y=100)
        value = rtl_run(["عائشہ", "نور", "ملک"], right=300, y=100)
        assert layout_of(label + value).lines[0].text == "بچے کا نام عائشہ نور ملک"

    def test_an_english_label_keeps_its_urdu_value_after_it(self) -> None:
        label = ltr_run(["Name", "of", "Child"], x=50, y=100)
        value = rtl_run(["عائشہ", "نور", "ملک"], right=550, y=100)
        assert layout_of(label + value).lines[0].text == "Name of Child عائشہ نور ملک"

    def test_urdu_inside_an_english_label_reads_in_its_own_direction(self) -> None:
        # "Name of Child / بچے کا نام" printed as one cell.
        words = ltr_run(["Name", "of", "Child", "/"], x=50, y=100)
        words += rtl_run(["بچے", "کا", "نام"], right=250, y=100)
        assert layout_of(words).lines[0].text == "Name of Child / بچے کا نام"

    def test_a_number_between_urdu_words_stays_in_the_urdu_run(self) -> None:
        words = rtl_run(["یونین", "کونسل", "42", "لاہور"], right=550, y=100)
        assert layout_of(words).lines[0].text == "یونین کونسل 42 لاہور"

    def test_a_number_at_the_end_of_an_urdu_line_stays_at_the_end(self) -> None:
        words = rtl_run(["یونین", "کونسل", "42"], right=550, y=100)
        assert layout_of(words).lines[0].text == "یونین کونسل 42"

    def test_a_number_in_its_own_column_does_not_flip_an_urdu_row(self) -> None:
        # A serial number stamped in the left margin of an Urdu row: it has no
        # letters, so it follows the row's direction instead of leading it.
        row = rtl_run(["بچے", "کا", "نام"], right=550, y=100)
        serial = place("42", 60, 100)
        assert layout_of([*row, serial]).lines[0].text == "بچے کا نام 42"


class TestEngineOrder:
    """MuPDF and Tesseract report runs in logical order; that order is trusted."""

    def test_a_runs_own_order_wins_over_its_geometry(self) -> None:
        # One run, whose words the engine placed right to left.
        words = rtl_run(["عائشہ", "نور", "ملک"], right=550, y=100, line_key=(0, 0))
        assert layout_of(words, engine_order=True).lines[0].text == "عائشہ نور ملک"

    def test_runs_of_one_row_are_ordered_by_direction_not_by_index(self) -> None:
        # MuPDF breaks a bilingual row into direction runs, in whatever order suits
        # its content stream. The row must still read label then value.
        value = rtl_run(["عائشہ", "نور", "ملک"], right=550, y=100, line_key=(0, 1), first_order=10)
        label = ltr_run(["Name", "of", "Child"], x=50, y=100, line_key=(0, 0), first_order=0)
        layout = layout_of(value + label, engine_order=True)
        assert layout.lines[0].text == "Name of Child عائشہ نور ملک"

    def test_an_urdu_value_split_into_three_runs_reads_as_printed(self) -> None:
        # The real case: "یونین کونسل 42، لاہور" arrives as Urdu / number / Urdu.
        head = rtl_run(["یونین", "کونسل"], right=550, y=100, line_key=(0, 0), first_order=0)
        number = place("42", 430, 100, line_key=(0, 1), order=10)
        comma_and_city = rtl_run(["،", "لاہور"], right=430, y=100, line_key=(0, 2), first_order=20)
        layout = layout_of([*head, number, *comma_and_city], engine_order=True)
        assert layout.lines[0].text == "یونین کونسل 42، لاہور"

    def test_a_missing_run_key_falls_back_to_geometry(self) -> None:
        # One word without a key means the engine's runs cannot be trusted at all.
        words = ltr_run(["Sex", "Female"], x=50, y=100, line_key=(0, 0))
        words.append(place("(F)", 200, 100))
        assert layout_of(words, engine_order=True).lines[0].text == "Sex Female (F)"


class TestBlocks:
    def test_a_wide_vertical_gap_starts_a_block(self) -> None:
        heading = ltr_run(["CERTIFICATE", "OF", "BIRTH"], x=50, y=60)
        body = ltr_run(["Sex:", "Female"], x=50, y=200)
        layout = layout_of(heading + body)
        assert len(layout.blocks) == 2
        assert [line.block for line in layout.lines] == [0, 1]

    def test_consecutive_lines_share_a_block(self) -> None:
        words = ltr_run(["one"], x=50, y=100)
        words += ltr_run(["two"], x=50, y=118)
        words += ltr_run(["three"], x=50, y=136)
        layout = layout_of(words)
        assert len(layout.blocks) == 1
        assert layout.blocks[0].lines == [0, 1, 2]

    def test_a_block_encloses_its_lines(self) -> None:
        words = ltr_run(["one"], x=50, y=100) + ltr_run(["two"], x=50, y=118)
        layout = layout_of(words)
        block = layout.blocks[0]
        for line in layout.lines:
            assert block.bbox.x0 <= line.bbox.x0 and block.bbox.x1 >= line.bbox.x1
            assert block.bbox.y0 <= line.bbox.y0 and block.bbox.y1 >= line.bbox.y1


class TestGeometry:
    def test_coordinates_are_page_fractions(self) -> None:
        layout = layout_of([place("Sex", 60, 80)])
        box = layout.words[0].bbox
        assert box.x0 == pytest.approx(0.1, abs=1e-4)
        assert box.y0 == pytest.approx(0.1, abs=1e-4)
        assert box.x1 == pytest.approx((60 + 3 * CHAR) / WIDTH, abs=1e-4)
        assert box.y1 == pytest.approx((80 + SIZE) / HEIGHT, abs=1e-4)

    def test_a_box_beyond_the_page_is_clamped(self) -> None:
        layout = layout_of([place("Sex", WIDTH - 5, HEIGHT - 5)])
        box = layout.words[0].bbox
        assert box.x1 == 1.0
        assert box.y1 == 1.0

    def test_a_line_box_encloses_its_words(self) -> None:
        layout = layout_of(ltr_run(["Sex:", "Female"], x=50, y=100))
        expected = BBox.enclosing([word.bbox for word in layout.words])
        assert layout.lines[0].bbox == expected

    def test_page_size_is_recorded(self) -> None:
        layout = layout_of([place("Sex", 50, 100)])
        assert (layout.width, layout.height, layout.unit) == (WIDTH, HEIGHT, "pt")


class TestConfidence:
    def test_per_word_confidence_is_carried_through(self) -> None:
        words = [place("Sex", 50, 100, confidence=96.5), place("Female", 100, 100, confidence=88.5)]
        layout = layout_of(words)
        assert [word.confidence for word in layout.words] == [96.5, 88.5]
        assert layout.mean_word_confidence == pytest.approx(92.5)

    def test_native_text_has_no_confidence(self) -> None:
        assert layout_of([place("Sex", 50, 100)]).mean_word_confidence is None


class TestEmptyPages:
    def test_no_words_gives_an_empty_layout(self) -> None:
        layout = layout_of([])
        assert layout.words == [] and layout.lines == [] and layout.blocks == []
        assert layout.text == ""
        assert (layout.width, layout.height) == (WIDTH, HEIGHT)

    def test_an_unknown_page_size_is_not_invented(self) -> None:
        layout = layout_of([place("Sex", 50, 100)], width=0.0, height=0.0)
        assert layout.width is None and layout.height is None
        assert layout.words == []

    def test_tables_survive_an_empty_page(self) -> None:
        table = Table(rows=[[TableCell(text="Sex"), TableCell(text="Female")]])
        assert layout_of([], tables=[table]).tables == [table]
