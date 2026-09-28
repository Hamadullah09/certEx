"""What a learned template knows about one office's form.

A records office prints thousands of identical forms, so once a person has corrected
one of them, the correction should apply to every other copy. A template records where
each field sits *relative to a printed anchor* - never as an absolute position, because
the same form scanned again lands a few millimetres over and at a slightly different
size.

A rule therefore says: find this anchor text, then look in this direction, within this
distance, and take what matches this pattern. That survives rescanning, and it fails
visibly rather than silently when the form changes, because the anchor stops matching.
"""

from __future__ import annotations

import enum

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "TEMPLATE_RULES_VERSION",
    "AnchorDirection",
    "TemplateRule",
    "TemplateRules",
]

TEMPLATE_RULES_VERSION = 1


class AnchorDirection(str, enum.Enum):
    """Where a value sits relative to its anchor, in reading order."""

    AFTER = "after"
    """Same line, following the anchor: the usual label/value row."""

    BELOW = "below"
    """The line beneath the anchor, for forms that print values under their labels."""

    CELL_RIGHT = "cell_right"
    """The next cell of the anchor's table row."""


class TemplateRule(BaseModel):
    """How to find one field on this form."""

    model_config = ConfigDict(frozen=True)

    field: str
    anchor: str = Field(min_length=2, max_length=120, description="Printed text to find.")
    direction: AnchorDirection = AnchorDirection.AFTER
    max_distance: float = Field(
        default=0.6,
        ge=0.0,
        le=1.0,
        description="How far from the anchor to look, as a fraction of the page.",
    )
    pattern: str | None = Field(
        default=None,
        max_length=200,
        description="Regular expression the value must match, when the form is strict.",
    )
    page_offset: int = Field(
        default=0,
        ge=0,
        description="Pages after the unit's first page where this field is printed.",
    )


class TemplateRules(BaseModel):
    """The rule set stored on a template row."""

    model_config = ConfigDict(frozen=True)

    version: int = TEMPLATE_RULES_VERSION
    rules: list[TemplateRule] = Field(default_factory=list)
    anchors: list[str] = Field(
        default_factory=list,
        description="Stable printed lines this form is recognised by.",
    )

    def rule_for(self, field: str) -> TemplateRule | None:
        return next((rule for rule in self.rules if rule.field == field), None)
