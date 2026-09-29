"""Turning a read row into a registry entry.

One function stands between everything that produces field values - the extraction
pipeline, a CSV import, an operator typing a record in - and the register itself.
It reads the row through the schema's roles, so it works for a certificate type
this code has never seen: whatever the fields are called, the role says which one
is the number, whose name the entry is filed under, and which date the certificate
is about.

Nothing here touches the database, which is what makes it testable against a plain
dictionary and reusable by the import path.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping
from dataclasses import dataclass, field

from certex.certificates.keys import name_key, number_key
from certex.enums import FieldRole
from certex.fields import EVENT_DATE_ROLES, PERSON_NAME_ROLES, FieldSchema
from certex.pipeline.extract.values import read_date

__all__ = [
    "CertificateDraft",
    "DateEntry",
    "NameEntry",
    "build_draft",
]


@dataclass(frozen=True, slots=True)
class NameEntry:
    """One person named on a certificate, and the key they are found by."""

    role: FieldRole
    field_name: str
    value: str
    key: str
    position: int
    """Which occurrence of this role it is, 0-based.

    A marriage certificate names two parties; the first is not more important than
    the second, but they have to stay distinguishable and in the printed order.
    """


@dataclass(frozen=True, slots=True)
class DateEntry:
    """One date printed on a certificate, under the role it was given."""

    role: FieldRole
    field_name: str
    value: dt.date
    position: int


@dataclass(frozen=True, slots=True)
class CertificateDraft:
    """A registry entry as it would be written, before anything is written.

    The columns here are the ones the register indexes. Everything else lives in
    ``values``, which holds the whole row exactly as it was read.
    """

    certificate_number: str
    certificate_number_key: str

    values: dict[str, str] = field(default_factory=dict)
    names: tuple[NameEntry, ...] = ()
    dates: tuple[DateEntry, ...] = ()

    registration_number: str | None = None
    registration_number_key: str | None = None

    primary_name: str | None = None
    primary_name_key: str | None = None
    secondary_name: str | None = None
    secondary_name_key: str | None = None

    event_date: dt.date | None = None
    event_date_role: FieldRole | None = None
    registration_date: dt.date | None = None
    issue_date: dt.date | None = None

    issuing_authority: str | None = None
    ambiguous_dates: tuple[str, ...] = ()
    """Fields whose printed date could be read more than one way (03/04/2019).

    Carried through so the entry can say so rather than quietly picking one; the
    register keeps the reading, and the review queue keeps the doubt.
    """

    @property
    def has_identifier(self) -> bool:
        return bool(self.certificate_number_key)

    def names_for(self, *roles: FieldRole) -> tuple[NameEntry, ...]:
        wanted = set(roles)
        return tuple(entry for entry in self.names if entry.role in wanted)

    def dates_for(self, *roles: FieldRole) -> tuple[DateEntry, ...]:
        wanted = set(roles)
        return tuple(entry for entry in self.dates if entry.role in wanted)


def _text(values: Mapping[str, str | None], name: str) -> str | None:
    raw = values.get(name)
    if raw is None:
        return None
    cleaned = str(raw).strip()
    return cleaned or None


def build_draft(
    schema: FieldSchema,
    values: Mapping[str, str | None],
    *,
    today: dt.date | None = None,
) -> CertificateDraft:
    """Read a row through its schema into the entry the register would hold.

    Values are taken as given: they have already been normalised by whatever
    produced them, and re-normalising here would quietly disagree with what the
    reviewer approved. Dates are the exception - they are parsed, because a date
    column has to be a date to be ordered or ranged over.
    """
    kept = {name: text for name in schema.names if (text := _text(values, name)) is not None}

    identifier = schema.identifier
    number = _text(values, identifier.name) if identifier else None

    secondary = schema.first(FieldRole.SECONDARY_REFERENCE)
    registration_number = _text(values, secondary.name) if secondary else None

    names = _read_names(schema, values)
    dates, ambiguous = _read_dates(schema, values, today=today)

    filed_under = [entry for entry in names if entry.role in PERSON_NAME_ROLES]
    event = _event_date(dates)
    authority = schema.first(FieldRole.ISSUING_AUTHORITY)

    return CertificateDraft(
        certificate_number=number or "",
        certificate_number_key=number_key(number),
        values=kept,
        names=names,
        dates=dates,
        registration_number=registration_number,
        registration_number_key=number_key(registration_number) or None,
        primary_name=filed_under[0].value if filed_under else None,
        primary_name_key=filed_under[0].key if filed_under else None,
        secondary_name=filed_under[1].value if len(filed_under) > 1 else None,
        secondary_name_key=filed_under[1].key if len(filed_under) > 1 else None,
        event_date=event[1] if event else None,
        event_date_role=event[0] if event else None,
        registration_date=_one_date(dates, FieldRole.REGISTRATION_DATE),
        issue_date=_one_date(dates, FieldRole.ISSUE_DATE),
        issuing_authority=_text(values, authority.name) if authority else None,
        ambiguous_dates=ambiguous,
    )


def _read_names(schema: FieldSchema, values: Mapping[str, str | None]) -> tuple[NameEntry, ...]:
    """Every person named on the certificate, in schema order.

    Roles repeat, so the position within a role is recorded: the groom and the
    bride are both ``party_name``, and an entry that kept only one of them would be
    searchable by one spouse and not the other.
    """
    entries: list[NameEntry] = []
    seen: dict[FieldRole, int] = {}
    for spec in schema.fields:
        if not spec.role.is_name:
            continue
        printed = _text(values, spec.name)
        if printed is None:
            continue
        key = name_key(printed)
        if not key:
            # Punctuation or a stray mark; nothing to file it under.
            continue
        position = seen.get(spec.role, 0)
        seen[spec.role] = position + 1
        entries.append(
            NameEntry(
                role=spec.role,
                field_name=spec.name,
                value=printed,
                key=key,
                position=position,
            )
        )
    return tuple(entries)


def _read_dates(
    schema: FieldSchema,
    values: Mapping[str, str | None],
    *,
    today: dt.date | None,
) -> tuple[tuple[DateEntry, ...], tuple[str, ...]]:
    entries: list[DateEntry] = []
    ambiguous: list[str] = []
    seen: dict[FieldRole, int] = {}
    for spec in schema.fields:
        if not spec.role.is_date:
            continue
        printed = _text(values, spec.name)
        if printed is None:
            continue
        reading = read_date(printed, today=today)
        if reading.iso is None:
            continue
        if reading.ambiguous:
            ambiguous.append(spec.name)
        position = seen.get(spec.role, 0)
        seen[spec.role] = position + 1
        entries.append(
            DateEntry(
                role=spec.role,
                field_name=spec.name,
                value=dt.date.fromisoformat(reading.iso),
                position=position,
            )
        )
    return tuple(entries), tuple(ambiguous)


def _event_date(dates: tuple[DateEntry, ...]) -> tuple[FieldRole, dt.date] | None:
    """The date the certificate is about, by role precedence."""
    for role in EVENT_DATE_ROLES:
        for entry in dates:
            if entry.role is role:
                return role, entry.value
    return None


def _one_date(dates: tuple[DateEntry, ...], role: FieldRole) -> dt.date | None:
    return next((entry.value for entry in dates if entry.role is role), None)
