"""Which kind of certificate is this?

Everything downstream branches on the answer: which fields are looked for, which
validation rules apply, which CSV columns the row fills. Getting it wrong produces a
row of empty columns rather than a visible error, so the classifier is built to say "I
do not know" rather than to guess.

How it decides:

* Phrases are matched against the unit's normalised text, with a phrase counted once
  however often it appears - a form that repeats "Date of Birth" three times is not
  three times as much a birth certificate.
* A phrase in the heading area counts double. A title at the top of the page is the
  certificate naming itself; the same words further down are usually a reference to
  another document ("attach a copy of the birth certificate").
* OCR mangles headings, so a heading line that no pattern matches exactly is compared
  against the titles with a fuzzy ratio, and a close match counts as the title once.
* The winner must be clear of the runner-up. A death certificate that mentions the
  deceased's date of birth should not read as a birth certificate on a tie.

The confidence is the product of two honest measures: how much evidence was found at
all, and how clearly the winner beat the runner-up. Both are needed - a page with one
weak phrase and no competition is not a confident classification.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final

from rapidfuzz import fuzz

from certex.enums import CertificateType, ClassificationMethod
from certex.pipeline.classify.keywords import KEYWORDS, TITLE_WEIGHT
from certex.pipeline.text.normalize import normalize_text

__all__ = ["Classification", "classify_text"]

_HEADING_LINES: Final = 6
"""Most lines the heading area can span, however long the document is."""

_HEADING_MULTIPLIER: Final = 2.0

_MIN_LINES_FOR_HEADING: Final = 3
"""Below this, a text has no heading to speak of and nothing earns the heading bonus."""

_STRONG_SCORE: Final = 10.0
"""Evidence at which coverage is full: a title in the heading, counted double, plus two
fields only this kind of certificate has.

A title on its own reaches about half of it, deliberately: a covering note that mentions
"birth certificate" should not be classified as confidently as a document laid out as
one."""

_MIN_MARGIN: Final = 1.5
"""How far the winner must be clear of the runner-up, in weighted points."""

_FUZZY_THRESHOLD: Final = 85.0
"""Similarity at which an OCR-mangled heading still counts as a title."""

_MAX_CONFIDENCE: Final = 0.98
"""Keyword evidence never means certainty; a template match in a later stage can."""

_TITLES: Final[dict[CertificateType, tuple[str, ...]]] = {
    kind: tuple(phrase for phrase, weight in phrases if weight >= TITLE_WEIGHT)
    for kind, phrases in KEYWORDS.items()
}


@dataclass(frozen=True, slots=True)
class Classification:
    """What the classifier decided, and what it decided it from."""

    certificate_type: CertificateType
    confidence: float
    method: ClassificationMethod
    scores: dict[CertificateType, float] = field(default_factory=dict)
    matched: tuple[str, ...] = ()
    """The phrases that carried the decision, for explaining a surprising row."""


def _heading_of(text: str) -> str:
    """The top of a laid-out document, or nothing when there is no layout to speak of.

    A single sentence has no heading, and treating it as one would let a passing mention
    of "birth certificate" in a letter score as loudly as a certificate's own title.
    """
    lines = [line for line in text.splitlines() if line.strip()]
    if len(lines) < _MIN_LINES_FOR_HEADING:
        return ""
    # The top third, capped: on a twenty-line certificate that is the title block,
    # and on a four-line fragment it is one line rather than the whole page.
    take = max(1, min(_HEADING_LINES, len(lines) // 3))
    return "\n".join(lines[:take])


def _fuzzy_title(heading: str) -> tuple[CertificateType, str] | None:
    """A heading line that is nearly a title, as OCR leaves it."""
    best: tuple[float, CertificateType, str] | None = None
    for line in heading.splitlines():
        candidate = line.strip()
        if len(candidate) < 8:
            continue
        for kind, titles in _TITLES.items():
            for title in titles:
                score = fuzz.partial_ratio(title.lower(), candidate.lower())
                if score >= _FUZZY_THRESHOLD and (best is None or score > best[0]):
                    best = (score, kind, title)
    if best is None:
        return None
    return best[1], best[2]


def _score(text: str) -> tuple[dict[CertificateType, float], dict[CertificateType, list[str]]]:
    lowered = text.lower()
    heading = _heading_of(text).lower()
    scores: dict[CertificateType, float] = {}
    matched: dict[CertificateType, list[str]] = {}

    for kind, phrases in KEYWORDS.items():
        total = 0.0
        hits: list[str] = []
        for phrase, weight in phrases:
            needle = normalize_text(phrase).lower()
            if needle not in lowered:
                continue
            total += weight * (_HEADING_MULTIPLIER if needle in heading else 1.0)
            hits.append(phrase)
        scores[kind] = total
        matched[kind] = hits
    return scores, matched


def classify_text(text: str) -> Classification:
    """Classify one certificate's text."""
    normalised = normalize_text(text)
    if not normalised.strip():
        return Classification(
            certificate_type=CertificateType.OTHER,
            confidence=0.0,
            method=ClassificationMethod.DEFAULT,
        )

    scores, matched = _score(normalised)
    fuzzy = _fuzzy_title(_heading_of(normalised))
    if fuzzy is not None:
        kind, title = fuzzy
        if title not in matched[kind]:
            # An exact match already counted this title; only an OCR-mangled one adds.
            scores[kind] += TITLE_WEIGHT * _HEADING_MULTIPLIER
            matched[kind].append(f"~{title}")

    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    (winner, top), (_runner, second) = ranked[0], ranked[1]

    if top <= 0.0 or top - second < _MIN_MARGIN:
        return Classification(
            certificate_type=CertificateType.OTHER,
            confidence=0.0,
            method=ClassificationMethod.DEFAULT,
            scores=scores,
            matched=tuple(matched[winner]) if top > 0 else (),
        )

    coverage = min(1.0, top / _STRONG_SCORE)
    separation = top / (top + second)  # 0.5 when tied, 1.0 when unopposed
    confidence = min(_MAX_CONFIDENCE, round(coverage * (2 * separation - 1), 4))
    return Classification(
        certificate_type=winner,
        confidence=confidence,
        method=ClassificationMethod.KEYWORD,
        scores=scores,
        matched=tuple(matched[winner]),
    )
