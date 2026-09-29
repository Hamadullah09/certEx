"""What has to be true about a certificate, and what to say when it is not.

Extraction reads what is printed. Validation asks whether what is printed can be true,
and there is a real difference: a date of death before the date of birth is not a
low-confidence reading, it is a record that cannot be correct. Both end up in front of a
reviewer, but for different reasons and with different fixes.

The checks are deliberately literal about what they can prove:

* **A field that is absent is not wrong**, it is missing - and only the fields a
  certificate is useless without are worth flagging, or every row carries a flag and a
  flag stops meaning anything.
* **A date that reads two ways is flagged even though it parsed.** "03-04-2019" is the
  third of April here; in a document typed by someone following American convention it
  is the fourth of March. Nothing in the text can settle it, so a person must. The
  ambiguity is invisible in the stored value, which is already ISO, so it is re-read
  from the snippet extraction kept of what was actually printed.
* **Order, not arithmetic, is what cross-field checks can prove.** A death before a
  birth is impossible; a birth in 1890 is merely unusual, and this stage does not guess
  at unusual.

Every flag names a thing a reviewer can look at and fix.
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Final

from certex.enums import CertificateType, FieldRole, ValidationFlag
from certex.fields import (
    EVENT_DATE_ROLES,
    FieldKind,
    FieldSchema,
    FieldSpec,
    schema_or_builtin,
)
from certex.pipeline.extract.values import read_date, read_sex

__all__ = ["FieldIssue", "RowFacts", "ValidationOutcome", "validate_row"]

_ISO_DATE: Final = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_CNIC: Final = re.compile(r"^\d{5}-\d{7}-\d$")
_MAX_HUMAN_AGE: Final = 130
"""Beyond this an age is a mis-read rather than a long life."""

_AGE_TOLERANCE_YEARS: Final = 1
"""A printed age is a whole number of years and may be rounded either way."""


@dataclass(frozen=True, slots=True)
class FieldIssue:
    """One thing wrong with one field."""

    field: str
    flag: ValidationFlag
    detail: str

    def as_json(self) -> dict[str, str]:
        return {"field": self.field, "flag": self.flag.value, "detail": self.detail}


@dataclass(frozen=True, slots=True)
class RowFacts:
    """What validation needs to know beyond the field values themselves."""

    certificate_type: CertificateType
    values: Mapping[str, str]
    field_schema: FieldSchema | None = None
    """The version this row was read under. None falls back to the built-in."""

    printed: Mapping[str, str] = field(default_factory=dict)
    """What each field looked like on the page, before normalisation."""

    field_confidences: Mapping[str, float] = field(default_factory=dict)
    type_confidence: float = 1.0
    boundary_confidence: float = 1.0
    ocr_used: bool = False
    ocr_mean_confidence: float | None = None
    today: dt.date | None = None

    @property
    def schema(self) -> FieldSchema:
        """The fields to validate against, however this row came to be read."""
        return schema_or_builtin(self.certificate_type, self.field_schema)


@dataclass(frozen=True, slots=True)
class ValidationOutcome:
    """Everything validation concluded about one row."""

    issues: list[FieldIssue] = field(default_factory=list)

    @property
    def flags(self) -> list[ValidationFlag]:
        """Distinct flags, in the order they were first raised."""
        seen: list[ValidationFlag] = []
        for issue in self.issues:
            if issue.flag not in seen:
                seen.append(issue.flag)
        return seen

    @property
    def flagged_fields(self) -> set[str]:
        return {issue.field for issue in self.issues if issue.field}

    def as_json(self) -> list[dict[str, str]]:
        return [issue.as_json() for issue in self.issues]


def _parsed_date(value: str | None) -> dt.date | None:
    if not value or not _ISO_DATE.match(value):
        return None
    try:
        return dt.date.fromisoformat(value)
    except ValueError:  # pragma: no cover - the pattern already guarantees the shape
        return None


def _check_completeness(facts: RowFacts, issues: list[FieldIssue]) -> None:
    present = {name for name, value in facts.values.items() if str(value).strip()}
    if not present:
        issues.append(
            FieldIssue(
                field="",
                flag=ValidationFlag.NO_FIELDS_EXTRACTED,
                detail="Nothing could be read from this certificate.",
            )
        )
        return
    for spec in facts.schema.fields:
        if spec.required and spec.name not in present:
            issues.append(
                FieldIssue(
                    field=spec.name,
                    flag=ValidationFlag.MISSING_REQUIRED,
                    detail=f"{spec.label} is missing.",
                )
            )


def _check_dates(facts: RowFacts, issues: list[FieldIssue]) -> None:
    today = facts.today or dt.date.today()
    for spec in facts.schema.fields:
        if spec.kind is not FieldKind.DATE:
            continue
        raw = str(facts.values.get(spec.name, "")).strip()
        if not raw:
            continue

        parsed = _parsed_date(raw)
        if parsed is None:
            # Extraction keeps what it could not read, so the reviewer sees the real
            # text; here that text is reported as unreadable rather than dropped.
            reading = read_date(raw)
            flag = (
                ValidationFlag.DATE_IMPOSSIBLE
                if reading.impossible
                else ValidationFlag.DATE_UNPARSEABLE
            )
            issues.append(
                FieldIssue(
                    field=spec.name,
                    flag=flag,
                    detail=f"{spec.label} reads {raw!r}, which is not a date.",
                )
            )
            continue

        if parsed > today:
            issues.append(
                FieldIssue(
                    field=spec.name,
                    flag=ValidationFlag.DATE_IN_FUTURE,
                    detail=f"{spec.label} is {raw}, which is in the future.",
                )
            )

        printed = str(facts.printed.get(spec.name, "")).strip()
        if printed and read_date(printed).ambiguous:
            issues.append(
                FieldIssue(
                    field=spec.name,
                    flag=ValidationFlag.DATE_AMBIGUOUS,
                    detail=(
                        f"{spec.label} is printed as {printed!r}, which reads as {raw} "
                        "day-first and as another valid date month-first."
                    ),
                )
            )


def _order(
    issues: list[FieldIssue],
    *,
    earlier: dt.date | None,
    later: dt.date | None,
    flag: ValidationFlag,
    field_name: str,
    detail: str,
) -> None:
    """Flag when two dates are in an order that cannot happen."""
    if earlier is None or later is None or earlier <= later:
        return
    issues.append(FieldIssue(field=field_name, flag=flag, detail=detail))


def _dates_for(facts: RowFacts, *roles: FieldRole) -> list[tuple[FieldSpec, dt.date]]:
    """Every parseable date the schema gives these roles, with its field."""
    found: list[tuple[FieldSpec, dt.date]] = []
    for spec in facts.schema.by_role(*roles):
        parsed = _parsed_date(facts.values.get(spec.name))
        if parsed is not None:
            found.append((spec, parsed))
    return found


def _check_consistency(facts: RowFacts, issues: list[FieldIssue]) -> None:
    """Dates that cannot sit in the order they are printed in.

    Driven entirely by roles, so the same code checks a death certificate with one
    date of birth and a marriage certificate with two - and checks a schema an
    operator invented this morning, whose fields this file has never heard of.
    """
    births = _dates_for(facts, FieldRole.BIRTH_DATE)
    deaths = _dates_for(facts, FieldRole.DEATH_DATE)
    marriages = _dates_for(facts, FieldRole.MARRIAGE_DATE)
    registrations = _dates_for(facts, FieldRole.REGISTRATION_DATE)
    issues_dates = _dates_for(facts, FieldRole.ISSUE_DATE)

    for death_spec, death in deaths:
        for _birth_spec, birth in births:
            _order(
                issues,
                earlier=birth,
                later=death,
                flag=ValidationFlag.DOD_BEFORE_DOB,
                field_name=death_spec.name,
                detail="The date of death is before the date of birth.",
            )

    for marriage_spec, marriage in marriages:
        for birth_spec, birth in births:
            _order(
                issues,
                earlier=birth,
                later=marriage,
                flag=ValidationFlag.MARRIAGE_BEFORE_BIRTH,
                field_name=marriage_spec.name,
                detail=(f"The marriage is dated before {birth_spec.label.lower()} would allow."),
            )

    # The event a certificate records is whichever of these it carries, in the
    # order the registry files entries under - shared with the register itself, so
    # a validation message and a search result never disagree about which date the
    # certificate is about.
    event = next(
        (date for role in EVENT_DATE_ROLES for _spec, date in _dates_for(facts, role)),
        None,
    )

    for registration_spec, registration in registrations:
        _order(
            issues,
            earlier=event,
            later=registration,
            flag=ValidationFlag.REGISTRATION_BEFORE_EVENT,
            field_name=registration_spec.name,
            detail="The registration is dated before the event it records.",
        )
        for issue_spec, issued in issues_dates:
            _order(
                issues,
                earlier=registration,
                later=issued,
                flag=ValidationFlag.ISSUE_BEFORE_REGISTRATION,
                field_name=issue_spec.name,
                detail="The certificate is dated before the entry was registered.",
            )

    _check_age(facts, issues, births=births, deaths=deaths)


def _check_age(
    facts: RowFacts,
    issues: list[FieldIssue],
    *,
    births: list[tuple[FieldSpec, dt.date]],
    deaths: list[tuple[FieldSpec, dt.date]],
) -> None:
    """An age that disagrees with the dates printed beside it."""
    for spec in facts.schema.by_role(FieldRole.AGE):
        raw = str(facts.values.get(spec.name, "")).strip()
        if not raw:
            continue
        try:
            age = int(float(raw))
        except ValueError:
            issues.append(
                FieldIssue(
                    field=spec.name,
                    flag=ValidationFlag.VALUE_TRUNCATED,
                    detail=f"The age reads {raw!r}, which is not a number.",
                )
            )
            continue

        if not 0 <= age <= _MAX_HUMAN_AGE:
            issues.append(
                FieldIssue(
                    field=spec.name,
                    flag=ValidationFlag.AGE_INCONSISTENT,
                    detail=f"An age of {age} is not possible.",
                )
            )
            continue

        if not births or not deaths:
            continue
        birth = births[0][1]
        death = deaths[0][1]
        lived = death.year - birth.year - ((death.month, death.day) < (birth.month, birth.day))
        if abs(lived - age) > _AGE_TOLERANCE_YEARS:
            issues.append(
                FieldIssue(
                    field=spec.name,
                    flag=ValidationFlag.AGE_INCONSISTENT,
                    detail=(
                        f"The age reads {age}, but the dates give {lived} years between "
                        "birth and death."
                    ),
                )
            )


def _check_identifiers(facts: RowFacts, issues: list[FieldIssue]) -> None:
    seen: dict[str, str] = {}
    for spec in facts.schema.fields:
        if spec.kind is not FieldKind.ID_NUMBER:
            continue
        raw = str(facts.values.get(spec.name, "")).strip()
        if not raw:
            continue
        if not _CNIC.match(raw):
            issues.append(
                FieldIssue(
                    field=spec.name,
                    flag=ValidationFlag.CNIC_INVALID,
                    detail=(
                        f"{spec.label} reads {raw!r}, which is not a 13-digit identity number."
                    ),
                )
            )
            continue
        if raw in seen:
            # Two people on one certificate cannot share an identity number; one of the
            # two labels was almost certainly read onto the wrong value.
            issues.append(
                FieldIssue(
                    field=spec.name,
                    flag=ValidationFlag.DUPLICATE_ID_IN_ROW,
                    detail=f"{spec.label} is the same number as {seen[raw]}.",
                )
            )
        seen[raw] = spec.label


def _check_enumerations(facts: RowFacts, issues: list[FieldIssue]) -> None:
    for spec in facts.schema.fields:
        if spec.kind is not FieldKind.SEX:
            continue
        raw = str(facts.values.get(spec.name, "")).strip()
        if raw and read_sex(raw) is None:
            issues.append(
                FieldIssue(
                    field=spec.name,
                    flag=ValidationFlag.SEX_UNRECOGNISED,
                    detail=f"{spec.label} reads {raw!r}, which is neither male nor female.",
                )
            )


def _check_reading_quality(
    facts: RowFacts, issues: list[FieldIssue], *, min_field_confidence: float
) -> None:
    if facts.certificate_type is CertificateType.OTHER:
        issues.append(
            FieldIssue(
                field="",
                flag=ValidationFlag.UNKNOWN_CERTIFICATE_TYPE,
                detail="This does not read as a birth, death or marriage certificate.",
            )
        )
    if facts.type_confidence < min_field_confidence:
        issues.append(
            FieldIssue(
                field="",
                flag=ValidationFlag.LOW_TYPE_CONFIDENCE,
                detail="The kind of certificate is not certain.",
            )
        )
    if facts.boundary_confidence < min_field_confidence:
        issues.append(
            FieldIssue(
                field="",
                flag=ValidationFlag.LOW_BOUNDARY_CONFIDENCE,
                detail="Where this certificate starts and ends is not certain.",
            )
        )
    if facts.ocr_used and facts.ocr_mean_confidence is not None:
        if facts.ocr_mean_confidence <= 0:
            issues.append(
                FieldIssue(
                    field="",
                    flag=ValidationFlag.OCR_EMPTY,
                    detail="The scan produced no readable text.",
                )
            )
        elif facts.ocr_mean_confidence < min_field_confidence * 100:
            issues.append(
                FieldIssue(
                    field="",
                    flag=ValidationFlag.LOW_OCR_CONFIDENCE,
                    detail="The scan was hard to read; check every value against the page.",
                )
            )

    for name, confidence in facts.field_confidences.items():
        if confidence < min_field_confidence and str(facts.values.get(name, "")).strip():
            issues.append(
                FieldIssue(
                    field=name,
                    flag=ValidationFlag.LOW_FIELD_CONFIDENCE,
                    detail="This value was read with low confidence.",
                )
            )


def validate_row(facts: RowFacts, *, min_field_confidence: float = 0.6) -> ValidationOutcome:
    """Every way this row is questionable, in the order a reviewer would meet them."""
    issues: list[FieldIssue] = []
    _check_completeness(facts, issues)
    _check_dates(facts, issues)
    _check_consistency(facts, issues)
    _check_identifiers(facts, issues)
    _check_enumerations(facts, issues)
    _check_reading_quality(facts, issues, min_field_confidence=min_field_confidence)
    return ValidationOutcome(issues=issues)
