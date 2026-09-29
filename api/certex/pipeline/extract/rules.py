"""Reading fields off a certificate by their printed labels.

A certificate is a form: a label, then its value. So the rules engine finds the label
and takes what follows it. Everything else here exists because real forms complicate
that one idea:

* **Labels are printed many ways.** "Father's Name", "Fathers Name", "Name of Father",
  "والد کا نام". Every spelling a field answers to lives in its field spec, and a label
  is matched exactly where possible and fuzzily where OCR has damaged it.
* **Bilingual forms label twice.** "Certificate No. / سرٹیفکیٹ نمبر BC-2019-004471" has
  the value after the *second* label, so once a label matches, the search keeps walking
  past any other name for the same field that follows it.
* **A heading is not a label.** "GOVERNMENT OF PAKISTAN - LOCAL GOVERNMENT DEPARTMENT"
  contains the words "local government", and taking what follows would file "DEPARTMENT"
  as the issuing authority. A real label is followed by a separator or by the white space
  of a value column; words that merely continue a sentence score lower, so the same
  field's proper label elsewhere on the page wins.
* **The value is not always to the right.** It may be in the next line, in the cell
  beside the label, or - on a right-to-left form - to the left. Reading order, which
  the layout stage has already resolved, decides: the value is what comes after the
  label in reading order.
* **Some values have a shape, and the shape is more reliable than the boundary.** A
  date, a time, an identity number and an amount are each recognisable on sight, so for
  those fields the value is the shape found after the label rather than everything found
  after it. On a speckled page that is the difference between "2019-04-02" and
  "Aon': ' | 12019-04-02".
* **A scan leaves specks between the label and the value.** "Registration No.:" read
  off a speckled page comes back as "T REG/LHR/2019/88213" - the value, with a mark on
  the paper read as a letter in front of it. A lone character before the rest of the
  value is dropped, unless it is an initial ("A. Khan") or the whole value ("F").
* **A label can appear twice.** "Date of Birth" is on a marriage certificate twice, once
  for each party. Where a more specific label ("Groom's Date of Birth") also matches, it
  wins, because it is the longer and more specific match.
* **A short name for a field is a trap on its own line.** "Name of Child" also contains
  "name", and reading after "name" would file "of Child" as the child's name. On any one
  line only the most specific label that matches is used, even when it yields no value -
  and an exact match always beats a damaged one.

Each candidate carries a confidence built from what was actually observed: how well the
label matched, where the value was found relative to it, and - for a scanned page - how
sure OCR was of the words it read.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Final

from rapidfuzz import fuzz

from certex.enums import CertificateType, ExtractionMethod
from certex.fields import FieldKind, FieldSchema, FieldSpec, schema_or_builtin
from certex.pipeline.extract.candidates import Candidate, FieldSource
from certex.pipeline.text.layout_builder import join_with_gaps
from certex.pipeline.text.normalize import arabic_script_ratio, normalize_text
from certex.schemas.layout import BBox, Line, PageLayout, Word

__all__ = ["UnitPage", "extract_extra_fields", "extract_with_rules"]

_FUZZY_THRESHOLD: Final = 85.0
"""Similarity at which a damaged label still counts as that label.

Measured, not guessed, and it cannot go lower. On a thirteen-character label two wrong
characters score about 85 - and so does the distance between labels that mean different
things: "Mother's Name" against "Father's Name", or "Date of Birth" against "Time of
Birth", both score 84.6. A threshold that accepted the damaged label would also accept
the wrong one, and file the father's name as the mother's.

So a label damaged beyond this is not read. That leaves an empty field, which a reviewer
can fill from the page image; the alternative leaves a plausible wrong value, which
nobody will ever look at again."""

_MIN_LABEL_LENGTH: Final = 3
"""Shorter labels than this match too much to be used fuzzily."""

_GENERIC_LABEL_LENGTH: Final = 7
"""At or below this length a label is generic, and only counts at the start of a line.

A form that prints just "Name:" needs the short synonym, but "Father's Name" contains it
too - and reading after it would file the father's name as the child's. Requiring a short
label to begin the line keeps both cases right."""

_SEPARATORS: Final = ":-=." + chr(0x2013) + chr(0x2014)
"""Characters printed between a label and its value."""

_LEADING_NOISE: Final = re.compile(rf"^[\s{re.escape(_SEPARATORS)}/|]+")
_TRAILING_NOISE: Final = re.compile(rf"[\s{re.escape(_SEPARATORS)}/|]+$")

_SAME_LINE_SCORE: Final = 1.0
_TABLE_CELL_SCORE: Final = 0.97
_NEXT_LINE_SCORE: Final = 0.8
"""A value under its label is a weaker reading than one beside it - forms do both."""

_PROSE_PENALTY: Final = 0.85
"""Applied when nothing separates label from value: it may be a sentence, not a field."""

_COLUMN_GAP: Final = 3.0
"""A gap this many line heights wide is a value column, which separates as clearly as a
colon does."""

_LABEL_CHAIN_WINDOW: Final = 40
"""How far past a label the same field's name in the other language may start."""

_OCR_FLOOR: Final = 0.6
"""An OCR value never scores above this share of a native one until confidence is high."""

_EXTRA_FIELD_LINE: Final = re.compile(r"^(?P<label>[^:]{3,60}):\s*(?P<value>.{1,200})$")

_SHAPES: Final[dict[FieldKind, re.Pattern[str]]] = {
    # What each kind of value looks like, in the forms Pakistani certificates print.
    FieldKind.DATE: re.compile(
        r"\d{4}-\d{1,2}-\d{1,2}"
        r"|\d{1,2}\s*[-/.]\s*\d{1,2}\s*[-/.]\s*\d{2,4}"
        r"|\d{1,2}\s+[^\W\d_]{3,9}\.?,?\s+\d{4}"
    ),
    FieldKind.TIME: re.compile(r"\d{1,2}\s*[:.]\s*\d{2}\s*(?:am|pm|a\.m\.|p\.m\.)?", re.IGNORECASE),
    FieldKind.ID_NUMBER: re.compile(r"\d{5}[-\s]?\d{7}[-\s]?\d"),
    FieldKind.NUMBER: re.compile(r"\d[\d,]*(?:\.\d+)?"),
}


def _shaped(value: str, kind: FieldKind) -> str | None:
    """The part of ``value`` that looks like this kind of value, if the kind has a shape."""
    pattern = _SHAPES.get(kind)
    if pattern is None:
        return None
    match = pattern.search(value)
    return match.group(0).strip() if match else None


@dataclass(frozen=True, slots=True)
class UnitPage:
    """One page of a certificate unit."""

    page_number: int
    layout: PageLayout


@dataclass(frozen=True, slots=True)
class _LineIndex:
    """A line's text rebuilt with word offsets, so a label's end can be located."""

    line: Line
    words: list[Word]
    text: str
    spans: list[tuple[int, int]]

    @property
    def lowered(self) -> str:
        return self.text.lower()


def _index_lines(layout: PageLayout) -> list[_LineIndex]:
    indexed: list[_LineIndex] = []
    for line in layout.lines:
        words = [layout.words[index] for index in line.words if index < len(layout.words)]
        if not words:
            continue
        text, spans = join_with_gaps(
            [(word.text, word.bbox.x0, word.bbox.x1, word.bbox.height) for word in words],
            line_height=max(line.bbox.height, 1e-6),
        )
        indexed.append(_LineIndex(line=line, words=words, text=text, spans=spans))
    return indexed


def _exact_matches(haystack: str, needle: str) -> list[tuple[int, int, float]]:
    """Every place ``needle`` appears in ``haystack``."""
    found: list[tuple[int, int, float]] = []
    start = haystack.find(needle)
    while start != -1:
        found.append((start, start + len(needle), 1.0))
        start = haystack.find(needle, start + 1)
    return found


def _fuzzy_match(haystack: str, needle: str) -> tuple[int, int, float] | None:
    """Where a damaged copy of ``needle`` appears, when it is long enough to be sure."""
    if len(needle) < _MIN_LABEL_LENGTH:
        return None
    alignment = fuzz.partial_ratio_alignment(needle, haystack, score_cutoff=_FUZZY_THRESHOLD)
    if alignment is None:
        return None
    return alignment.dest_start, alignment.dest_end, alignment.score / 100.0


def _label_matches(haystack: str, needle: str) -> list[tuple[int, int, float]]:
    """Every place ``needle`` appears, exactly or - failing that - as OCR left it."""
    found = _exact_matches(haystack, needle)
    if found:
        return found
    fuzzy = _fuzzy_match(haystack, needle)
    return [fuzzy] if fuzzy else []


def _starts_the_line(text: str, start: int) -> bool:
    """Whether a match at ``start`` is the line's own label rather than part of it."""
    return text[:start].strip(" " + _SEPARATORS + "/|()[]") == ""


def _best_label_on_line(entry: _LineIndex, spec: FieldSpec) -> tuple[str, int, float, float] | None:
    """The name for this field that explains the most of this line.

    Matches are ranked by how much label they account for - the similarity times the
    label's length - rather than by similarity alone. "Name of Child" damaged to "Name 0f
    Child" explains thirteen characters at 92%, and beats the bare synonym "name"
    explaining four at 100%, which would otherwise read the value as "0f Child: ...".

    Returns the label, where it ends, how well it matched, and that evidence weight.
    """
    best: tuple[str, int, float, float] | None = None
    for synonym in _sorted_synonyms(spec):
        matches = _label_matches(entry.lowered, synonym.lower())
        if not matches:
            continue
        start, end, ratio = matches[-1]
        if len(synonym) <= _GENERIC_LABEL_LENGTH and not _starts_the_line(entry.text, start):
            continue
        evidence = ratio * len(synonym)
        if best is None or evidence > best[3]:
            best = (synonym, end, ratio, evidence)
    return best


def _is_exact(ratio: float) -> bool:
    return ratio >= 1.0


def _is_urdu(text: str) -> bool:
    return arabic_script_ratio(text) > 0.5


def _extend_past_the_other_language(
    entry: _LineIndex, spec: FieldSpec, *, label_end: int, matched: str
) -> tuple[int, str | None]:
    """Walk past this field's name in the other language, printed beside this one.

    A bilingual row reads "Sex / جنس لڑکی", and the value is what follows the Urdu label
    too. Only a name in the *other* script is stepped over: a field's English synonyms
    also appear inside English values - "Issuing Authority: Union Council 42" contains
    "union council", and stepping past that would file the value as "42".
    """
    for synonym in _sorted_synonyms(spec):
        if _is_urdu(synonym) == _is_urdu(matched):
            continue
        for start, match_end, _ratio in _label_matches(entry.lowered, synonym.lower()):
            if label_end <= start <= label_end + _LABEL_CHAIN_WINDOW:
                between = entry.text[label_end:start]
                if between.strip(" " + _SEPARATORS + "/|()[]") == "":
                    return match_end, synonym
    return label_end, None


def _separated(entry: _LineIndex, *, label_end: int, value_start_word: int) -> bool:
    """Whether label and value are divided, by punctuation or by a column of space."""
    words = entry.words
    if value_start_word >= len(words) or value_start_word == 0:
        return True
    between = entry.text[label_end : entry.spans[value_start_word][0]]
    if any(char in _SEPARATORS for char in between) or "/" in between or "|" in between:
        return True
    previous, current = words[value_start_word - 1], words[value_start_word]
    gap = max(current.bbox.x0 - previous.bbox.x1, previous.bbox.x0 - current.bbox.x1, 0.0)
    return gap > _COLUMN_GAP * max(entry.line.bbox.height, 1e-6)


def _word_index_at(spans: Sequence[tuple[int, int]], offset: int) -> int:
    """The first word that starts at or after ``offset``."""
    for index, (start, _end) in enumerate(spans):
        if start >= offset:
            return index
    return len(spans)


def _clean(value: str) -> str:
    """The value without the separators and specks around it."""
    cleaned = _TRAILING_NOISE.sub("", _LEADING_NOISE.sub("", value)).strip()
    return _drop_leading_specks(cleaned)


def _drop_leading_specks(value: str) -> str:
    """Remove stray single characters OCR read in the gap before the value.

    Only ever removes a character that stands alone in front of more text, and never an
    initial - "A. Khan" keeps its A, "T REG/LHR/2019/88213" loses its T, and a value that
    is itself one character, like a sex of "F", is left entirely alone.
    """
    parts = value.split()
    while len(parts) > 1:
        head = parts[0]
        if len(head) > 1 or head.endswith("."):
            break
        if head.isalnum() and head.isdigit():
            break  # a lone digit may be the start of a number
        parts = parts[1:]
    return _LEADING_NOISE.sub("", " ".join(parts))


def _mean_confidence(words: Iterable[Word]) -> float | None:
    scores = [word.confidence for word in words if word.confidence is not None]
    return sum(scores) / len(scores) if scores else None


def _ocr_factor(words: Sequence[Word]) -> float:
    """How much to trust words that were read rather than parsed.

    Native text is taken at face value. OCR is scaled by its own confidence, so a
    crisp scan loses almost nothing and a doubtful one is visibly less certain.
    """
    mean = _mean_confidence(words)
    if mean is None:
        return 1.0
    return round(_OCR_FLOOR + (1.0 - _OCR_FLOOR) * (mean / 100.0), 4)


def _looks_like_a_label(text: str, specs: Sequence[FieldSpec]) -> bool:
    lowered = text.lower()
    return any(synonym.lower() in lowered for spec in specs for synonym in spec.synonyms if synonym)


@dataclass(frozen=True, slots=True)
class _Found:
    value: str
    words: list[Word]
    position_score: float
    label: str


def _value_after_label(
    indexed: _LineIndex,
    *,
    label_end: int,
    following: _LineIndex | None,
    specs: Sequence[FieldSpec],
    label: str,
) -> _Found | None:
    """What follows a label: the rest of its line, or the line beneath it."""
    first = _word_index_at(indexed.spans, label_end)
    tail_words = indexed.words[first:]
    tail = _clean(indexed.text[label_end:])
    if tail and tail_words:
        separated = _separated(indexed, label_end=label_end, value_start_word=first)
        return _Found(
            value=tail,
            words=tail_words,
            position_score=_SAME_LINE_SCORE if separated else _PROSE_PENALTY,
            label=label,
        )

    if following is None:
        return None
    # Nothing after the label: a form that prints the value underneath. Only accept it
    # when the line below is not itself another field's label.
    candidate = _clean(following.text)
    if not candidate or _looks_like_a_label(candidate, specs):
        return None
    return _Found(
        value=candidate,
        words=following.words,
        position_score=_NEXT_LINE_SCORE,
        label=label,
    )


def _from_table(layout: PageLayout, spec: FieldSpec) -> tuple[str, BBox | None, float, str] | None:
    """A value in the cell beside its label, for certificates laid out as a table.

    Every row is scored rather than the first match taken: "Date of Birth" and "Time of
    Birth" are similar enough for a damaged-label match, and on a form that prints both,
    the row whose label matches exactly has to win.
    """
    best: tuple[str, BBox | None, float, str] | None = None
    for table in layout.tables:
        for row in table.rows:
            if len(row) < 2:
                continue
            label_cell, *rest = row
            label_text = label_cell.text.lower()
            value_cell = next((cell for cell in rest if _clean(cell.text)), None)
            if value_cell is None:
                continue
            for synonym in _sorted_synonyms(spec):
                matches = _label_matches(label_text, synonym.lower())
                if not matches:
                    continue
                ratio = max(match[2] for match in matches)
                if best is None or ratio > best[2]:
                    best = (_clean(value_cell.text), value_cell.bbox, ratio, synonym)
                break  # the longest synonym that matched is the most specific one
    return best


def _sorted_synonyms(spec: FieldSpec) -> tuple[str, ...]:
    """Longest first: "Groom's Date of Birth" must beat "Date of Birth"."""
    return tuple(sorted({item for item in spec.synonyms if item}, key=len, reverse=True))


def _search_page(page: UnitPage, spec: FieldSpec, specs: Sequence[FieldSpec]) -> Candidate | None:
    indexed = _index_lines(page.layout)
    best: Candidate | None = None
    best_rank: tuple[float, float, float] = (0.0, 0.0, 0.0)

    for position, entry in enumerate(indexed):
        following = indexed[position + 1] if position + 1 < len(indexed) else None
        match = _best_label_on_line(entry, spec)
        if match is None:
            continue
        synonym, end, ratio, evidence = match
        # A bilingual form labels the same field twice; the value follows the last of
        # them, in either language.
        end, chained = _extend_past_the_other_language(entry, spec, label_end=end, matched=synonym)
        found = _value_after_label(
            entry, label_end=end, following=following, specs=specs, label=chained or synonym
        )
        if found is None:
            continue
        value = _shaped(found.value, spec.kind) or found.value
        confidence = ratio * found.position_score * _ocr_factor(found.words)
        candidate = Candidate(
            field=spec.name,
            value=normalize_text(value),
            confidence=round(confidence, 4),
            method=ExtractionMethod.RULE,
            source=FieldSource(
                page_number=page.page_number,
                snippet=found.value,
                bbox=BBox.enclosing([word.bbox for word in found.words]),
                label=found.label,
            ),
        )
        # Ranking across the page: a label found exactly always beats one that only
        # nearly matched somewhere else - "Age at Death" printed on one line outranks
        # "age of deceased" read into "Name of Deceased" on another. Between two exact
        # matches, the longer label is the stronger evidence.
        rank = (float(_is_exact(ratio)), evidence, candidate.confidence)
        if best is None or rank > best_rank:
            best, best_rank = candidate, rank

    from_table = _from_table(page.layout, spec)
    if from_table is not None:
        value, bbox, ratio, label = from_table
        candidate = Candidate(
            field=spec.name,
            value=normalize_text(_shaped(value, spec.kind) or value),
            confidence=round(ratio * _TABLE_CELL_SCORE, 4),
            method=ExtractionMethod.RULE,
            source=FieldSource(page_number=page.page_number, snippet=value, bbox=bbox, label=label),
        )
        if best is None or candidate.confidence > best.confidence:
            best = candidate
    return best  # a table cell answers only where no labelled line did better


def extract_with_rules(
    pages: Sequence[UnitPage],
    *,
    certificate_type: CertificateType,
    schema: FieldSchema | None = None,
) -> dict[str, Candidate]:
    """Read every field of a schema off the unit's pages."""
    specs = schema_or_builtin(certificate_type, schema).fields
    found: dict[str, Candidate] = {}
    for spec in specs:
        for page in pages:
            candidate = _search_page(page, spec, specs)
            if candidate is None or not candidate.value:
                continue
            existing = found.get(spec.name)
            if existing is None or candidate.confidence > existing.confidence:
                found[spec.name] = candidate
    return found


def extract_extra_fields(
    pages: Sequence[UnitPage],
    *,
    certificate_type: CertificateType,
    schema: FieldSchema | None = None,
) -> dict[str, Candidate]:
    """Labelled values the schema has no field for.

    An office that prints "Blood Group" on its certificates should not lose it just
    because this system has never heard of it. These are exported in their own columns
    rather than being silently dropped.
    """
    specs = schema_or_builtin(certificate_type, schema).fields
    known = {synonym.lower() for spec in specs for synonym in spec.synonyms if synonym}
    extras: dict[str, Candidate] = {}

    for page in pages:
        for entry in _index_lines(page.layout):
            match = _EXTRA_FIELD_LINE.match(entry.text.strip())
            if match is None:
                continue
            label = _clean(match.group("label"))
            value = _clean(match.group("value"))
            if not label or not value or len(label) > 60:
                continue
            lowered = label.lower()
            if any(item in lowered or lowered in item for item in known):
                continue
            key = _slug(label)
            if not key or key in extras:
                continue
            extras[key] = Candidate(
                field=key,
                value=normalize_text(value),
                confidence=round(0.7 * _ocr_factor(entry.words), 4),
                method=ExtractionMethod.RULE,
                source=FieldSource(
                    page_number=page.page_number,
                    snippet=entry.text,
                    bbox=entry.line.bbox,
                    label=label,
                ),
            )
    return extras


def _slug(label: str) -> str:
    """A column-safe key for an unknown label, in the shape of a field name."""
    cleaned = re.sub(r"[^\w\s]", " ", label, flags=re.UNICODE)
    parts = [part for part in cleaned.lower().split() if part]
    return "_".join(parts)[:60]
