"""Response models for learned extraction templates.

A template is the one part of the pipeline that gets better on its own, so an office
needs to be able to see what it has learned - which forms it recognises, how often each
one has been used, and whether it is still switched on. Nothing here exposes the rules
themselves as a bare blob: a rule is only meaningful next to the form it was learned
from, and a list screen that printed anchor strings would be noise.
"""

from __future__ import annotations

import datetime as dt
import uuid

from pydantic import BaseModel, ConfigDict, Field

from certex.db.models import Template
from certex.enums import CertificateType

__all__ = ["TemplateSummary"]


class TemplateSummary(BaseModel):
    """One learned form, as the templates screen lists it."""

    model_config = ConfigDict(from_attributes=True, frozen=True)

    id: uuid.UUID
    name: str
    fingerprint: str = Field(
        description="Stable hash of issuing authority, form number and layout anchors."
    )
    certificate_type: CertificateType
    rule_count: int = Field(description="How many fields this template knows where to find.")
    hit_count: int = Field(description="How many certificates it has been applied to.")
    is_active: bool
    created_at: dt.datetime
    updated_at: dt.datetime

    @classmethod
    def of(cls, template: Template) -> TemplateSummary:
        """Built by hand because ``rule_count`` is a count of what is inside the rules
        blob rather than a column, and a screen that said "0 rules" for a template that
        has twelve would be worse than saying nothing."""
        rules = template.rules_jsonb or {}
        found = rules.get("rules") if isinstance(rules, dict) else None
        return cls(
            id=template.id,
            name=template.name,
            fingerprint=template.fingerprint,
            certificate_type=template.certificate_type,
            rule_count=len(found) if isinstance(found, list) else 0,
            hit_count=template.hit_count,
            is_active=template.is_active,
            created_at=template.created_at,
            updated_at=template.updated_at,
        )
