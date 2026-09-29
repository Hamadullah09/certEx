"""Request and response models for the register.

The shape is driven by one constraint: a certificate's fields are not known at
build time. An operator can define a type this code has never heard of, so the
payloads carry ``values`` as a map of field name to text, and the schema version
the entry was read under says what those names mean. Everything the registry
itself needs - the number, who it is about, the date it happened - is lifted out
into named response fields by role, so a client can render a result list without
knowing the schema at all.
"""

from __future__ import annotations

import datetime as dt
import uuid

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from certex.enums import (
    CertificateSource,
    CertificateStatus,
    DocumentLinkKind,
    DuplicateStatus,
    FieldRole,
)

__all__ = [
    "CertificateCreate",
    "CertificateDetail",
    "CertificateSummary",
    "DocumentLink",
    "DocumentLinkCreate",
    "DuplicateCandidate",
    "NamedValue",
    "TypedDate",
]

MAX_VALUE_LENGTH = 2000
"""A single field's text. Long enough for an address, short enough to index."""

MAX_FIELDS_PER_RECORD = 300
"""More fields than any schema may define, so a malformed payload is refused
before it reaches the database rather than after."""


class NamedValue(BaseModel):
    """One person named on a certificate, with the role they hold."""

    model_config = ConfigDict(from_attributes=True)

    role: FieldRole
    field_name: str
    value: str
    position: int


class TypedDate(BaseModel):
    """One date printed on a certificate, under its role."""

    model_config = ConfigDict(from_attributes=True)

    role: FieldRole
    field_name: str
    value: dt.date
    position: int


class DocumentLink(BaseModel):
    """A document this entry was read from, or that supports it."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    document_id: uuid.UUID
    unit_id: uuid.UUID | None = None
    kind: DocumentLinkKind
    page_start: int | None = None
    page_end: int | None = None
    note: str | None = None
    created_at: dt.datetime


class DocumentLinkCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_id: uuid.UUID
    unit_id: uuid.UUID | None = None
    kind: DocumentLinkKind = DocumentLinkKind.SUPPORTING
    page_start: int | None = Field(default=None, ge=1)
    page_end: int | None = Field(default=None, ge=1)
    note: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def _pages_ordered(self) -> DocumentLinkCreate:
        if (
            self.page_start is not None
            and self.page_end is not None
            and self.page_end < self.page_start
        ):
            raise ValueError("The last page cannot come before the first.")
        return self


class CertificateSummary(BaseModel):
    """One row of a result list.

    Deliberately schema-independent: every field here is filled by role, so the
    same list component renders a birth, a marriage and a type invented this
    morning.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    certificate_type_id: uuid.UUID
    certificate_number: str
    registration_number: str | None = None
    primary_name: str | None = None
    secondary_name: str | None = None
    event_date: dt.date | None = None
    event_date_role: FieldRole | None = None
    registration_date: dt.date | None = None
    issue_date: dt.date | None = None
    issuing_authority: str | None = None
    status: CertificateStatus
    duplicate_status: DuplicateStatus
    needs_review: bool
    row_confidence: float
    source: CertificateSource
    created_at: dt.datetime


class DuplicateCandidate(BaseModel):
    """An existing entry that another one might repeat."""

    model_config = ConfigDict(frozen=True)

    certificate_id: uuid.UUID
    certificate_number: str
    reason: str = Field(
        description=(
            "Why these were brought together: same_number, same_number_other_type, "
            "or same_name_and_event_date."
        )
    )
    primary_name: str | None = None
    event_date: dt.date | None = None
    same_type: bool = True


class CertificateDetail(CertificateSummary):
    """One entry in full: its values, who it names, and where it came from."""

    schema_version_id: uuid.UUID | None = None
    record_version: int
    duplicate_of_id: uuid.UUID | None = None
    values: dict[str, str] = Field(default_factory=dict)
    confidences: dict[str, float] = Field(default_factory=dict)
    provenance: dict[str, dict[str, str | int | float | None]] = Field(default_factory=dict)
    names: list[NamedValue] = Field(default_factory=list)
    dates: list[TypedDate] = Field(default_factory=list)
    documents: list[DocumentLink] = Field(default_factory=list)
    updated_at: dt.datetime


class CertificateCreate(BaseModel):
    """A record typed in by an operator.

    ``values`` is keyed by the schema's field names. Anything the schema does not
    define is refused rather than stored, because a value nobody can read back is
    worse than a missing one.
    """

    model_config = ConfigDict(extra="forbid")

    certificate_type_id: uuid.UUID
    schema_version_id: uuid.UUID | None = Field(
        default=None,
        description=(
            "Which schema version to read the values under. Defaults to the "
            "newest published version of the type's default schema."
        ),
    )
    values: dict[str, str] = Field(min_length=1, max_length=MAX_FIELDS_PER_RECORD)
    allow_duplicate: bool = Field(
        default=False,
        description=(
            "Record this even though the certificate number is already held. "
            "The entry is created linked to the one it repeats and sent to review; "
            "neither entry is changed or merged."
        ),
    )

    @field_validator("values")
    @classmethod
    def _clean_values(cls, value: dict[str, str]) -> dict[str, str]:
        cleaned: dict[str, str] = {}
        for name, raw in value.items():
            text = raw.strip()
            if not text:
                continue
            if len(text) > MAX_VALUE_LENGTH:
                raise ValueError(f"The value for {name!r} is too long.")
            cleaned[name] = text
        if not cleaned:
            raise ValueError("Every value was blank; there is nothing to record.")
        return cleaned
