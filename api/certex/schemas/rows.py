"""The extracted rows, as the review screen and the export see them.

A row is one certificate: the values read from it, how sure each one is, where each came
from, and what validation thought. The review screen needs all of that at once - a
reviewer deciding whether "Tarig" should be "Tariq" is looking at the value, its
confidence, the words it was read from and the page image, in one glance - so a row is
returned whole rather than as something to assemble from three requests.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from certex.enums import CertificateType, ReviewStatus

__all__ = [
    "FieldValue",
    "RowCorrection",
    "RowDetail",
    "RowSummary",
]

_MAX_FIELD_LENGTH = 2000
"""Longer than any certificate field; long enough that nothing real is refused."""


class FieldValue(BaseModel):
    """One field of one row, with everything needed to judge it."""

    model_config = ConfigDict(frozen=True)

    name: str
    value: str | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    method: str | None = Field(
        default=None, description="Which layer read it: template, rule or manual."
    )
    page_number: int | None = None
    bbox: dict[str, float] | None = Field(
        default=None, description="Where on the page it was read, as page fractions."
    )
    snippet: str | None = Field(default=None, description="The text as printed.")
    label: str | None = Field(default=None, description="The printed label it was found under.")
    flags: list[str] = Field(
        default_factory=list, description="Validation flags raised against this field."
    )
    issues: list[str] = Field(default_factory=list, description="What is wrong with it, in words.")


class RowSummary(BaseModel):
    """One certificate, as a line in the results grid."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    unit_id: uuid.UUID
    document_id: uuid.UUID
    batch_id: uuid.UUID
    serial_no: int = Field(
        description="Position of this row in the batch, counting from 1. Column one of the CSV."
    )
    file_name: str
    page_start: int
    page_end: int
    certificate_type: CertificateType
    review_status: ReviewStatus
    row_confidence: float = Field(ge=0.0, le=1.0)
    flags: list[str] = Field(default_factory=list)
    fields: dict[str, str | None] = Field(default_factory=dict)
    field_confidences: dict[str, float] = Field(default_factory=dict)
    extra_fields: dict[str, str] = Field(default_factory=dict)
    ocr_used: bool = False
    detected_language: str | None = None
    reviewed_at: dt.datetime | None = None
    updated_at: dt.datetime


class RowDetail(RowSummary):
    """One certificate opened for review, with provenance for every field."""

    values: list[FieldValue] = Field(
        default_factory=list, description="Every field of this type, in export order."
    )
    issues: list[dict[str, str]] = Field(
        default_factory=list, description="Validation issues, field by field."
    )
    page_numbers: list[int] = Field(
        default_factory=list, description="Pages of the source file this row covers."
    )


class RowCorrection(BaseModel):
    """A reviewer's edits to one row.

    Only the fields being changed are sent. A field set to null is being cleared, which
    is different from not mentioning it.
    """

    model_config = ConfigDict(extra="forbid")

    fields: dict[str, Annotated[str, Field(max_length=_MAX_FIELD_LENGTH)] | None] = Field(
        default_factory=dict,
        description="Field name to its corrected value, or null to clear it.",
    )
    approve: bool = Field(
        default=False,
        description="Mark the row as checked by a person once the edits are applied.",
    )

    @field_validator("fields")
    @classmethod
    def _at_least_something(cls, value: dict[str, str | None]) -> dict[str, str | None]:
        if len(value) > 200:
            raise ValueError("too many fields in one correction")
        return value

    def cleaned(self) -> dict[str, Any]:
        """Edits with surrounding whitespace removed; empty becomes a cleared field."""
        return {
            name: (raw.strip() or None) if isinstance(raw, str) else None
            for name, raw in self.fields.items()
        }
