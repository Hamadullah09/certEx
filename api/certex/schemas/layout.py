"""Page layout: words, lines, blocks and tables with their positions.

Every text source - a PDF text layer, a Word document, OCR - produces the same
structure, stored in ``page_texts.layout_blocks_jsonb``. The rules engine reads
positions from it ("the value is to the right of this label"), and the review pane
draws its bounding boxes over the rendered page.

Coordinates are fractions of the page, origin top-left, so a box lines up with the
page image at any rendering resolution. A Word document has no geometry at all;
its layout carries synthetic positions derived from reading order and table
columns, marked by ``unit == "synthetic"``, so position-based rules still behave
sensibly but nothing ever draws them over an image.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "LAYOUT_VERSION",
    "BBox",
    "Block",
    "LayoutEngine",
    "LayoutUnit",
    "Line",
    "PageLayout",
    "Table",
    "TableCell",
    "Word",
]

LAYOUT_VERSION = 1

LayoutUnit = Literal["pt", "px", "synthetic"]
LayoutEngine = Literal["pdfplumber", "pymupdf", "docx", "tesseract", "none"]


class BBox(BaseModel):
    """An axis-aligned box as fractions of page width and height."""

    model_config = ConfigDict(frozen=True)

    x0: float = Field(ge=0.0, le=1.0)
    y0: float = Field(ge=0.0, le=1.0)
    x1: float = Field(ge=0.0, le=1.0)
    y1: float = Field(ge=0.0, le=1.0)

    @classmethod
    def from_absolute(
        cls, x0: float, y0: float, x1: float, y1: float, *, width: float, height: float
    ) -> BBox:
        """Normalise a box given in page units, clamping to the page."""

        def clamp(value: float) -> float:
            return round(min(max(value, 0.0), 1.0), 5)

        left, right = sorted((x0, x1))
        top, bottom = sorted((y0, y1))
        return cls(
            x0=clamp(left / width),
            y0=clamp(top / height),
            x1=clamp(right / width),
            y1=clamp(bottom / height),
        )

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.y1 - self.y0

    @property
    def center_x(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def center_y(self) -> float:
        return (self.y0 + self.y1) / 2

    def union(self, other: BBox) -> BBox:
        return BBox(
            x0=min(self.x0, other.x0),
            y0=min(self.y0, other.y0),
            x1=max(self.x1, other.x1),
            y1=max(self.y1, other.y1),
        )

    def vertical_overlap(self, other: BBox) -> float:
        """Overlap of the two boxes' vertical extents, as a fraction of the shorter one."""
        overlap = min(self.y1, other.y1) - max(self.y0, other.y0)
        shorter = min(self.height, other.height)
        if shorter <= 0:
            return 0.0
        return max(0.0, overlap) / shorter

    @staticmethod
    def enclosing(boxes: list[BBox]) -> BBox | None:
        if not boxes:
            return None
        result = boxes[0]
        for box in boxes[1:]:
            result = result.union(box)
        return result


class Word(BaseModel):
    model_config = ConfigDict(frozen=True)

    text: str
    bbox: BBox
    confidence: float | None = Field(
        default=None,
        ge=0.0,
        le=100.0,
        description="OCR confidence on a 0-100 scale; None for text read from a text layer.",
    )
    line: int = Field(ge=0, description="Index into PageLayout.lines.")


class Line(BaseModel):
    model_config = ConfigDict(frozen=True)

    text: str
    bbox: BBox
    words: list[int] = Field(description="Indices into PageLayout.words, in reading order.")
    block: int = Field(ge=0, description="Index into PageLayout.blocks.")


class Block(BaseModel):
    model_config = ConfigDict(frozen=True)

    bbox: BBox
    lines: list[int]


class TableCell(BaseModel):
    model_config = ConfigDict(frozen=True)

    text: str
    bbox: BBox | None = None


class Table(BaseModel):
    model_config = ConfigDict(frozen=True)

    bbox: BBox | None = None
    rows: list[list[TableCell]]


class PageLayout(BaseModel):
    """Everything known about the text on one page."""

    model_config = ConfigDict(frozen=True)

    version: int = LAYOUT_VERSION
    page_number: int = Field(ge=1)
    width: float | None = Field(default=None, description="Page width in `unit`, if known.")
    height: float | None = Field(default=None, description="Page height in `unit`, if known.")
    unit: LayoutUnit
    engine: LayoutEngine
    words: list[Word] = Field(default_factory=list)
    lines: list[Line] = Field(default_factory=list)
    blocks: list[Block] = Field(default_factory=list)
    tables: list[Table] = Field(default_factory=list)

    @property
    def text(self) -> str:
        """The page's text in reading order, one line per line."""
        return "\n".join(line.text for line in self.lines)

    @property
    def mean_word_confidence(self) -> float | None:
        scores = [word.confidence for word in self.words if word.confidence is not None]
        return sum(scores) / len(scores) if scores else None

    @classmethod
    def empty(cls, page_number: int, *, engine: LayoutEngine, unit: LayoutUnit) -> PageLayout:
        return cls(page_number=page_number, engine=engine, unit=unit)
