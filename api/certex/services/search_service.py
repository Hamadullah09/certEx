"""Finding a certificate.

This is what the office does all day: somebody at the counter has a number, or a name
and roughly a year, and the clerk has thirty seconds. So the search answers in tiers,
most certain first, and says which tier answered:

1. **The certificate number**, exactly. Unambiguous, and the commonest case, because
   the person at the counter is usually holding the certificate.
2. **The number as a prefix**, for a partly remembered or partly legible number.
3. **The name**, exactly as it normalises. A name is not unique: this is expected to
   return several entries, and the result carries each one's father's name and date so
   a clerk can tell them apart.
4. **A name that is close**, by trigram similarity, for a different transliteration or
   a spelling nobody agrees on.

A tier that finds nothing falls through to the next. A tier that finds something wins,
because mixing exact number matches with fuzzy name matches in one list would bury the
answer. The tier is reported, so the screen can say "no certificate with that number;
here are people with that name".

Ordering inside the fuzzy tier is by similarity, which rules out a keyset cursor - a
cursor needs a total order that does not depend on the query. So search pages by offset
and says how many there are, and the plain browse elsewhere keeps its cursor.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Final

from sqlalchemy import ColumnElement, Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from certex.certificates.keys import name_key, number_key
from certex.db.models import Certificate, CertificateName
from certex.enums import CertificateStatus, DuplicateStatus, FieldRole

__all__ = [
    "MAX_OFFSET",
    "MIN_NUMBER_PREFIX",
    "SIMILARITY_FLOOR",
    "MatchKind",
    "SearchHit",
    "SearchQuery",
    "SearchResults",
    "search",
]

MIN_NUMBER_PREFIX: Final = 3
"""Shortest prefix worth searching numbers by. Two characters matches half the register."""

SIMILARITY_FLOOR: Final = 0.35
"""Trigram similarity below which two names are not the same name.

Chosen against the transliterations this system actually sees - Muhammad and Mohammad
score 0.6, Ahmed and Ahmad 0.5 - and above the level at which unrelated short names
start to collide.
"""

MAX_OFFSET: Final = 10_000
"""How deep a search may page. Nobody reads page 200; a query that needs to is the
wrong query, and refusing it keeps one request from scanning the register."""


class MatchKind(str, Enum):
    """Which tier answered. The screen says different things for each."""

    CERTIFICATE_NUMBER = "certificate_number"
    NUMBER_PREFIX = "number_prefix"
    NAME = "name"
    SIMILAR_NAME = "similar_name"
    FILTERED = "filtered"
    """No search text: the filters alone, newest event first."""

    NONE = "none"


@dataclass(frozen=True, slots=True)
class SearchQuery:
    """One search as the API received it."""

    text: str | None = None
    certificate_type_id: uuid.UUID | None = None
    name: str | None = None
    father_name: str | None = None
    event_date_from: dt.date | None = None
    event_date_to: dt.date | None = None
    needs_review: bool | None = None
    duplicates_only: bool = False
    include_void: bool = False
    limit: int = 25
    offset: int = 0

    @property
    def has_text(self) -> bool:
        return bool(self.text and self.text.strip())


@dataclass(frozen=True, slots=True)
class SearchHit:
    """One result, and why it is one."""

    certificate: Certificate
    kind: MatchKind
    score: float = 1.0
    same_name_count: int = 1
    """How many entries in the register share this one's name.

    The number the clerk needs before they start reading: "this is one of four people
    called Muhammad Ahmed" changes what they do next.
    """


@dataclass(frozen=True, slots=True)
class SearchResults:
    hits: tuple[SearchHit, ...]
    kind: MatchKind
    total: int
    limit: int
    offset: int

    @property
    def has_more(self) -> bool:
        return self.offset + len(self.hits) < self.total


def _base(query: SearchQuery, *, workspace_id: uuid.UUID) -> Select[tuple[Certificate]]:
    """The filters that apply however the search was phrased."""
    statement = select(Certificate).where(Certificate.workspace_id == workspace_id)
    if not query.include_void:
        statement = statement.where(Certificate.status != CertificateStatus.VOID)
    if query.certificate_type_id is not None:
        statement = statement.where(Certificate.certificate_type_id == query.certificate_type_id)
    if query.needs_review is not None:
        statement = statement.where(Certificate.needs_review.is_(query.needs_review))
    if query.duplicates_only:
        statement = statement.where(Certificate.duplicate_status != DuplicateStatus.NONE)
    if query.event_date_from is not None:
        statement = statement.where(Certificate.event_date >= query.event_date_from)
    if query.event_date_to is not None:
        statement = statement.where(Certificate.event_date <= query.event_date_to)

    if query.name:
        key = name_key(query.name)
        if key:
            statement = statement.where(_named(key))
    if query.father_name:
        key = name_key(query.father_name)
        if key:
            statement = statement.where(
                Certificate.id.in_(
                    select(CertificateName.certificate_id).where(
                        CertificateName.role == FieldRole.FATHER_NAME,
                        CertificateName.value_key == key,
                    )
                )
            )
    return statement


def _named(key: str) -> ColumnElement[bool]:
    """Whether an entry carries this name, in any role.

    The subject's name is a column for speed, but a clerk searching a name may mean
    the mother on a birth certificate or the second party to a marriage, so the side
    table is searched too.
    """
    return or_(
        Certificate.primary_name_key == key,
        Certificate.secondary_name_key == key,
        Certificate.id.in_(
            select(CertificateName.certificate_id).where(CertificateName.value_key == key)
        ),
    )


async def _page(
    session: AsyncSession,
    statement: Select[tuple[Certificate]],
    *,
    query: SearchQuery,
) -> tuple[list[Certificate], int]:
    """One page of a statement, and how many rows it matches in all."""
    total = await session.scalar(
        select(func.count()).select_from(statement.order_by(None).subquery())
    )
    rows = list((await session.scalars(statement.limit(query.limit).offset(query.offset))).all())
    return rows, int(total or 0)


async def search(
    session: AsyncSession, *, workspace_id: uuid.UUID, query: SearchQuery
) -> SearchResults:
    """Answer a search, in tiers, saying which tier answered."""
    if query.offset > MAX_OFFSET:
        return SearchResults(
            hits=(), kind=MatchKind.NONE, total=0, limit=query.limit, offset=query.offset
        )

    base = _base(query, workspace_id=workspace_id)

    if not query.has_text:
        statement = base.order_by(
            Certificate.event_date.desc().nullslast(),
            Certificate.created_at.desc(),
            Certificate.id.desc(),
        )
        rows, total = await _page(session, statement, query=query)
        return await _results(session, rows, MatchKind.FILTERED, total=total, query=query)

    text = (query.text or "").strip()
    for kind, statement in _tiers(base, text):
        rows, total = await _page(session, statement, query=query)
        if rows:
            return await _results(session, rows, kind, total=total, query=query)

    return SearchResults(
        hits=(), kind=MatchKind.NONE, total=0, limit=query.limit, offset=query.offset
    )


def _tiers(
    base: Select[tuple[Certificate]], text: str
) -> list[tuple[MatchKind, Select[tuple[Certificate]]]]:
    """The tiers to try, in order, for this search text."""
    tiers: list[tuple[MatchKind, Select[tuple[Certificate]]]] = []

    number = number_key(text)
    if number:
        tiers.append(
            (
                MatchKind.CERTIFICATE_NUMBER,
                base.where(
                    or_(
                        Certificate.certificate_number_key == number,
                        Certificate.registration_number_key == number,
                    )
                ).order_by(Certificate.created_at, Certificate.id),
            )
        )
        if len(number) >= MIN_NUMBER_PREFIX:
            pattern = f"{_escape_like(number)}%"
            tiers.append(
                (
                    MatchKind.NUMBER_PREFIX,
                    base.where(
                        or_(
                            Certificate.certificate_number_key.like(pattern, escape="\\"),
                            Certificate.registration_number_key.like(pattern, escape="\\"),
                        )
                    ).order_by(Certificate.certificate_number_key, Certificate.id),
                )
            )

    name = name_key(text)
    if name:
        tiers.append(
            (
                MatchKind.NAME,
                base.where(_named(name)).order_by(
                    Certificate.event_date.desc().nullslast(), Certificate.id
                ),
            )
        )
        similarity = func.similarity(Certificate.primary_name_key, name)
        tiers.append(
            (
                MatchKind.SIMILAR_NAME,
                base.where(
                    Certificate.primary_name_key.is_not(None),
                    similarity >= SIMILARITY_FLOOR,
                ).order_by(similarity.desc(), Certificate.id),
            )
        )
    return tiers


def _escape_like(value: str) -> str:
    """Neutralise the wildcards in a value used as a LIKE prefix.

    A search for "BC%" must look for a per-cent sign, not match the whole register.
    """
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


async def _results(
    session: AsyncSession,
    rows: Sequence[Certificate],
    kind: MatchKind,
    *,
    total: int,
    query: SearchQuery,
) -> SearchResults:
    counts = await _same_name_counts(session, rows)
    hits = tuple(
        SearchHit(
            certificate=row,
            kind=kind,
            score=1.0,
            same_name_count=counts.get(row.primary_name_key or "", 1),
        )
        for row in rows
    )
    return SearchResults(hits=hits, kind=kind, total=total, limit=query.limit, offset=query.offset)


async def _same_name_counts(session: AsyncSession, rows: Sequence[Certificate]) -> dict[str, int]:
    """How many entries share each name on this page.

    One grouped query for the page rather than one per row: a page of fifty results
    would otherwise be fifty round trips to answer a question nobody asked directly.
    """
    keys = {row.primary_name_key for row in rows if row.primary_name_key}
    if not keys:
        return {}
    workspace_ids = {row.workspace_id for row in rows}
    grouped = await session.execute(
        select(Certificate.primary_name_key, func.count())
        .where(
            Certificate.workspace_id.in_(workspace_ids),
            Certificate.primary_name_key.in_(keys),
            Certificate.status != CertificateStatus.VOID,
        )
        .group_by(Certificate.primary_name_key)
    )
    return {key: int(count) for key, count in grouped.all() if key is not None}
