"""The CSV an office fills in.

The template is generated from the schema, never written by hand, so a field added
to a certificate type appears in the template the same day. Three details decide
whether a clerk can actually use the file:

**The header is the field key, not the label.** A label is prose - it changes, it is
translated, two fields can share one - and a column heading that changes breaks every
saved spreadsheet. The keys are stable, and the second row explains them.

**The second row is guidance, not data.** It says what each column expects: required
or not, the form a date takes, what the identifier is. The importer skips it, because
a person who deletes it and a person who leaves it both end up with the same import.

**A byte-order mark.** Excel on Windows reads a UTF-8 CSV as the system code page
without one, and every Urdu label in the guidance row turns to mojibake.
"""

from __future__ import annotations

import csv
import io
from typing import Final

from certex.enums import FieldRole
from certex.export.writers import UTF8_BOM, guard_formula
from certex.fields import FieldKind, FieldSchema, FieldSpec

__all__ = ["GUIDANCE_MARKER", "is_guidance_row", "template_csv", "template_headers"]

GUIDANCE_MARKER: Final = "#"
"""The guidance row's first cell starts with this, which is how the importer knows
to skip it. A certificate number never starts with a hash."""

_KIND_HINTS: Final[dict[FieldKind, str]] = {
    FieldKind.DATE: "date - 2019-04-03 or 03-04-2019",
    FieldKind.TIME: "time - 14:30",
    FieldKind.NAME: "full name as printed",
    FieldKind.ID_NUMBER: "identity number - 35201-1234567-1",
    FieldKind.REFERENCE: "number as printed on the certificate",
    FieldKind.SEX: "male or female",
    FieldKind.NUMBER: "a number",
    FieldKind.ADDRESS: "address",
    FieldKind.TEXT: "text",
}

_ROLE_HINTS: Final[dict[FieldRole, str]] = {
    FieldRole.IDENTIFIER: ("the certificate number - every row needs one and no two may share it"),
}


def template_headers(schema: FieldSchema) -> list[str]:
    """The column headings, in the schema's own order."""
    return list(schema.names)


def _hint(spec: FieldSpec) -> str:
    parts = [_ROLE_HINTS.get(spec.role) or spec.label]
    parts.append(_KIND_HINTS.get(spec.kind, "text"))
    if spec.required and not spec.is_identifier:
        parts.append("required")
    return " - ".join(part for part in parts if part)


def template_csv(schema: FieldSchema, *, delimiter: str = ",", include_bom: bool = True) -> str:
    """The whole template: a header row, a guidance row, and nothing else.

    Small enough to build in one string - a schema holds at most a couple of hundred
    fields - unlike the exports, which stream.
    """
    buffer = io.StringIO()
    writer = csv.writer(
        buffer, delimiter=delimiter, quoting=csv.QUOTE_MINIMAL, lineterminator="\r\n"
    )
    writer.writerow([guard_formula(name) for name in template_headers(schema)])

    guidance = [f"{GUIDANCE_MARKER} {_hint(spec)}" for spec in schema.fields]
    if guidance:
        writer.writerow(guidance)
    return (UTF8_BOM if include_bom else "") + buffer.getvalue()


def is_guidance_row(values: list[str]) -> bool:
    """Whether this row is the template's own explanation of itself.

    Checked on the first cell only. A row whose every cell is guidance is the one the
    template wrote; a row that merely begins with a comment in its first column is
    unusual enough that treating it as guidance is the kinder reading - it would fail
    validation as a certificate number in any case.
    """
    first = next((value.strip() for value in values if value.strip()), "")
    return first.startswith(GUIDANCE_MARKER)
