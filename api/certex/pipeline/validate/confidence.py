"""How sure the system is about a whole row, and who sees it next.

The number has one job: to be trusted. A row that scores 0.95 and is wrong is worse than
no score at all, because the whole point of routing is that a person's attention goes
where it is needed. So the score is built only from things that were actually observed,
and every one of them can only lower it:

* **How well the fields were read.** Required fields count for more, because a
  certificate without its number and its subject's name is not a record at all - but a
  row full of confidently-read optional fields and no name should not score well.
* **A field that is missing counts as zero**, rather than being left out of the average.
  Averaging only what was found lets a row that read two fields out of fifteen score
  higher than one that read fourteen.
* **What validation found.** A flag that says the record cannot be true costs more than
  one that says a value was hard to read.
* **The certificate type, as far as the fields do not already settle it.** A doubtful
  classification matters because the fields were looked for in the wrong schema - but
  finding a birth certificate's fields *is* evidence that it is one, and better evidence
  than a keyword count. So the type only holds a row back while the fields have not
  corroborated it.
* **A ceiling.** An automated reading is never certainty, so the score stops short of
  1.0. That also gives an operator a way to say "let nobody through without looking":
  set the threshold to 1.0 and nothing can ever clear it.

Routing then follows from the score and the flags, and the rule is deliberately blunt:
any flag at all means a person looks, however high the score.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

from certex.enums import CertificateType, ExtractionMethod, ReviewStatus, ValidationFlag
from certex.fields import FieldSchema, schema_or_builtin
from certex.pipeline.validate.rules import ValidationOutcome

__all__ = [
    "CONFIDENCE_CEILING",
    "REQUIRED_FIELD_WEIGHT",
    "route_row",
    "score_row",
]

CONFIDENCE_CEILING: Final = 0.99
"""An automated reading is never certainty; this leaves 1.0 meaning "a person said so"."""

REQUIRED_FIELD_WEIGHT: Final = 3.0
"""How much more a required field counts than an optional one."""

_METHOD_PRIOR: Final[dict[ExtractionMethod, float]] = {
    # A human correction is fact. A template is a human's decision about this form,
    # applied again. A rule is a generalisation that has not been checked on this form.
    ExtractionMethod.MANUAL: 1.0,
    ExtractionMethod.TEMPLATE: 1.0,
    ExtractionMethod.RULE: 0.95,
    ExtractionMethod.LLM: 0.85,
    ExtractionMethod.NONE: 0.0,
}

_SERIOUS_FLAGS: Final[frozenset[ValidationFlag]] = frozenset(
    {
        # The record contradicts itself: something is certainly wrong, not merely unclear.
        ValidationFlag.NO_FIELDS_EXTRACTED,
        ValidationFlag.DOD_BEFORE_DOB,
        ValidationFlag.MARRIAGE_BEFORE_BIRTH,
        ValidationFlag.REGISTRATION_BEFORE_EVENT,
        ValidationFlag.ISSUE_BEFORE_REGISTRATION,
        ValidationFlag.AGE_INCONSISTENT,
        ValidationFlag.DATE_IMPOSSIBLE,
        ValidationFlag.DATE_IN_FUTURE,
        ValidationFlag.DUPLICATE_ID_IN_ROW,
        ValidationFlag.MISSING_REQUIRED,
        ValidationFlag.OCR_EMPTY,
        ValidationFlag.TEXT_EXTRACTION_FAILED,
    }
)

_SERIOUS_FLAG_COST: Final = 0.25
_MINOR_FLAG_COST: Final = 0.08
_MAX_FLAG_COST: Final = 0.6
"""However many flags a row carries, the score keeps some information in it."""


def score_row(
    *,
    certificate_type: CertificateType,
    values: Mapping[str, str],
    field_confidences: Mapping[str, float],
    field_methods: Mapping[str, str],
    outcome: ValidationOutcome,
    type_confidence: float = 1.0,
    schema: FieldSchema | None = None,
) -> float:
    """How much of this certificate was read, and how well."""
    specs = schema_or_builtin(certificate_type, schema).fields
    if not specs:  # pragma: no cover - every type has at least the common fields
        return 0.0

    weighted = 0.0
    total_weight = 0.0
    for spec in specs:
        weight = REQUIRED_FIELD_WEIGHT if spec.required else 1.0
        total_weight += weight
        value = str(values.get(spec.name, "")).strip()
        if not value:
            continue  # counts as zero: a field not read is not a field read badly
        confidence = float(field_confidences.get(spec.name, 0.0))
        method = _method_of(field_methods.get(spec.name))
        weighted += weight * confidence * _METHOD_PRIOR[method]

    coverage = weighted / total_weight
    penalty = min(_MAX_FLAG_COST, sum(_cost_of(flag) for flag in outcome.flags))
    # Reading this type's fields corroborates the type, so a keyword score of 0.89 does
    # not hold back a certificate whose every field was found.
    type_support = max(type_confidence, coverage)
    score = coverage * type_support * (1.0 - penalty)
    return round(min(CONFIDENCE_CEILING, max(0.0, score)), 4)


def _method_of(raw: str | None) -> ExtractionMethod:
    try:
        return ExtractionMethod(raw) if raw else ExtractionMethod.NONE
    except ValueError:  # pragma: no cover - methods are written by this system
        return ExtractionMethod.NONE


def _cost_of(flag: ValidationFlag) -> float:
    return _SERIOUS_FLAG_COST if flag in _SERIOUS_FLAGS else _MINOR_FLAG_COST


def route_row(
    *,
    confidence: float,
    outcome: ValidationOutcome,
    auto_approve_at: float,
    review_floor: float,
) -> ReviewStatus:
    """Who sees this row next.

    A flag always means a person looks. Setting ``auto_approve_at`` to 1.0 means nobody
    is ever skipped, because the score cannot reach it.
    """
    if confidence < review_floor:
        return ReviewStatus.FAILED
    if outcome.flags or confidence < auto_approve_at:
        return ReviewStatus.NEEDS_REVIEW
    return ReviewStatus.AUTO_APPROVED
