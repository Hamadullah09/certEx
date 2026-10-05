"""Turning a reviewer's correction into a rule for the next copy of the same form.

A records office prints the same form thousands of times. When a clerk fixes a value
the extractor read wrongly, the useful part is not the value - that certificate is now
correct either way - it is *where on the form that value was printed*. Learning that
once makes every later copy of the same form read correctly without anybody touching
it.

What gets learned is deliberately narrow. A rule is written only when the corrected
text can be found on the page and the printed words beside it look like a label: short,
wordy, and carrying no digits of their own. Anything else - a value the reviewer typed
from the paper because the scan never contained it, a value that appears in three
places, a "label" that is really another certificate's number - teaches nothing, and a
wrong rule is worse than no rule because it would be applied with high confidence to
every copy of the form.

Nothing here writes to the database, and nothing here decides *whether* to learn. This
module answers one question: given these pages and this corrected value, what rule
would find it again?
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Final

from certex.pipeline.extract.rules import UnitPage
from certex.pipeline.extract.templates import label_part, line_text
from certex.pipeline.text.normalize import normalize_text
from certex.schemas.layout import PageLayout
from certex.schemas.template import AnchorDirection, TemplateRule

__all__ = ["learn_rules"]

_MIN_ANCHOR_LENGTH: Final = 3
"""Lower than the threshold fingerprinting uses, and deliberately so. A three-letter
*line* is noise, which is why the fingerprint ignores it; a three-letter *label* is
"Sex" or "Age", which are printed on most of these forms."""

_MAX_ANCHOR_LENGTH: Final = 60
"""Longer than this is a sentence, or a label run together with somebody's value."""

_MIN_VALUE_LENGTH: Final = 1
_DIGITS: Final = re.compile(r"\d")
_LETTERS: Final = re.compile(r"[^\W\d_]", re.UNICODE)
_TRIM: Final = " \t:-/|.,"

_BELOW_GAP_MARGIN: Final = 2.0
"""A learned BELOW rule allows twice the gap it saw, so a slightly taller rescan of the
same form still reads. Much more than that and the rule starts reaching past the line it
was learned from into whatever follows."""


def _is_label(text: str) -> bool:
    """Whether this looks like printed furniture rather than somebody's data.

    Digits are the strongest signal available: a form's labels are words, and the parts
    that change between copies - numbers, dates, CNICs - are not.
    """
    return (
        _MIN_ANCHOR_LENGTH <= len(text) <= _MAX_ANCHOR_LENGTH
        and not _DIGITS.search(text)
        and _LETTERS.search(text) is not None
    )


def _occurrence(haystack: str, needle: str) -> int:
    """Where ``needle`` starts in ``haystack``, as a standalone run of text.

    Bounded on both sides so a one-letter value like the "F" of a sex field anchors on
    the F that stands alone, not on the f inside "Father".
    """
    if not needle:
        return -1
    pattern = re.compile(rf"(?<![^\W_]){re.escape(needle)}(?![^\W_])", re.IGNORECASE | re.UNICODE)
    match = pattern.search(haystack)
    return match.start() if match else -1


def _from_table(layout: PageLayout, value: str) -> tuple[str, AnchorDirection] | None:
    """A value sitting in the cell beside its label.

    Checked before the text lines because a ruled form reports its cells as a table,
    and reading a table row as a line of text joins the label and the value with a gap
    whose width is meaningless.
    """
    for table in layout.tables:
        for row in table.rows:
            if len(row) < 2:
                continue
            anchor = label_part(row[0].text)
            if not _is_label(anchor):
                continue
            for cell in row[1:]:
                if normalize_text(cell.text).strip(_TRIM).casefold() == value.casefold():
                    return anchor, AnchorDirection.CELL_RIGHT
    return None


def _from_lines(layout: PageLayout, value: str) -> tuple[str, AnchorDirection, float] | None:
    """A value printed after its label, or on the line under it."""
    for index in range(len(layout.lines)):
        text = line_text(layout, index)
        position = _occurrence(text, value)
        if position == -1:
            continue

        head = text[:position].strip(_TRIM)
        if _is_label(head):
            return head, AnchorDirection.AFTER, 0.0

        # The value starts the line, so the label is the line above it. Only worth a
        # rule when the two lines sit close together: a label at the top of the page
        # and a value at the bottom are not a pair, they are a coincidence.
        if not head and index > 0:
            above = layout.lines[index - 1]
            anchor = label_part(above.text)
            gap = layout.lines[index].bbox.y0 - above.bbox.y1
            if gap >= 0 and _is_label(anchor):
                return anchor, AnchorDirection.BELOW, gap
    return None


def _rule_for(pages: Sequence[UnitPage], field: str, value: str) -> TemplateRule | None:
    cleaned = normalize_text(value).strip(_TRIM)
    if len(cleaned) < _MIN_VALUE_LENGTH:
        return None

    for page in pages:
        found = _from_table(page.layout, cleaned)
        if found is not None:
            anchor, direction = found
            return TemplateRule(field=field, anchor=anchor, direction=direction)

        located = _from_lines(page.layout, cleaned)
        if located is not None:
            anchor, direction, gap = located
            if direction is AnchorDirection.BELOW:
                return TemplateRule(
                    field=field,
                    anchor=anchor,
                    direction=direction,
                    max_distance=min(1.0, max(gap * _BELOW_GAP_MARGIN, 0.01)),
                )
            return TemplateRule(field=field, anchor=anchor, direction=direction)
    return None


def learn_rules(pages: Sequence[UnitPage], values: Mapping[str, str | None]) -> list[TemplateRule]:
    """A rule for every corrected value this form can be shown to contain.

    Fields the pages do not contain are skipped rather than guessed at. A clerk reading
    a number off the paper because the scan was unreadable has taught us about this
    certificate, not about the form.
    """
    rules: list[TemplateRule] = []
    for field, value in values.items():
        if not value:
            continue
        rule = _rule_for(pages, field, value)
        if rule is not None:
            rules.append(rule)
    return rules
