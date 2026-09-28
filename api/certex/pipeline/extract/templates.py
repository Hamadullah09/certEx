"""Recognising a form, and reading it the way it was read last time.

Two things happen here.

**Fingerprinting.** Every copy of one office's form shares its printed furniture: the
title, the department line, the field labels. The values differ on every copy, and the
scan geometry differs slightly on every copy, so a fingerprint is built only from the
short printed lines that carry no data - and from *which* lines they are, not where they
landed on the page. The same form scanned twice fingerprints the same; a different
office's form does not.

**Applying rules.** A template says "find this anchor, then look this way". The anchor
is matched in the text, and the value is taken from the direction the rule names, within
the distance it allows. Nothing is positioned absolutely, so a page scanned at a
different size or a few millimetres over still reads correctly.

A template is trusted above the rules engine because it encodes a person's correction on
this exact form - but only where its anchor is actually found. A rule whose anchor has
disappeared produces nothing, and the rules engine's answer stands.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from typing import Final

from certex.enums import ExtractionMethod
from certex.pipeline.extract.candidates import Candidate, FieldSource
from certex.pipeline.extract.rules import UnitPage
from certex.pipeline.text.layout_builder import join_with_gaps
from certex.pipeline.text.normalize import normalize_text
from certex.schemas.layout import BBox, PageLayout
from certex.schemas.template import AnchorDirection, TemplateRule, TemplateRules

__all__ = [
    "FINGERPRINT_VERSION",
    "anchor_lines",
    "apply_template",
    "fingerprint_pages",
]

FINGERPRINT_VERSION: Final = 1
"""Part of every fingerprint, so a change to this recipe does not match old templates."""

_MAX_ANCHOR_LENGTH: Final = 60
"""Longer lines are usually values, or a value run together with its label."""

_MIN_ANCHOR_LENGTH: Final = 4
_MAX_ANCHORS: Final = 40
_DIGITS: Final = re.compile(r"\d")
_TEMPLATE_CONFIDENCE: Final = 0.95
"""A template encodes a person's correction on this form, and is trusted accordingly."""


def _label_part(text: str) -> str:
    """The printed furniture of a line: its label, without the value beside it."""
    head = re.split(r"[:]", text, maxsplit=1)[0]
    return normalize_text(head).strip()


def anchor_lines(layout: PageLayout) -> list[str]:
    """The lines that identify this form rather than this certificate.

    Digits are what vary between copies - names vary too, but they sit after a colon or
    in the value column, which the label split removes.
    """
    anchors: list[str] = []
    for line in layout.lines:
        candidate = _label_part(line.text)
        if not _MIN_ANCHOR_LENGTH <= len(candidate) <= _MAX_ANCHOR_LENGTH:
            continue
        if _DIGITS.search(candidate):
            continue
        lowered = candidate.lower()
        if lowered not in anchors:
            anchors.append(lowered)
    return anchors[:_MAX_ANCHORS]


def fingerprint_pages(pages: Sequence[UnitPage]) -> str:
    """A stable identity for the form these pages are printed on."""
    anchors = sorted({anchor for page in pages for anchor in anchor_lines(page.layout)})
    digest = hashlib.sha256()
    digest.update(f"v{FINGERPRINT_VERSION}|".encode())
    for anchor in anchors:
        digest.update(anchor.encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def _line_text_with_spans(layout: PageLayout, line_index: int) -> tuple[str, list[tuple[int, int]]]:
    line = layout.lines[line_index]
    words = [layout.words[index] for index in line.words if index < len(layout.words)]
    return join_with_gaps(
        [(word.text, word.bbox.x0, word.bbox.x1) for word in words],
        line_height=max(line.bbox.height, 1e-6),
    )


def _value_from_line(
    layout: PageLayout, line_index: int, *, after: int
) -> tuple[str, BBox | None] | None:
    text, spans = _line_text_with_spans(layout, line_index)
    if after >= len(text):
        return None
    tail = text[after:].strip(" :-/|")
    if not tail:
        return None
    line = layout.lines[line_index]
    words = [layout.words[index] for index in line.words if index < len(layout.words)]
    first_word = next(
        (position for position, (start, _end) in enumerate(spans) if start >= after), len(spans)
    )
    boxes = [word.bbox for word in words[first_word:]]
    return tail, BBox.enclosing(boxes)


def _apply_rule(page: UnitPage, rule: TemplateRule) -> Candidate | None:
    layout = page.layout
    anchor = rule.anchor.lower()

    if rule.direction is AnchorDirection.CELL_RIGHT:
        # A cell rule reads the table, whether or not the label also appears as a line
        # of text: a Word table and a ruled PDF report their cells differently.
        cell = _cell_right(layout, anchor)
        return _candidate(page, rule, cell) if cell else None

    for index, line in enumerate(layout.lines):
        text, _spans = _line_text_with_spans(layout, index)
        position = text.lower().find(anchor)
        if position == -1:
            continue

        found: tuple[str, BBox | None] | None = None
        if rule.direction is AnchorDirection.AFTER:
            found = _value_from_line(layout, index, after=position + len(anchor))
        elif rule.direction is AnchorDirection.BELOW and index + 1 < len(layout.lines):
            below = layout.lines[index + 1]
            if below.bbox.y0 - line.bbox.y1 <= rule.max_distance:
                found = _value_from_line(layout, index + 1, after=0)
        candidate = _candidate(page, rule, found)
        if candidate is not None:
            return candidate
    return None


def _candidate(
    page: UnitPage, rule: TemplateRule, found: tuple[str, BBox | None] | None
) -> Candidate | None:
    """One rule's answer, once a value has been located - or nothing."""
    if found is None:
        return None
    value, bbox = found
    if rule.pattern:
        match = re.search(rule.pattern, value)
        if match is None:
            # The form has changed under the rule; saying nothing is better than
            # storing whatever now sits in that position.
            return None
        value = match.group(0)
    return Candidate(
        field=rule.field,
        value=normalize_text(value),
        confidence=_TEMPLATE_CONFIDENCE,
        method=ExtractionMethod.TEMPLATE,
        source=FieldSource(
            page_number=page.page_number, snippet=value, bbox=bbox, label=rule.anchor
        ),
    )


def _cell_right(layout: PageLayout, anchor: str) -> tuple[str, BBox | None] | None:
    for table in layout.tables:
        for row in table.rows:
            if len(row) < 2:
                continue
            if anchor in row[0].text.lower():
                value = next((cell for cell in row[1:] if cell.text.strip()), None)
                if value is not None:
                    return value.text.strip(), value.bbox
    return None


def apply_template(pages: Sequence[UnitPage], rules: TemplateRules) -> dict[str, Candidate]:
    """Read every field this template knows how to find."""
    found: dict[str, Candidate] = {}
    for rule in rules.rules:
        for offset, page in enumerate(pages):
            if rule.page_offset and offset != rule.page_offset:
                continue
            candidate = _apply_rule(page, rule)
            if candidate is not None and candidate.value:
                found[rule.field] = candidate
                break
    return found
