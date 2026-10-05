"""Workspace settings: the thresholds an office sets once and lives with.

Two numbers decide how much work the office does. A row read with confidence at or
above ``confidence_auto_approve`` is accepted without a person; below
``confidence_review_floor`` it is treated as a failed reading rather than a doubtful
one, because a value nobody can read is worse than no value. Everything between them
goes to the review queue.

Where those numbers belong is a judgement about the office, not about the software. An
archive digitising fifty-year-old registers sets them low and accepts more review; an
office issuing certificates today sets auto-approval at 1.00 and reads every row. Both
are right, which is why this is configuration.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator

__all__ = [
    "ReviewSettings",
    "WorkspaceProfile",
    "WorkspaceRename",
    "WorkspaceSettings",
    "WorkspaceSettingsUpdate",
]

Probability = Annotated[float, Field(ge=0.0, le=1.0)]


class ReviewSettings(BaseModel):
    """How much a reading has to be worth before a person need not look at it."""

    model_config = ConfigDict(extra="forbid")

    confidence_auto_approve: Probability = Field(
        default=1.0,
        description=(
            "At or above this row confidence a reading is accepted without a "
            "reviewer. The default of 1.00 means every row is reviewed, which is the "
            "safe position for an office that has not decided yet."
        ),
    )
    confidence_review_floor: Probability = Field(
        default=0.55,
        description=(
            "Below this a reading is treated as failed rather than doubtful, and the "
            "row is shown as unread instead of as a value somebody might trust."
        ),
    )
    review_imported_records: bool = Field(
        default=False,
        description=(
            "Whether records loaded from a CSV go to the review queue. They were "
            "typed by a person, so by default there is no machine reading to check."
        ),
    )
    review_suspected_duplicates: bool = Field(
        default=True,
        description=(
            "Whether a repeated certificate number sends an entry to review. Turning "
            "this off does not merge anything - the duplicate is still recorded and "
            "still shown on both entries."
        ),
    )

    @model_validator(mode="after")
    def _floor_below_auto_approve(self) -> ReviewSettings:
        if self.confidence_review_floor > self.confidence_auto_approve:
            raise ValueError(
                "The review floor cannot be above the auto-approve threshold, or "
                "every row would be both failed and approved."
            )
        return self


class WorkspaceSettings(BaseModel):
    """Everything an administrator can set for this office."""

    model_config = ConfigDict(extra="forbid")

    review: ReviewSettings = Field(default_factory=ReviewSettings)


class WorkspaceSettingsUpdate(BaseModel):
    """A settings change. Absent sections are left as they were."""

    model_config = ConfigDict(extra="forbid")

    review: ReviewSettings | None = None


class WorkspaceRename(BaseModel):
    """What this office is called."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)


class WorkspaceProfile(BaseModel):
    """The office itself, rather than the thresholds it reads by."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
