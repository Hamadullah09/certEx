"""Reading an operator's CSV, a row at a time.

The file comes from a clerk's spreadsheet, so nothing about it can be assumed. It may
be saved as Windows-1252 with a Urdu column turned to mojibake, delimited with
semicolons because the machine's locale says so, carry a byte-order mark, quote some
fields and not others, have its columns in any order, or be three hundred megabytes
long. All of that is ordinary and none of it is an error.

What *is* an error is a column the schema does not define, or a missing identifier
column, and those are reported against the file as a whole before a single row is
filed - because loading half a file and then stopping leaves an office with a register
it cannot trust.

Nothing here touches the database or holds more than one row in memory.
"""

from __future__ import annotations

import codecs
import csv
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Final, Protocol

from certex.fields import FieldSchema

__all__ = [
    "MAX_COLUMNS",
    "MAX_FIELD_CHARS",
    "MAX_ROW_CHARS",
    "ByteReader",
    "HeaderPlan",
    "RowRead",
    "decode_stream",
    "detect_delimiter",
    "plan_headers",
    "read_rows",
]

MAX_COLUMNS: Final = 400
"""More columns than any schema may define. A file with more is not a register."""

MAX_FIELD_CHARS: Final = 4000
"""A single cell. Longer than any certificate field, short enough that a runaway
quote cannot consume the file into one value."""

MAX_ROW_CHARS: Final = 64_000
"""One row. Bounds the memory a single malformed line can cost."""

_DELIMITERS: Final = (",", ";", "\t", "|")


class ByteReader(Protocol):
    """Anything that yields bytes a block at a time.

    Narrower than ``IO[bytes]`` on purpose: boto3's streaming body is not seekable
    and does not pretend to be a file, and this is the whole of what reading a CSV
    out of object storage needs.
    """

    def read(self, size: int = ..., /) -> bytes: ...


_ENCODINGS: Final = ("utf-8-sig", "utf-8", "cp1252", "latin-1")
"""Tried in order. ``latin-1`` decodes any byte, so the list cannot fail - a file in
some other encoding comes through as mojibake, which a person can see and fix, rather
than as a crash halfway through an import."""


@dataclass(frozen=True, slots=True)
class HeaderPlan:
    """How the file's columns map onto the schema's fields."""

    columns: tuple[str | None, ...]
    """One entry per column of the file: the field it fills, or None to ignore it."""

    headers: tuple[str, ...]
    """The headings as the file spells them, for error messages."""

    unknown: tuple[str, ...] = ()
    """Headings that match no field."""

    duplicated: tuple[str, ...] = ()
    """Fields that two columns both claim."""

    missing_required: tuple[str, ...] = ()
    """Required fields with no column at all."""

    @property
    def usable(self) -> bool:
        return not (self.unknown or self.duplicated or self.missing_required)

    @property
    def mapped_fields(self) -> tuple[str, ...]:
        return tuple(name for name in self.columns if name is not None)


@dataclass(slots=True)
class RowRead:
    """One data row of the file, mapped onto field names."""

    row_number: int
    """The line as a spreadsheet numbers it: the header is line 1."""

    values: dict[str, str] = field(default_factory=dict)
    too_many_columns: bool = False
    too_few_columns: bool = False
    oversized: bool = False
    """A cell or the row exceeded its limit and was truncated."""

    @property
    def is_blank(self) -> bool:
        return not any(value.strip() for value in self.values.values())


def decode_stream(raw: ByteReader) -> tuple[Iterator[str], str]:
    """Wrap a byte stream as lines of text, saying which encoding worked.

    The first block decides. Reading the whole file to choose an encoding would mean
    holding the whole file, and the header and the first rows are where a wrong guess
    shows up anyway.
    """
    head = raw.read(65_536)
    encoding = _sniff_encoding(head)
    decoder = codecs.getincrementaldecoder(encoding)(errors="replace")

    def lines() -> Iterator[str]:
        buffer = decoder.decode(head)
        block = head
        while True:
            start = 0
            while (index := buffer.find("\n", start)) != -1:
                yield buffer[start:index]
                start = index + 1
            buffer = buffer[start:]
            block = raw.read(65_536)
            if not block:
                break
            buffer += decoder.decode(block)
        buffer += decoder.decode(b"", True)
        if buffer:
            yield buffer

    return lines(), encoding


def _sniff_encoding(head: bytes) -> str:
    """Which encoding to read this file as, reported honestly.

    ``utf-8-sig`` decodes plain UTF-8 perfectly well, so trying it first would label
    every file as having a byte-order mark. The mark is checked for directly, because
    the name is stored on the import and read by a person deciding why their Urdu
    column came out wrong.
    """
    if head.startswith(codecs.BOM_UTF8):
        return "utf-8-sig"
    for candidate in _ENCODINGS:
        if candidate == "utf-8-sig":
            continue
        try:
            head.decode(candidate)
        except UnicodeDecodeError:
            continue
        return candidate
    return "latin-1"  # pragma: no cover - latin-1 decodes every byte


def detect_delimiter(header_line: str) -> str:
    """The delimiter the header uses: whichever of the four appears most.

    Counted on the header rather than sniffed by :mod:`csv`, whose Sniffer guesses
    from quoting patterns and picks a letter out of Urdu text often enough to matter.
    """
    counts = {candidate: header_line.count(candidate) for candidate in _DELIMITERS}
    best = max(counts, key=lambda candidate: counts[candidate])
    return best if counts[best] else ","


def plan_headers(schema: FieldSchema, headers: list[str]) -> HeaderPlan:
    """Match the file's headings to the schema's fields.

    A heading matches a field key exactly, or the field's label, ignoring case,
    surrounding space and the difference between spaces and underscores - which covers
    both a file generated from the template and one a clerk has been keeping for years
    with "Certificate Number" at the top.
    """
    by_key: dict[str, str] = {}
    for spec in schema.fields:
        by_key[_match_key(spec.name)] = spec.name
        by_key.setdefault(_match_key(spec.label), spec.name)

    columns: list[str | None] = []
    unknown: list[str] = []
    seen: dict[str, int] = {}
    duplicated: list[str] = []

    for heading in headers:
        cleaned = heading.strip()
        matched = by_key.get(_match_key(cleaned))
        if matched is None:
            columns.append(None)
            if cleaned:
                unknown.append(cleaned)
            continue
        if matched in seen:
            duplicated.append(matched)
            columns.append(None)
            continue
        seen[matched] = len(columns)
        columns.append(matched)

    identifier = schema.identifier
    required = [
        spec.name
        for spec in schema.fields
        if spec.required and spec.name not in seen
        # The identifier is required by definition; it is reported by name so the
        # message can say which column is missing rather than "an identifier".
    ]
    if identifier is not None and identifier.name not in seen and identifier.name not in required:
        required.insert(0, identifier.name)

    return HeaderPlan(
        columns=tuple(columns),
        headers=tuple(headers),
        unknown=tuple(dict.fromkeys(unknown)),
        duplicated=tuple(dict.fromkeys(duplicated)),
        missing_required=tuple(required),
    )


def _match_key(text: str) -> str:
    return text.strip().casefold().replace(" ", "_").replace("-", "_").strip("_")


def read_rows(lines: Iterator[str], plan: HeaderPlan, *, delimiter: str) -> Iterator[RowRead]:
    """Yield the file's data rows, mapped onto field names.

    Rows arrive in file order and are numbered as a spreadsheet numbers them, so an
    error can say "line 4,182" and mean the line the clerk will find. Blank rows and
    the template's own guidance row are skipped rather than reported: a trailing
    newline is not a mistake worth telling anyone about.
    """
    from certex.imports.template import is_guidance_row

    reader = csv.reader(
        _bounded(lines), delimiter=delimiter, quoting=csv.QUOTE_MINIMAL, strict=False
    )
    width = len(plan.columns)
    for index, raw_values in enumerate(reader, start=2):
        if not raw_values or not any(value.strip() for value in raw_values):
            continue
        if index == 2 and is_guidance_row(raw_values):
            continue

        oversized = False
        values: dict[str, str] = {}
        for position, name in enumerate(plan.columns):
            if name is None or position >= len(raw_values):
                continue
            text = raw_values[position].strip()
            if len(text) > MAX_FIELD_CHARS:
                text = text[:MAX_FIELD_CHARS]
                oversized = True
            if text:
                values[name] = text

        yield RowRead(
            row_number=index,
            values=values,
            too_many_columns=len(raw_values) > width,
            too_few_columns=len(raw_values) < width,
            oversized=oversized,
        )


def _bounded(lines: Iterator[str]) -> Iterator[str]:
    """Truncate any single line that is longer than a row has any right to be."""
    for line in lines:
        yield line[:MAX_ROW_CHARS] if len(line) > MAX_ROW_CHARS else line
