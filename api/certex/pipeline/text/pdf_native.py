"""Native PDF text layers, one page at a time.

pdfplumber is the primary reader: it yields word-level boxes and detects tables,
which is what the positional rules need. It is not used everywhere, for two
reasons found on real files rather than assumed:

* **Right-to-left script.** pdfplumber returns Urdu in visual order - a Word-exported
  "عائشہ" comes back as "ہشئاع", characters reversed. PyMuPDF (MuPDF) applies the
  bidi algorithm and returns logical order, so any page carrying Arabic-script text
  is read with MuPDF instead.
* **Unreadable layers.** Some producers place text inside nested form XObjects that
  pdfminer - pdfplumber's parser - silently skips, returning no characters while
  MuPDF reads the same page correctly. A page where pdfplumber finds nothing falls
  back to MuPDF before anyone concludes the page needs OCR.

Damage is contained to the page it affects. MuPDF opens the file and is the
authority on its pages, because it repairs broken cross-reference tables that
pdfminer rejects outright; a page pdfminer cannot parse is read with MuPDF, and a
page neither can read yields an empty layout - which the quality check sends to
OCR - instead of failing the document.

Which engine read a page is recorded on its layout, so a surprising extraction is
traceable.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from pathlib import Path

import fitz
import pdfplumber
from pdfminer.psparser import PSException

from certex.core.errors import CorruptDocumentError
from certex.logging_setup import get_logger, safe_error
from certex.pipeline.text.layout_builder import PositionedWord, build_layout
from certex.pipeline.text.normalize import contains_arabic_script, normalize_text
from certex.schemas.layout import BBox, PageLayout, Table, TableCell

__all__ = ["iter_pdf_layouts", "pdf_page_count"]

logger = get_logger(__name__)

_PDFMINER_ERRORS: tuple[type[BaseException], ...] = (
    # pdfminer reports malformed content through its own exception family, and
    # through whatever a half-parsed object graph happens to raise.
    PSException,
    ValueError,
    KeyError,
    TypeError,
    IndexError,
    AttributeError,
    AssertionError,
    RecursionError,
)
_MUPDF_ERRORS: tuple[type[BaseException], ...] = (RuntimeError, ValueError, fitz.mupdf.FzErrorBase)


def pdf_page_count(path: Path) -> int:
    with fitz.open(str(path)) as document:
        return int(document.page_count)


def _box(raw: object, *, width: float, height: float) -> BBox | None:
    if not isinstance(raw, Sequence) or len(raw) != 4:
        return None
    try:
        x0, y0, x1, y1 = (float(value) for value in raw)
    except (TypeError, ValueError):
        return None
    return BBox.from_absolute(x0, y0, x1, y1, width=width, height=height)


def _to_cells(
    rows_text: object, rows_boxes: list[list[object]], *, width: float, height: float
) -> list[list[TableCell]]:
    rows: list[list[TableCell]] = []
    if not isinstance(rows_text, list):
        return rows
    for row_index, row in enumerate(rows_text):
        if not isinstance(row, list):
            continue
        boxes = rows_boxes[row_index] if row_index < len(rows_boxes) else []
        cells: list[TableCell] = []
        for cell_index, value in enumerate(row):
            text = normalize_text(value) if isinstance(value, str) else ""
            raw_box = boxes[cell_index] if cell_index < len(boxes) else None
            cells.append(TableCell(text=text, bbox=_box(raw_box, width=width, height=height)))
        if any(cell.text for cell in cells):
            rows.append(cells)
    return rows


def _plumber_tables(page: object, *, width: float, height: float) -> list[Table]:
    tables: list[Table] = []
    try:
        found = page.find_tables()  # type: ignore[attr-defined]
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        logger.info("pdf_native.table_detection_failed", error_type=safe_error(exc))
        return tables
    for table in found:
        boxes = [list(getattr(row, "cells", []) or []) for row in getattr(table, "rows", [])]
        rows = _to_cells(table.extract(), boxes, width=width, height=height)
        if rows:
            tables.append(Table(bbox=_box(table.bbox, width=width, height=height), rows=rows))
    return tables


def _mupdf_tables(page: object, *, width: float, height: float) -> list[Table]:
    tables: list[Table] = []
    try:
        finder = page.find_tables()  # type: ignore[attr-defined]
    except (RuntimeError, ValueError, KeyError, IndexError, TypeError) as exc:
        logger.info("pdf_native.table_detection_failed", error_type=safe_error(exc))
        return tables
    for table in getattr(finder, "tables", []):
        boxes = [list(getattr(row, "cells", []) or []) for row in getattr(table, "rows", [])]
        rows = _to_cells(table.extract(), boxes, width=width, height=height)
        if rows:
            tables.append(Table(bbox=_box(table.bbox, width=width, height=height), rows=rows))
    return tables


def _read_with_plumber(page: object, *, page_number: int) -> PageLayout:
    width = float(page.width)  # type: ignore[attr-defined]
    height = float(page.height)  # type: ignore[attr-defined]
    raw_words = page.extract_words(keep_blank_chars=False, use_text_flow=False)  # type: ignore[attr-defined]
    words = [
        PositionedWord(
            text=str(item["text"]),
            x0=float(item["x0"]),
            y0=float(item["top"]),
            x1=float(item["x1"]),
            y1=float(item["bottom"]),
            order=index,
        )
        for index, item in enumerate(raw_words)
    ]
    return build_layout(
        words,
        page_number=page_number,
        width=width,
        height=height,
        unit="pt",
        engine="pdfplumber",
        tables=_plumber_tables(page, width=width, height=height),
    )


_GAP_FACTOR = 0.3
"""A horizontal gap wider than this share of the font size ends a word."""


def _horizontal_gap(first: Sequence[float], second: Sequence[float]) -> float:
    """Distance between two character boxes along x, in either reading direction."""
    return max(second[0] - first[2], first[0] - second[2], 0.0)


class _WordAccumulator:
    """Collects one line's characters into words, in MuPDF's logical order."""

    __slots__ = ("_box", "_characters", "_line_key", "_previous", "words")

    def __init__(self, words: list[PositionedWord], line_key: tuple[int, int]) -> None:
        self.words = words
        self._line_key = line_key
        self._characters: list[str] = []
        self._box: list[float] = []
        self._previous: list[float] | None = None

    def flush(self) -> None:
        if self._characters:
            self.words.append(
                PositionedWord(
                    text="".join(self._characters),
                    x0=self._box[0],
                    y0=self._box[1],
                    x1=self._box[2],
                    y1=self._box[3],
                    line_key=self._line_key,
                    order=len(self.words),
                )
            )
        self._characters = []
        self._box = []

    def add(self, glyph: str, bounds: list[float], *, font_size: float) -> None:
        if not glyph or glyph.isspace():
            self.flush()
            self._previous = bounds
            return
        if self._previous is not None and _horizontal_gap(self._previous, bounds) > max(
            1.0, _GAP_FACTOR * font_size
        ):
            self.flush()
        self._characters.append(glyph)
        if self._box:
            self._box = [
                min(self._box[0], bounds[0]),
                min(self._box[1], bounds[1]),
                max(self._box[2], bounds[2]),
                max(self._box[3], bounds[3]),
            ]
        else:
            self._box = list(bounds)
        self._previous = bounds


def _mupdf_words(page: object) -> list[PositionedWord]:
    """Words rebuilt from MuPDF's characters, split at whitespace *and* at gaps.

    MuPDF's own word list splits only at whitespace characters. A table whose label
    cell ends right where its value cell begins has no whitespace between them, so
    "Certificate No." and "BC-2020-001122" fused into one word. Splitting where the
    glyphs are visibly apart restores the boundary. Characters are visited in
    MuPDF's logical order, so Urdu words keep their correct letter order.
    """
    raw = page.get_text("rawdict")  # type: ignore[attr-defined]
    words: list[PositionedWord] = []

    for block_number, block in enumerate(raw.get("blocks", [])):
        if block.get("type") != 0:
            continue
        for line_number, line in enumerate(block.get("lines", [])):
            accumulator = _WordAccumulator(words, (block_number, line_number))
            for span in line.get("spans", []):
                size = float(span.get("size", 10.0))
                for char in span.get("chars", []):
                    bounds = [float(value) for value in char.get("bbox", (0, 0, 0, 0))]
                    accumulator.add(str(char.get("c", "")), bounds, font_size=size)
            accumulator.flush()

    return words


def _read_with_mupdf(page: object, *, page_number: int) -> PageLayout:
    rect = page.rect  # type: ignore[attr-defined]
    width, height = float(rect.width), float(rect.height)
    words = _mupdf_words(page)
    return build_layout(
        words,
        page_number=page_number,
        width=width,
        height=height,
        unit="pt",
        engine="pymupdf",
        tables=_mupdf_tables(page, width=width, height=height),
        engine_order=True,
    )


def _page_characters(page: object) -> list[str]:
    chars = page.chars  # type: ignore[attr-defined]
    return [str(char.get("text", "")) for char in chars if isinstance(char, dict)]


def _open_plumber(path: Path) -> object | None:
    """pdfplumber's view of the file, or None when pdfminer cannot parse it."""
    try:
        return pdfplumber.open(str(path))
    except _PDFMINER_ERRORS as exc:
        logger.info("pdf_native.plumber_unavailable", error_type=safe_error(exc))
        return None


def _read_page(plumber_document: object | None, mupdf_document: object, index: int) -> PageLayout:
    page_number = index + 1
    if plumber_document is not None:
        plumber_page: object | None = None
        try:
            pages = plumber_document.pages  # type: ignore[attr-defined]
            if index < len(pages):
                plumber_page = pages[index]
                characters = _page_characters(plumber_page)
                right_to_left = any(contains_arabic_script(char) for char in characters)
                if characters and not right_to_left:
                    return _read_with_plumber(plumber_page, page_number=page_number)
        except _PDFMINER_ERRORS as exc:
            logger.info(
                "pdf_native.plumber_page_failed",
                page_number=page_number,
                error_type=safe_error(exc),
            )
        finally:
            if plumber_page is not None:
                # Release the page's parsed objects so a 500-page file does not
                # accumulate every page in memory.
                plumber_page.close()  # type: ignore[attr-defined]

    try:
        return _read_with_mupdf(
            mupdf_document.load_page(index),  # type: ignore[attr-defined]
            page_number=page_number,
        )
    except _MUPDF_ERRORS as exc:
        logger.warning(
            "pdf_native.page_unreadable", page_number=page_number, error_type=safe_error(exc)
        )
        return PageLayout(page_number=page_number, unit="pt", engine="none")


def iter_pdf_layouts(path: Path, *, first_page: int = 1) -> Iterator[PageLayout]:
    """Yield each page's text-layer layout, holding one page in memory at a time.

    Pages whose text layer is empty or unreadable yield an empty layout; deciding
    that such a page needs OCR is the caller's job, informed by
    :mod:`certex.pipeline.text.quality`.

    Raises :class:`CorruptDocumentError` only when the file cannot be opened at all.
    """
    try:
        mupdf_document = fitz.open(str(path))
    except _MUPDF_ERRORS as exc:
        raise CorruptDocumentError("The PDF could not be opened; it appears damaged.") from exc

    with mupdf_document:
        plumber_document = _open_plumber(path)
        try:
            for index in range(first_page - 1, int(mupdf_document.page_count)):
                yield _read_page(plumber_document, mupdf_document, index)
        finally:
            if plumber_document is not None:
                plumber_document.close()  # type: ignore[attr-defined]
