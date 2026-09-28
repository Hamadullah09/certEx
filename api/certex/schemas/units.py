"""Certificate units: the page ranges that become rows.

A unit is the pipeline's guess at where one certificate sits inside a file. The guess
is usually right and sometimes not, so these shapes exist to show a reviewer what was
decided, with what confidence and on what evidence, and to let them correct it.
"""

from __future__ import annotations

import datetime as dt
import uuid

from pydantic import BaseModel, ConfigDict, Field, model_validator

from certex.enums import (
    BoundaryMethod,
    CertificateType,
    ClassificationMethod,
    UnitStatus,
)

__all__ = ["UnitMergeRequest", "UnitSplitRequest", "UnitSummary"]


class UnitSummary(BaseModel):
    """One certificate unit, as the review screen shows it."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    document_id: uuid.UUID
    batch_id: uuid.UUID
    ordinal: int = Field(ge=0, description="Position within its document, 0-based.")
    page_start: int = Field(ge=1)
    page_end: int = Field(ge=1)

    boundary_method: BoundaryMethod
    boundary_confidence: float = Field(ge=0.0, le=1.0)

    certificate_type: CertificateType
    type_confidence: float = Field(ge=0.0, le=1.0)
    classification_method: ClassificationMethod

    status: UnitStatus
    error_code: str | None = None
    error_message: str | None = None
    created_at: dt.datetime
    updated_at: dt.datetime

    @property
    def page_count(self) -> int:
        return self.page_end - self.page_start + 1


class UnitSplitRequest(BaseModel):
    """Split one unit in two, at the page that starts the second certificate."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    at_page: int = Field(
        ge=2,
        description=(
            "First page of the second certificate. Must be inside the unit and after "
            "its first page."
        ),
    )


class UnitMergeRequest(BaseModel):
    """Join units that are really one certificate.

    The units must be adjacent and belong to one document: merging across documents
    would produce a row whose pages come from two different files.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    unit_ids: list[uuid.UUID] = Field(min_length=2, max_length=50)

    @model_validator(mode="after")
    def _no_duplicates(self) -> UnitMergeRequest:
        if len(set(self.unit_ids)) != len(self.unit_ids):
            raise ValueError("unit_ids must not repeat")
        return self
