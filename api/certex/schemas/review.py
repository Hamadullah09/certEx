"""Request and response models for reviewing register entries."""

from __future__ import annotations

import datetime as dt
import uuid

from pydantic import BaseModel, ConfigDict, Field

from certex.enums import RevisionAction

__all__ = [
    "ApproveRequest",
    "CorrectionRequest",
    "DuplicateDecision",
    "ResolveDuplicateRequest",
    "ReviewSummary",
    "RevisionOut",
    "VoidRequest",
]

MAX_NOTE = 2000


class ReviewSummary(BaseModel):
    """How much is waiting, for the badge on the navigation."""

    model_config = ConfigDict(frozen=True)

    needs_review: int
    suspected_duplicates: int = Field(
        description=(
            "Entries with an unsettled duplicate question. Counted separately rather "
            "than added, because a duplicate also needs review and adding the two "
            "would promise a reviewer more work than exists."
        )
    )


class CorrectionRequest(BaseModel):
    """A change to what an entry says.

    A patch, not a replacement: only the named fields change. An empty string clears a
    field, which is a different instruction from not mentioning it.
    """

    model_config = ConfigDict(extra="forbid")

    values: dict[str, str] = Field(min_length=1, max_length=300)
    note: str = Field(
        min_length=1,
        max_length=MAX_NOTE,
        description=(
            "Why the value was wrong and where the right one came from. Required: a "
            "correction with no reason is indistinguishable from a mistake later."
        ),
    )
    approve: bool = Field(
        default=False,
        description="Also accept the entry, so a reviewer fixing a value is one action.",
    )


class ApproveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note: str | None = Field(default=None, max_length=MAX_NOTE)


class VoidRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note: str = Field(
        min_length=1,
        max_length=MAX_NOTE,
        description="Why the office cancelled this certificate.",
    )


class DuplicateDecision(BaseModel):
    """What a person decided about two entries carrying one number."""

    model_config = ConfigDict(extra="forbid")

    same_certificate: bool = Field(
        description=(
            "True when these are one certificate: the later entry is marked "
            "superseded and points at the earlier one. Nothing is deleted and no "
            "values are combined. False records them as different certificates that "
            "happen to collide, so the pair is not raised again."
        )
    )
    note: str | None = Field(default=None, max_length=MAX_NOTE)


class ResolveDuplicateRequest(DuplicateDecision):
    other_id: uuid.UUID = Field(description="The entry this one is being compared with.")


class RevisionOut(BaseModel):
    """One row of an entry's history."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    record_version: int
    action: RevisionAction
    changed_fields: list[str] = Field(default_factory=list)
    note: str | None = None
    actor_id: uuid.UUID | None = None
    created_at: dt.datetime
