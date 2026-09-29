"""Search responses.

The shape carries one thing a plain list does not: *why* each result is a result. A
clerk shown four entries needs to know whether the system found the number they typed
or guessed at a name that looked like it, because the two call for different next
steps.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from certex.schemas.certificates import CertificateSummary
from certex.services.search_service import MatchKind

__all__ = ["SearchHitOut", "SearchResponse"]


class SearchHitOut(BaseModel):
    """One result and what it matched on."""

    model_config = ConfigDict(frozen=True)

    certificate: CertificateSummary
    match: MatchKind
    same_name_count: int = Field(
        default=1,
        description=(
            "How many entries in the register carry this name. More than one is "
            "normal and is the reason the list shows the father's name and the date."
        ),
    )


class SearchResponse(BaseModel):
    """A page of results, and which tier of the search produced them."""

    model_config = ConfigDict(frozen=True)

    items: list[SearchHitOut]
    match: MatchKind = Field(
        description=(
            "certificate_number, number_prefix, name, similar_name, filtered, or none. "
            "The screen should say which: an exact number is an answer, a similar name "
            "is a suggestion."
        )
    )
    total: int
    limit: int
    offset: int
    has_more: bool
