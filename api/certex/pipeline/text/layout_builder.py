"""Group positioned words into lines and blocks, in reading order.

Every engine reports words with boxes; not every engine reports lines, and those
that do report them differently. pdfplumber gives bare words; Tesseract gives
block/paragraph/line numbers; MuPDF gives lines that break wherever the *writing
direction* changes, so one printed row of a bilingual form can arrive as several
"lines". This module turns all of them into the same :class:`PageLayout`.

Rules that matter for certificates:

* **Visual lines come from geometry.** Words are grouped by vertical overlap, so a
  label and its value printed on the same baseline share a line, whatever the
  engine thought.
* **Columns are separate.** A wide horizontal gap splits a line into columns - the
  label cell and the value cell of a form - and each column resolves its own
  direction.
* **Direction follows the text.** Within a column, runs of Urdu are read right to
  left and runs of English left to right, with numbers between Urdu words staying
  inside the Urdu run - the same outcome the Unicode bidi algorithm gives, applied to
  whole engine runs. Where the engine supplies logical order inside a run (MuPDF,
  Tesseract), that order is kept.
* **Spaces follow the page.** Words are joined with a space only where there is a
  visible gap, so "42" followed by an Urdu comma stays "42،".
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal

from certex.pipeline.text.normalize import is_arabic_script, normalize_text, normalize_word
from certex.schemas.layout import (
    BBox,
    Block,
    LayoutEngine,
    LayoutUnit,
    Line,
    PageLayout,
    Table,
    Word,
)

__all__ = ["PositionedWord", "build_layout", "join_with_gaps"]

_LINE_OVERLAP = 0.5
"""Minimum vertical overlap, as a fraction of the shorter word, to share a line."""

_BLOCK_GAP_FACTOR = 1.6
"""A vertical gap wider than this many median line heights starts a new block."""

_COLUMN_GAP_FACTOR = 3.0
"""A horizontal gap wider than this many line heights separates two columns."""

_SPACE_GAP_FACTOR = 0.15
"""A horizontal gap wider than this share of the line height is a word space."""

Direction = Literal["ltr", "rtl", "neutral"]


@dataclass(slots=True)
class PositionedWord:
    """A word in absolute page units, as an engine reported it."""

    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    confidence: float | None = None
    line_key: tuple[int, ...] | None = None
    """Engine-supplied run identity, e.g. (block, line) from MuPDF."""

    order: int = 0
    """Engine reading order. Trusted within a run when the engine emits logical order."""


@dataclass(slots=True)
class _LineDraft:
    words: list[PositionedWord] = field(default_factory=list)

    @property
    def y0(self) -> float:
        return min(word.y0 for word in self.words)

    @property
    def y1(self) -> float:
        return max(word.y1 for word in self.words)

    @property
    def x0(self) -> float:
        return min(word.x0 for word in self.words)

    @property
    def height(self) -> float:
        return self.y1 - self.y0


@dataclass(slots=True)
class _Run:
    """Consecutive words the engine placed in one run."""

    words: list[PositionedWord]

    @property
    def x0(self) -> float:
        return min(word.x0 for word in self.words)

    @property
    def x1(self) -> float:
        return max(word.x1 for word in self.words)

    @property
    def direction(self) -> Direction:
        return _direction(" ".join(word.text for word in self.words))


def join_with_gaps(
    pieces: Sequence[tuple[str, float, float]], *, line_height: float
) -> tuple[str, list[tuple[int, int]]]:
    """Join words into a line, and say where each one landed.

    A space is inserted only where the page has a visible gap, so "42" followed
    immediately by an Urdu comma stays "42،" while two words a space apart stay apart.
    Returns the joined text and, for each input word in order, the (start, end) offsets
    it occupies - which is how the rules engine finds the value that follows a label.
    """
    text_parts: list[str] = []
    spans: list[tuple[int, int]] = []
    cursor = 0
    threshold = _SPACE_GAP_FACTOR * line_height
    for index, (word, x0, x1) in enumerate(pieces):
        if index:
            previous = pieces[index - 1]
            gap = max(x0 - previous[2], previous[1] - x1, 0.0)
            if gap > threshold:
                text_parts.append(" ")
                cursor += 1
        spans.append((cursor, cursor + len(word)))
        text_parts.append(word)
        cursor += len(word)
    return "".join(text_parts), spans


def _direction(text: str) -> Direction:
    letters = [char for char in text if char.isalpha()]
    if not letters:
        return "neutral"
    arabic = sum(1 for char in letters if is_arabic_script(char))
    return "rtl" if arabic * 2 > len(letters) else "ltr"


def _overlap_fraction(a0: float, a1: float, b0: float, b1: float) -> float:
    overlap = min(a1, b1) - max(a0, b0)
    shorter = min(a1 - a0, b1 - b0)
    if shorter <= 0:
        return 1.0 if overlap >= 0 else 0.0
    return max(0.0, overlap) / shorter


def _horizontal_gap(left: PositionedWord | _Run, right: PositionedWord | _Run) -> float:
    return max(right.x0 - left.x1, left.x0 - right.x1, 0.0)


def _group_by_geometry(words: list[PositionedWord]) -> list[_LineDraft]:
    """Cluster words into visual lines by vertical overlap, top to bottom."""
    drafts: list[_LineDraft] = []
    for word in sorted(words, key=lambda item: ((item.y0 + item.y1) / 2, item.x0)):
        target = None
        for draft in reversed(drafts[-3:]):
            if _overlap_fraction(word.y0, word.y1, draft.y0, draft.y1) >= _LINE_OVERLAP:
                target = draft
                break
        if target is None:
            target = _LineDraft()
            drafts.append(target)
        target.words.append(word)
    return drafts


def _runs(words: list[PositionedWord], *, engine_order: bool) -> list[_Run]:
    """Split a visual line into runs: the engine's own, or one run per word."""
    if not engine_order:
        return [_Run(words=[word]) for word in sorted(words, key=lambda item: item.x0)]
    runs: list[_Run] = []
    current_key: tuple[int, ...] | None = None
    for word in sorted(words, key=lambda item: item.order):
        if not runs or word.line_key != current_key:
            runs.append(_Run(words=[]))
            current_key = word.line_key
        runs[-1].words.append(word)
    return runs


def _columns(runs: list[_Run], *, line_height: float) -> list[list[_Run]]:
    """Split visually sorted runs into columns at wide horizontal gaps."""
    ordered = sorted(runs, key=lambda run: run.x0)
    columns: list[list[_Run]] = []
    for run in ordered:
        if columns and _horizontal_gap(columns[-1][-1], run) > _COLUMN_GAP_FACTOR * line_height:
            columns.append([run])
        elif columns:
            columns[-1].append(run)
        else:
            columns.append([run])
    return columns


def _resolve(runs_left_to_right: list[_Run]) -> tuple[list[_Run], Direction]:
    """Order one column's runs for reading, returning them with the column direction.

    The base direction is that of the first strong run from the left: an English
    column reads left to right, an Urdu one right to left. Runs against the base
    direction - an Urdu name inside an English label, English inside Urdu - are read
    in their own direction, and neutral runs (numbers, punctuation) join the run
    they sit between. A column with no letters at all - a serial number in its own
    cell - reports "neutral", so it follows the row rather than steering it.
    """
    strong = [run.direction for run in runs_left_to_right if run.direction != "neutral"]
    if not strong:
        return list(runs_left_to_right), "neutral"
    base: Direction = strong[0]

    sequence = runs_left_to_right if base == "ltr" else list(reversed(runs_left_to_right))
    opposite: Direction = "rtl" if base == "ltr" else "ltr"
    directions = [run.direction for run in sequence]
    resolved: list[Direction] = []
    for index, direction in enumerate(directions):
        if direction != "neutral":
            resolved.append(direction)
            continue
        before = next((d for d in reversed(directions[:index]) if d != "neutral"), base)
        after = next((d for d in directions[index + 1 :] if d != "neutral"), base)
        resolved.append(opposite if before == after == opposite else base)

    ordered: list[_Run] = []
    index = 0
    while index < len(sequence):
        if resolved[index] != opposite:
            ordered.append(sequence[index])
            index += 1
            continue
        end = index
        while end < len(sequence) and resolved[end] == opposite:
            end += 1
        ordered.extend(reversed(sequence[index:end]))
        index = end
    return ordered, base


def _read_line(draft: _LineDraft, *, engine_order: bool) -> tuple[list[PositionedWord], str]:
    """Words of one visual line in reading order, and the line's text."""
    heights = [word.y1 - word.y0 for word in draft.words if word.y1 > word.y0]
    line_height = statistics.median(heights) if heights else 1.0
    runs = _runs(draft.words, engine_order=engine_order)
    columns = _columns(runs, line_height=line_height)

    resolved_columns = [_resolve(column) for column in columns]
    strong = [direction for _, direction in resolved_columns if direction != "neutral"]
    if strong and all(direction == "rtl" for direction in strong):
        # A wholly right-to-left row reads its columns from the right. Columns with
        # no letters take that direction with the rest of the row.
        resolved_columns.reverse()

    words: list[PositionedWord] = []
    text_parts: list[str] = []
    for column_runs, _direction_of_column in resolved_columns:
        # A run's words are already in reading order: the engine's logical order,
        # or a single word when the engine gave none.
        column_words = [word for run in column_runs for word in run.words]
        if not column_words:
            continue
        column_text, _spans = join_with_gaps(
            [(word.text, word.x0, word.x1) for word in column_words],
            line_height=line_height,
        )
        text_parts.append(column_text)
        words.extend(column_words)

    return words, normalize_text(" ".join(text_parts))


def build_layout(
    words: list[PositionedWord],
    *,
    page_number: int,
    width: float,
    height: float,
    unit: LayoutUnit,
    engine: LayoutEngine,
    tables: list[Table] | None = None,
    engine_order: bool = False,
) -> PageLayout:
    """Assemble a :class:`PageLayout` from positioned words.

    ``engine_order`` says each word's ``line_key`` and ``order`` describe runs in
    logical reading order, which is then trusted inside each run.
    """
    usable = [
        PositionedWord(
            text=normalize_word(word.text),
            x0=word.x0,
            y0=word.y0,
            x1=word.x1,
            y1=word.y1,
            confidence=word.confidence,
            line_key=word.line_key,
            order=word.order,
        )
        for word in words
    ]
    usable = [word for word in usable if word.text]
    if not usable or width <= 0 or height <= 0:
        return PageLayout(
            page_number=page_number,
            width=width or None,
            height=height or None,
            unit=unit,
            engine=engine,
            tables=tables or [],
        )

    trust_engine = engine_order and all(word.line_key is not None for word in usable)
    drafts = _group_by_geometry(usable)
    heights = [draft.height for draft in drafts if draft.height > 0]
    median_height = statistics.median(heights) if heights else 0.0

    out_words: list[Word] = []
    out_lines: list[Line] = []
    out_blocks: list[Block] = []
    block_lines: list[int] = []
    previous_bottom: float | None = None

    def close_block() -> None:
        if not block_lines:
            return
        enclosing = BBox.enclosing([out_lines[index].bbox for index in block_lines])
        if enclosing is not None:
            out_blocks.append(Block(bbox=enclosing, lines=list(block_lines)))
        block_lines.clear()

    for draft in sorted(drafts, key=lambda item: (item.y0, item.x0)):
        if (
            previous_bottom is not None
            and median_height > 0
            and draft.y0 - previous_bottom > _BLOCK_GAP_FACTOR * median_height
        ):
            close_block()

        ordered, line_text = _read_line(draft, engine_order=trust_engine)
        line_index = len(out_lines)
        word_indices: list[int] = []
        for word in ordered:
            word_indices.append(len(out_words))
            out_words.append(
                Word(
                    text=word.text,
                    bbox=BBox.from_absolute(
                        word.x0, word.y0, word.x1, word.y1, width=width, height=height
                    ),
                    confidence=word.confidence,
                    line=line_index,
                )
            )
        line_box = BBox.enclosing([out_words[index].bbox for index in word_indices])
        if line_box is None:  # pragma: no cover - a draft always has words
            continue
        out_lines.append(
            Line(text=line_text, bbox=line_box, words=word_indices, block=len(out_blocks))
        )
        block_lines.append(line_index)
        previous_bottom = draft.y1

    close_block()

    return PageLayout(
        page_number=page_number,
        width=width,
        height=height,
        unit=unit,
        engine=engine,
        words=out_words,
        lines=out_lines,
        blocks=out_blocks,
        tables=tables or [],
    )
