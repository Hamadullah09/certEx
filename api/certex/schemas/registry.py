"""Certificate type and schema-builder request/response models."""

from __future__ import annotations

import datetime as dt
import uuid

from pydantic import BaseModel, ConfigDict, Field, field_validator

from certex.enums import CertificateType, FieldRole, SchemaVersionStatus
from certex.fields import FieldKind
from certex.services.schema_service import FIELD_KEY_PATTERN, MAX_FIELDS_PER_SCHEMA

__all__ = [
    "CertificateTypeCreate",
    "CertificateTypeSummary",
    "FieldDefinition",
    "SchemaCreate",
    "SchemaSummary",
    "SchemaVersionDetail",
    "SchemaVersionSummary",
    "VersionCreate",
]


class CertificateTypeSummary(BaseModel):
    """One entry in the Birth / Marriage / Death navigation."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    key: str
    name: str
    description: str | None = None
    classifier_key: CertificateType | None = None
    position: int
    is_active: bool


class CertificateTypeCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=2, max_length=64)
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    classifier_key: CertificateType | None = Field(
        default=None,
        description=(
            "Which built-in classifier label this type corresponds to, if any. "
            "A custom type has none, and its documents are classified as OTHER."
        ),
    )
    position: int = Field(default=0, ge=0, le=999)

    @field_validator("key")
    @classmethod
    def _upper_key(cls, value: str) -> str:
        cleaned = value.strip().upper().replace(" ", "_")
        if not cleaned.replace("_", "").isalnum():
            raise ValueError("Use letters, digits and underscores only.")
        return cleaned


class FieldDefinition(BaseModel):
    """One column of the schema builder."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(
        min_length=2,
        max_length=64,
        description="Machine key. Becomes the CSV column header and the JSON key.",
    )
    label: str = Field(min_length=1, max_length=200)
    kind: FieldKind = FieldKind.TEXT
    role: FieldRole = FieldRole.NONE
    required: bool = False
    searchable: bool = False
    unique: bool = False
    description: str | None = Field(default=None, max_length=1000)
    labels_en: list[str] = Field(
        default_factory=list,
        max_length=40,
        description="Printed labels the rules engine should match, in English.",
    )
    labels_ur: list[str] = Field(default_factory=list, max_length=40)

    @field_validator("name")
    @classmethod
    def _valid_key(cls, value: str) -> str:
        cleaned = value.strip().lower()
        if not FIELD_KEY_PATTERN.match(cleaned):
            raise ValueError(
                "Use lowercase letters, digits and underscores, starting with a letter."
            )
        return cleaned

    @field_validator("labels_en", "labels_ur")
    @classmethod
    def _clean_labels(cls, value: list[str]) -> list[str]:
        seen: list[str] = []
        for item in value:
            text = item.strip()
            if text and text not in seen:
                seen.append(text[:120])
        return seen


class SchemaCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    certificate_type_id: uuid.UUID
    name: str = Field(min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=2000)


class VersionCreate(BaseModel):
    """A new, immutable revision of a schema."""

    model_config = ConfigDict(extra="forbid")

    fields: list[FieldDefinition] = Field(min_length=1, max_length=MAX_FIELDS_PER_SCHEMA)
    notes: str | None = Field(default=None, max_length=2000)
    publish: bool = Field(
        default=True,
        description="Publish immediately. A draft can be edited; a published version cannot.",
    )


class SchemaVersionSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    schema_id: uuid.UUID
    version: int
    status: SchemaVersionStatus
    notes: str | None = None
    published_at: dt.datetime | None = None
    created_at: dt.datetime


class SchemaVersionDetail(SchemaVersionSummary):
    fields: list[FieldDefinition] = Field(default_factory=list)


class SchemaSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    certificate_type_id: uuid.UUID
    name: str
    description: str | None = None
    is_default: bool
    created_at: dt.datetime
    latest_version: int | None = None
    latest_version_id: uuid.UUID | None = None
