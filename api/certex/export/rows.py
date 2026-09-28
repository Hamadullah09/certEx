"""Turning a stored row into the cells of an exported sheet.

Two rules run through all of it.

**A blank cell means "not found".** Never "None", never "null", never a dash. The file is
read by people and by Excel, and both take an empty cell to mean nothing was there, which
is exactly what it means.

**Values are already canonical.** Dates are ISO, sex is M or F, identity numbers are
formatted - that happened at extraction, so that a correction typed by a reviewer and a
value read by the machine end up written the same way. Export does not reinterpret them;
it only decides where they go.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from certex.export.columns import EXTRA_PREFIX, ColumnPlan
from certex.services.row_service import RowRecord

__all__ = ["ExportRow", "render_row"]


@dataclass(frozen=True, slots=True)
class ExportRow:
    """One row of the sheet, keyed by column."""

    cells: dict[str, str]
    flagged: bool
    """Whether this row carries validation flags - what the spreadsheet shades."""

    def values(self, plan: ColumnPlan) -> list[str]:
        return [self.cells.get(key, "") for key in plan.keys]


def _text(value: object) -> str:
    """A cell's text: empty for anything absent, never the word "None"."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(value)


def render_row(record: RowRecord, plan: ColumnPlan) -> ExportRow:
    """Lay one extracted row out across the export's columns."""
    row = record.extraction
    sources = row.field_sources_jsonb
    confidences = row.field_confidences_jsonb
    flags = [str(flag) for flag in row.flags_jsonb]

    cells: dict[str, Any] = {
        "serial_no": record.serial_no,
        "certificate_type": row.certificate_type.value,
        "file_name": record.document.original_filename,
        "page_range": (
            str(record.unit.page_start)
            if record.unit.page_start == record.unit.page_end
            else f"{record.unit.page_start}-{record.unit.page_end}"
        ),
        "review_status": row.review_status.value,
        "row_confidence": f"{row.row_confidence:.2f}",
        "flags": " ".join(flags),
    }

    for column in plan.columns:
        if column.field is None:
            continue
        if column.kind == "value":
            cells[column.key] = row.fields_jsonb.get(column.field)
        elif column.kind == "extra":
            cells[column.key] = row.extra_fields_jsonb.get(column.field)
        elif column.kind == "confidence":
            value = confidences.get(column.field)
            cells[column.key] = f"{float(value):.2f}" if isinstance(value, int | float) else None
        elif column.kind == "snippet":
            source = sources.get(column.field)
            cells[column.key] = source.get("snippet") if isinstance(source, dict) else None

    # extra__ columns exist only when some row had that field; a row without it is blank.
    for column in plan.columns:
        if column.key.startswith(EXTRA_PREFIX) and column.key not in cells:
            cells[column.key] = None

    return ExportRow(
        cells={key: _text(value) for key, value in cells.items()},
        flagged=bool(flags),
    )
