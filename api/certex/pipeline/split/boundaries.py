"""Where one certificate ends and the next begins.

A records office hands over one PDF that is really two hundred certificates, and the
cost of getting this wrong is asymmetric: splitting one certificate into two leaves two
half-filled rows that a reviewer must find and merge, while running two certificates
together produces one row whose values silently come from two different people. Both
are bad; the second is worse, because nothing about the row looks wrong.

So the cascade is conservative. Each strategy is tried in order of how much it is worth
trusting, and a strategy only wins if its evidence covers the whole document:

1. **Bookmarks.** A PDF outline with an entry per certificate is the producer telling
   us the answer. Nothing else is this reliable.
2. **Evenly spaced headings.** "CERTIFICATE OF BIRTH" at the top of every second page,
   with the last certificate the same length as the rest, is a file of equal-length
   certificates. Only believed when page one has a heading - otherwise the pattern is
   measuring something else, such as a running page header.
3. **Serial numbers.** A new certificate number appearing at the top of a page starts a
   certificate. Only believed when every page carries one.
4. **The period behind irregular headings.** Headings at pages 1, 3, 7 and 11 of a
   twelve-page file are not four certificates of uneven length: they are six
   certificates whose second and fourth headings OCR failed to read. The common divisor
   of the gaps recovers the period, and splitting on it risks two half-rows a reviewer
   can merge instead of one row holding two people's details.
5. **Headings as they fell.** Only when no period explains them.

When nothing fits, the document is one certificate. That is the right answer for the
single scanned certificate per file that this system mostly sees, and for anything else
it is the answer a reviewer can fix with one split.
"""

from __future__ import annotations

import re
import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from functools import reduce
from itertools import pairwise
from math import gcd
from typing import Final

from certex.enums import BoundaryMethod
from certex.pipeline.text.normalize import normalize_text

__all__ = [
    "Boundaries",
    "PageSummary",
    "certificate_numbers",
    "detect_boundaries",
    "has_heading",
]

_HEADING_ZONE: Final = 0.35
"""Share of a page's lines counted as its heading area."""

_HEADING_LINES: Final = 6
"""At most this many lines count as the heading area, however long the page is."""

# A certificate's own title, in the shapes Pakistani records offices print it. Matched
# against normalised text, so Urdu look-alike letters have already been folded.
_HEADING_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"\bcertificate\s+of\s+(birth|death|marriage)\b", re.IGNORECASE),
    re.compile(r"\b(birth|death|marriage|nikah)\s+certificate\b", re.IGNORECASE),
    re.compile(r"\bnikah\s*nama\b", re.IGNORECASE),
    re.compile(r"\bcertificate\s+of\s+registration\s+of\s+(birth|death|marriage)\b", re.IGNORECASE),
    re.compile("سرٹیفکیٹ"),  # certificate
    re.compile("نکاح نامہ|نکاحنامہ"),  # nikah nama
)

# "Certificate No. BC-2019-004471", "Registration No REG/LHR/2019/88213", and the Urdu
# labels for both. The number itself is deliberately loose: every office formats it
# differently, and all this needs to know is whether it changed.
_NUMBER_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(
        r"(?:certificate|registration|serial|reg)\.?\s*(?:no|number|#)\.?\s*[:\-]?\s*"
        r"([A-Za-z0-9][A-Za-z0-9/\-]{3,})",
        re.IGNORECASE,
    ),
    re.compile(r"(?:سرٹیفکیٹ|رجسٹریشن)\s*نمبر\s*[:\-]?\s*([A-Za-z0-9][A-Za-z0-9/\-]{3,})"),
)


@dataclass(frozen=True, slots=True)
class PageSummary:
    """What boundary detection needs to know about one page."""

    page_number: int
    text: str

    @property
    def heading_text(self) -> str:
        """The top of the page, where a certificate announces itself."""
        lines = [line for line in self.text.splitlines() if line.strip()]
        if not lines:
            return ""
        take = max(1, min(_HEADING_LINES, round(len(lines) * _HEADING_ZONE)))
        return "\n".join(lines[:take])


@dataclass(frozen=True, slots=True)
class Boundaries:
    """The page ranges of one document's certificates, and how they were decided."""

    ranges: list[tuple[int, int]]
    method: BoundaryMethod
    confidence: float

    @property
    def count(self) -> int:
        return len(self.ranges)


def has_heading(text: str) -> bool:
    """Whether a certificate's own title appears in this text."""
    normalised = normalize_text(text)
    return any(pattern.search(normalised) for pattern in _HEADING_PATTERNS)


def certificate_numbers(text: str) -> list[str]:
    """Certificate or registration numbers printed in this text, in order."""
    normalised = normalize_text(text)
    found: list[str] = []
    for pattern in _NUMBER_PATTERNS:
        found.extend(match.group(1).strip(" .,:-") for match in pattern.finditer(normalised))
    return [value for value in found if value]


def _ranges_from_starts(starts: Sequence[int], last_page: int) -> list[tuple[int, int]]:
    ordered = sorted(set(starts))
    return [
        (start, (ordered[index + 1] - 1) if index + 1 < len(ordered) else last_page)
        for index, start in enumerate(ordered)
    ]


def _single(last_page: int) -> Boundaries:
    return Boundaries(
        ranges=[(1, last_page)],
        method=BoundaryMethod.SINGLE_DOCUMENT,
        confidence=1.0 if last_page == 1 else 0.6,
    )


def _from_bookmarks(bookmark_pages: Sequence[int], last_page: int) -> Boundaries | None:
    starts = sorted({page for page in bookmark_pages if 1 <= page <= last_page})
    if len(starts) < 2 or starts[0] != 1:
        # An outline that does not start at page one is a table of contents or a
        # partial index, not a list of certificates.
        return None
    return Boundaries(
        ranges=_ranges_from_starts(starts, last_page),
        method=BoundaryMethod.BOOKMARK,
        confidence=0.95,
    )


def _heading_starts(pages: Sequence[PageSummary]) -> list[int]:
    """Pages that announce a certificate, when the first page is one of them.

    A heading that first appears on page three is not marking certificates - it is a
    running header, a continuation notice, or a reference to another document.
    """
    starts = [page.page_number for page in pages if has_heading(page.heading_text)]
    return starts if len(starts) >= 2 and starts[0] == 1 else []


def _from_uniform_headings(pages: Sequence[PageSummary], last_page: int) -> Boundaries | None:
    """Headings at a constant spacing, with the final certificate the same length."""
    starts = _heading_starts(pages)
    if not starts:
        return None
    gaps = [second - first for first, second in pairwise(starts)]
    tail = last_page - starts[-1] + 1
    if len(set(gaps)) != 1 or gaps[0] != tail:
        return None
    return Boundaries(
        ranges=_ranges_from_starts(starts, last_page),
        method=BoundaryMethod.UNIFORM_PAGE_COUNT,
        # Evenly spaced headings are a structural signal, not just a textual one.
        confidence=0.92,
    )


def _from_content_headings(pages: Sequence[PageSummary], last_page: int) -> Boundaries | None:
    """Headings taken as they fell, once no period explains their spacing."""
    starts = _heading_starts(pages)
    if not starts:
        return None
    return Boundaries(
        ranges=_ranges_from_starts(starts, last_page),
        method=BoundaryMethod.CONTENT_HEADER,
        confidence=0.85,
    )


def _from_numbers(pages: Sequence[PageSummary], last_page: int) -> Boundaries | None:
    seen: list[tuple[int, str]] = []
    for page in pages:
        numbers = certificate_numbers(page.heading_text) or certificate_numbers(page.text)
        if not numbers:
            return None  # a page with no number: this signal does not cover the file
        seen.append((page.page_number, numbers[0]))

    starts: list[int] = []
    previous: str | None = None
    for page_number, number in seen:
        if number != previous:
            starts.append(page_number)
        previous = number
    if len(starts) < 2 or starts[0] != 1:
        return None
    return Boundaries(
        ranges=_ranges_from_starts(starts, last_page),
        method=BoundaryMethod.SERIAL_NUMBER,
        confidence=0.88,
    )


def _from_stride(pages: Sequence[PageSummary], last_page: int) -> Boundaries | None:
    """The period that explains irregularly spaced headings.

    When every gap between headings is a multiple of one number, and that number divides
    the file, the headings that are missing are missing because OCR could not read them -
    not because those certificates are longer.
    """
    starts = _heading_starts(pages)
    if len(starts) < 3:
        return None
    gaps = [second - first for first, second in pairwise(starts)]
    if len(set(gaps)) == 1:
        return None  # regular spacing is not this strategy's business
    stride = reduce(gcd, gaps)
    if stride < 1 or last_page % stride or (last_page // stride) <= len(starts):
        return None
    agreement = gaps.count(stride) / len(gaps)
    return Boundaries(
        ranges=_ranges_from_starts(range(1, last_page + 1, stride), last_page),
        method=BoundaryMethod.FIXED_STRIDE,
        # Weaker than a heading on every certificate: this is an inference from a
        # rhythm, so it is offered for review rather than trusted.
        confidence=round(0.55 + 0.15 * agreement, 2),
    )


def detect_boundaries(
    pages: Sequence[PageSummary], *, bookmark_pages: Sequence[int] = ()
) -> Boundaries:
    """Split one document's pages into certificates.

    ``bookmark_pages`` are the 1-based pages a PDF outline points at, if any.
    """
    if not pages:
        return _single(1)
    last_page = max(page.page_number for page in pages)
    if last_page == 1:
        return _single(1)

    for candidate in (
        _from_bookmarks(bookmark_pages, last_page),
        _from_uniform_headings(pages, last_page),
        _from_numbers(pages, last_page),
        _from_stride(pages, last_page),
        _from_content_headings(pages, last_page),
    ):
        if candidate is not None and candidate.count > 1:
            return candidate
    return _single(last_page)


def mean_pages_per_unit(boundaries: Boundaries) -> float:
    """Average pages per certificate; used in logs to spot a runaway split."""
    if not boundaries.ranges:  # pragma: no cover - ranges are never empty
        return 0.0
    return statistics.fmean(end - start + 1 for start, end in boundaries.ranges)
