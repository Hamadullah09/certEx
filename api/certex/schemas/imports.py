"""Request and response models for bulk import."""

from __future__ import annotations

import datetime as dt
import uuid

from pydantic import BaseModel, ConfigDict, Field, computed_field

from certex.enums import ImportDuplicatePolicy, ImportStatus

__all__ = [
    "ImportErrorPage",
    "ImportRowErrorOut",
    "ImportSummary",
]


class ImportSummary(BaseModel):
    """One import, and how far it has got.

    The counts are what a screen polls while a large file is being read; they move
    while the status is still RUNNING.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    certificate_type_id: uuid.UUID
    schema_version_id: uuid.UUID | None = None
    original_filename: str
    byte_size: int
    status: ImportStatus
    duplicate_policy: ImportDuplicatePolicy
    delimiter: str
    encoding: str | None = None
    total_rows: int
    created_rows: int
    skipped_rows: int
    failed_rows: int
    error_code: str | None = None
    error_message: str | None = None
    started_at: dt.datetime | None = None
    finished_at: dt.datetime | None = None
    created_at: dt.datetime

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_finished(self) -> bool:
        return self.status.is_finished


class ImportRowErrorOut(BaseModel):
    """One row that could not be filed.

    ``value_excerpt`` is the value out of the operator's own file, kept so the message
    can name it. It is personal data: it goes to the operator who uploaded the file and
    nowhere else.
    """

    model_config = ConfigDict(from_attributes=True)

    row_number: int
    code: str
    message: str
    field_name: str | None = None
    certificate_number: str | None = None
    value_excerpt: str | None = None


class ImportErrorPage(BaseModel):
    """A page of failures, with the total so a client can size the list."""

    model_config = ConfigDict(frozen=True)

    items: list[ImportRowErrorOut]
    total: int = Field(description="Every recorded failure for this import.")
    limit: int
    offset: int
    truncated: bool = Field(
        default=False,
        description=(
            "Whether the import stopped recording individual failures. The counts on "
            "the import itself remain complete."
        ),
    )
