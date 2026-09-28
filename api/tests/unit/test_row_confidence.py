"""Scoring a row, and deciding who sees it next.

The score's only job is to be trusted, so the tests are about the properties an operator
relies on when they set a threshold: reading more of a certificate scores higher, a
missing required field costs more than a missing optional one, a record that contradicts
itself costs more than one that was merely hard to read, and nothing automated ever
reaches certainty.
"""

from __future__ import annotations

import pytest

from certex.enums import CertificateType, ExtractionMethod, ReviewStatus, ValidationFlag
from certex.pipeline.validate.confidence import CONFIDENCE_CEILING, route_row, score_row
from certex.pipeline.validate.rules import FieldIssue, RowFacts, ValidationOutcome, validate_row

pytestmark = pytest.mark.unit

COMPLETE_BIRTH = {
    "certificate_number": "BC-2019-004471",
    "registration_number": "REG/LHR/2019/88213",
    "registration_date": "2019-04-02",
    "issuing_authority": "Union Council 42, Lahore",
    "registrar_name": "Muhammad Aslam",
    "date_of_issue": "2019-04-09",
    "child_full_name": "Ayesha Noor Malik",
    "sex": "F",
    "date_of_birth": "1987-03-14",
    "time_of_birth": "04:25",
    "place_of_birth": "Services Hospital, Lahore",
    "father_full_name": "Tariq Mahmood Malik",
    "father_id_number": "35201-1234567-1",
    "mother_full_name": "Nasreen Akhtar",
    "mother_id_number": "35201-7654321-8",
    "permanent_address": "House 14, Street 7, Gulberg III, Lahore",
    "informant_name": "Tariq Mahmood Malik",
}

NOTHING = ValidationOutcome()


def outcome_with(*flags: ValidationFlag) -> ValidationOutcome:
    return ValidationOutcome(issues=[FieldIssue(field="", flag=flag, detail="") for flag in flags])


def score(
    values: dict[str, str],
    *,
    confidence: float = 1.0,
    method: ExtractionMethod = ExtractionMethod.RULE,
    outcome: ValidationOutcome = NOTHING,
    certificate_type: CertificateType = CertificateType.BIRTH,
    type_confidence: float = 1.0,
) -> float:
    return score_row(
        certificate_type=certificate_type,
        values=values,
        field_confidences=dict.fromkeys(values, confidence),
        field_methods=dict.fromkeys(values, method.value),
        outcome=outcome,
        type_confidence=type_confidence,
    )


class TestReadingMore:
    def test_a_fully_read_certificate_scores_high(self) -> None:
        assert score(COMPLETE_BIRTH) > 0.9

    def test_reading_fewer_fields_scores_lower(self) -> None:
        half = dict(list(COMPLETE_BIRTH.items())[:8])
        assert score(half) < score(COMPLETE_BIRTH)

    def test_a_field_that_was_not_read_counts_as_zero_not_as_absent(self) -> None:
        # Averaging only what was found would let a row with two good fields beat one
        # with fourteen.
        two_fields = {
            "certificate_number": "BC-2019-004471",
            "child_full_name": "Ayesha Noor Malik",
        }
        assert score(two_fields) < 0.5

    def test_an_empty_row_scores_zero(self) -> None:
        assert score({}) == 0.0


class TestWhatCountsForMore:
    def test_losing_a_required_field_costs_more_than_an_optional_one(self) -> None:
        without_required = {
            name: value for name, value in COMPLETE_BIRTH.items() if name != "child_full_name"
        }
        without_optional = {
            name: value for name, value in COMPLETE_BIRTH.items() if name != "informant_name"
        }
        assert score(without_required) < score(without_optional)

    def test_a_template_reading_beats_a_rule_reading(self) -> None:
        # A template is a person's decision about this form, applied again.
        by_template = score(COMPLETE_BIRTH, method=ExtractionMethod.TEMPLATE)
        by_rule = score(COMPLETE_BIRTH, method=ExtractionMethod.RULE)
        assert by_template > by_rule

    def test_values_read_with_less_confidence_score_lower(self) -> None:
        assert score(COMPLETE_BIRTH, confidence=0.6) < score(COMPLETE_BIRTH, confidence=1.0)

    def test_an_uncertain_type_holds_back_a_row_the_fields_do_not_corroborate(self) -> None:
        # Few fields found and a doubtful classification: the fields may have been looked
        # for in the wrong schema entirely.
        sparse = dict(list(COMPLETE_BIRTH.items())[:4])
        assert score(sparse, type_confidence=0.3) < score(sparse) * 0.8

    def test_finding_every_field_corroborates_a_doubtful_classification(self) -> None:
        # A keyword score of 0.89 should barely mark down a certificate whose every field
        # was read: having them all is better evidence of the type than the keywords are.
        # Multiplying by the keyword score would cost eleven points instead of five.
        certain = score(COMPLETE_BIRTH)
        doubtful = score(COMPLETE_BIRTH, type_confidence=0.89)
        assert doubtful > 0.9
        assert certain - doubtful < 0.06


class TestFlags:
    def test_a_record_that_contradicts_itself_costs_more_than_a_hard_read(self) -> None:
        contradiction = score(COMPLETE_BIRTH, outcome=outcome_with(ValidationFlag.DOD_BEFORE_DOB))
        hard_to_read = score(
            COMPLETE_BIRTH, outcome=outcome_with(ValidationFlag.LOW_OCR_CONFIDENCE)
        )
        assert contradiction < hard_to_read < score(COMPLETE_BIRTH)

    def test_more_flags_cost_more(self) -> None:
        one = score(COMPLETE_BIRTH, outcome=outcome_with(ValidationFlag.DATE_AMBIGUOUS))
        two = score(
            COMPLETE_BIRTH,
            outcome=outcome_with(ValidationFlag.DATE_AMBIGUOUS, ValidationFlag.CNIC_INVALID),
        )
        assert two < one

    def test_a_pile_of_flags_still_leaves_a_usable_score(self) -> None:
        # The score has to keep ranking rows against each other, not collapse to zero.
        everything = outcome_with(*list(ValidationFlag)[:12])
        assert score(COMPLETE_BIRTH, outcome=everything) > 0.0


class TestCeiling:
    def test_no_automated_reading_reaches_certainty(self) -> None:
        assert score(COMPLETE_BIRTH) <= CONFIDENCE_CEILING

    def test_the_ceiling_leaves_room_for_review_everything(self) -> None:
        # An operator sets the threshold to 1.0 to mean "nobody is skipped".
        assert CONFIDENCE_CEILING < 1.0


class TestRouting:
    def test_a_clean_high_scoring_row_can_be_approved(self) -> None:
        status = route_row(
            confidence=0.95, outcome=NOTHING, auto_approve_at=0.90, review_floor=0.60
        )
        assert status is ReviewStatus.AUTO_APPROVED

    def test_any_flag_sends_a_row_to_review_however_high_it_scores(self) -> None:
        status = route_row(
            confidence=0.99,
            outcome=outcome_with(ValidationFlag.DATE_AMBIGUOUS),
            auto_approve_at=0.90,
            review_floor=0.60,
        )
        assert status is ReviewStatus.NEEDS_REVIEW

    def test_a_middling_score_goes_to_review(self) -> None:
        status = route_row(
            confidence=0.75, outcome=NOTHING, auto_approve_at=0.90, review_floor=0.60
        )
        assert status is ReviewStatus.NEEDS_REVIEW

    def test_a_low_score_is_a_failure_not_a_review(self) -> None:
        status = route_row(confidence=0.4, outcome=NOTHING, auto_approve_at=0.90, review_floor=0.60)
        assert status is ReviewStatus.FAILED

    def test_review_everything_means_nothing_is_approved(self) -> None:
        # The office's setting: a person confirms every row.
        status = route_row(
            confidence=CONFIDENCE_CEILING,
            outcome=NOTHING,
            auto_approve_at=1.0,
            review_floor=0.60,
        )
        assert status is ReviewStatus.NEEDS_REVIEW


class TestAgainstRealValidation:
    def test_a_clean_certificate_scores_near_the_ceiling(self) -> None:
        outcome = validate_row(
            RowFacts(certificate_type=CertificateType.BIRTH, values=COMPLETE_BIRTH)
        )
        assert outcome.flags == []
        assert score(COMPLETE_BIRTH, outcome=outcome) > 0.9

    def test_a_contradictory_certificate_is_marked_down_and_reviewed(self) -> None:
        values = {
            "certificate_number": "DC-2021-000913",
            "deceased_full_name": "Abdul Rehman Qureshi",
            "date_of_birth": "1948-06-30",
            "date_of_death": "1940-01-01",
        }
        outcome = validate_row(RowFacts(certificate_type=CertificateType.DEATH, values=values))
        confidence = score(values, outcome=outcome, certificate_type=CertificateType.DEATH)

        assert ValidationFlag.DOD_BEFORE_DOB in outcome.flags
        status = route_row(
            confidence=confidence, outcome=outcome, auto_approve_at=0.90, review_floor=0.60
        )
        assert status in (ReviewStatus.NEEDS_REVIEW, ReviewStatus.FAILED)
