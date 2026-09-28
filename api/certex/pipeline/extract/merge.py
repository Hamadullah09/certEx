"""Choosing between what the layers found.

Two layers read each certificate: a template, when this office's form has been
corrected before, and the rules engine, which reads labels on any form. Where they
disagree, the template wins - it encodes a person's decision about this exact form,
while the rules engine is a generalisation.

That preference is not absolute. A template can go stale: the office reprints its form,
the anchor shifts, and the rule starts returning the wrong half of a line - with a
confidence that never falls, because the anchor still matches. So a lower-priority
source overturns a higher one when it is *clearly* surer: more than 0.2 of confidence
better. That margin is wide enough that ordinary noise never flips a field, and narrow
enough that a template reading something implausible does not outvote a clean one.

Every merged value keeps the method that produced it, so a row can always answer "where
did this come from" - and so template drift shows up as fields quietly moving back to
the rules engine.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

from certex.pipeline.extract.candidates import Candidate

__all__ = ["OVERRIDE_MARGIN", "merge_candidates"]

OVERRIDE_MARGIN: Final = 0.2
"""How much surer a lower-priority layer must be to overturn a higher-priority one."""


def merge_candidates(*layers: Mapping[str, Candidate]) -> dict[str, Candidate]:
    """Merge layers given in priority order, highest first."""
    merged: dict[str, Candidate] = {}
    for layer in layers:
        for field, candidate in layer.items():
            if not candidate.value:
                continue
            current = merged.get(field)
            if current is None or candidate.confidence > current.confidence + OVERRIDE_MARGIN:
                merged[field] = candidate
    return merged
