"""What the columns of an export are, and in what order.

A records office opens this file in Excel and works down it, so the order is not a
detail:

1. **serial_no** - the row's position in the batch. It is what someone says on the
   telephone ("row 148 is wrong"), so it comes first.
2. **certificate_type** - what kind of record this row is, which decides what the rest of
   the columns mean.
3. then the fields every certificate has, then the fields of the types actually present,
   then anything the office printed that this system has no field for, as ``extra__`` -
   alphabetically, so the tail of the file is stable between runs.

A mixed batch gets one wide sheet rather than one column set per type: a clerk filtering
by type in Excel is doing something they already know how to do, whereas reconciling
three files is not. Exporting each type separately is offered as its own mode.

Confidence and the printed snippet can be included beside each value. They are off by
default: most people want a clean sheet, and the ones who want to audit a run want both.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Final

from certex.enums import CertificateType
from certex.fields import FieldSpec, common_fields, fields_for

__all__ = [
    "CONFIDENCE_SUFFIX",
    "EXTRA_PREFIX",
    "SNIPPET_SUFFIX",
    "ColumnPlan",
    "build_column_plan",
]

EXTRA_PREFIX: Final = "extra__"
"""Marks a column the schema never asked for, so it is obvious where it came from."""

CONFIDENCE_SUFFIX: Final = "__confidence"
SNIPPET_SUFFIX: Final = "__source"

SERIAL_COLUMN: Final = "serial_no"
TYPE_COLUMN: Final = "certificate_type"

_CONTEXT_COLUMNS: Final[tuple[tuple[str, str], ...]] = (
    # Which file and which pages a row came from: the first thing anyone checks when a
    # row looks wrong.
    ("file_name", "Source file"),
    ("page_range", "Pages"),
    ("review_status", "Review status"),
    ("row_confidence", "Row confidence"),
    ("flags", "Flags"),
)


@dataclass(frozen=True, slots=True)
class Column:
    """One column of the exported sheet."""

    key: str
    header: str
    field: str | None = None
    """The extraction field this column carries, when it carries one."""

    kind: str = "value"
    """value, confidence, snippet, extra, or context."""


@dataclass(frozen=True, slots=True)
class ColumnPlan:
    """Every column of an export, in order."""

    columns: list[Column]

    @property
    def keys(self) -> list[str]:
        return [column.key for column in self.columns]

    @property
    def headers(self) -> list[str]:
        return [column.header for column in self.columns]

    def __len__(self) -> int:
        return len(self.columns)


def _field_columns(
    specs: Sequence[FieldSpec], *, include_confidence: bool, include_snippet: bool
) -> list[Column]:
    columns: list[Column] = []
    for spec in specs:
        columns.append(Column(key=spec.name, header=spec.label, field=spec.name))
        if include_confidence:
            columns.append(
                Column(
                    key=f"{spec.name}{CONFIDENCE_SUFFIX}",
                    header=f"{spec.label} (confidence)",
                    field=spec.name,
                    kind="confidence",
                )
            )
        if include_snippet:
            columns.append(
                Column(
                    key=f"{spec.name}{SNIPPET_SUFFIX}",
                    header=f"{spec.label} (as printed)",
                    field=spec.name,
                    kind="snippet",
                )
            )
    return columns


def build_column_plan(
    certificate_types: Iterable[CertificateType],
    *,
    extra_field_names: Iterable[str] = (),
    include_confidence: bool = False,
    include_snippet: bool = False,
) -> ColumnPlan:
    """The columns for a set of certificate types present in the export."""
    present = list(dict.fromkeys(certificate_types))
    columns: list[Column] = [
        Column(key=SERIAL_COLUMN, header="Serial no", kind="context"),
        Column(key=TYPE_COLUMN, header="Certificate type", kind="context"),
    ]

    seen: set[str] = set()
    ordered_specs: list[FieldSpec] = []
    for spec in common_fields():
        ordered_specs.append(spec)
        seen.add(spec.name)
    # Types in a fixed order, so two exports of the same batch have the same columns
    # whatever order the rows happened to arrive in.
    for certificate_type in sorted(present, key=lambda item: item.value):
        for spec in fields_for(certificate_type):
            if spec.name in seen:
                continue
            ordered_specs.append(spec)
            seen.add(spec.name)

    columns.extend(
        _field_columns(
            ordered_specs, include_confidence=include_confidence, include_snippet=include_snippet
        )
    )
    columns.extend(
        Column(
            key=f"{EXTRA_PREFIX}{name}",
            header=f"{name.replace('_', ' ').capitalize()} (extra)",
            field=name,
            kind="extra",
        )
        for name in sorted(set(extra_field_names))
    )
    columns.extend(
        Column(key=key, header=header, kind="context") for key, header in _CONTEXT_COLUMNS
    )
    return ColumnPlan(columns=columns)
