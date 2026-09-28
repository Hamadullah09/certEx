"""What one extraction layer found, before anything is chosen.

Every layer - rules now, templates alongside them, a person's correction later -
produces candidates in this shape, so the merge can compare them on equal terms and
every stored value can say where it came from: which page, which box on that page, and
the text as printed. Provenance is not a nicety here. A reviewer looking at a wrong
value needs to see the words it was read from, and a corrected value needs to point at
the place a template should look next time.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from certex.enums import ExtractionMethod
from certex.schemas.layout import BBox

__all__ = ["Candidate", "FieldSource"]


@dataclass(frozen=True, slots=True)
class FieldSource:
    """Where on the document a value was read from."""

    page_number: int
    snippet: str
    """The text as printed, before normalisation - what the reviewer will recognise."""

    bbox: BBox | None = None
    label: str | None = None
    """The printed label this value was found under, when a label was involved."""

    def as_json(self) -> dict[str, Any]:
        return {
            "page_number": self.page_number,
            "snippet": self.snippet[:500],
            "bbox": self.bbox.model_dump() if self.bbox else None,
            "label": self.label,
        }


@dataclass(frozen=True, slots=True)
class Candidate:
    """One layer's answer for one field."""

    field: str
    value: str
    confidence: float
    method: ExtractionMethod
    source: FieldSource

    def with_confidence(self, confidence: float) -> Candidate:
        return Candidate(
            field=self.field,
            value=self.value,
            confidence=round(confidence, 4),
            method=self.method,
            source=self.source,
        )
