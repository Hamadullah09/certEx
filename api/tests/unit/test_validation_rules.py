"""What has to be true about a certificate.

Each test is a record that a records office would want stopped, or one it would not - and
the difference matters as much as the catching. A flag on every row is a flag on none, so
there are as many tests here for what must *not* be flagged as for what must.
"""

from __future__ import annotations

import datetime as dt

import pytest

from certex.enums import CertificateType, ValidationFlag
from certex.pipeline.validate.rules import RowFacts, validate_row

pytestmark = pytest.mark.unit

TODAY = dt.date(2026, 9, 28)

GOOD_BIRTH = {
    "certificate_number": "BC-2019-004471",
    "registration_number": "REG/LHR/2019/88213",
    "registration_date": "2019-04-02",
    "child_full_name": "Ayesha Noor Malik",
    "sex": "F",
    "date_of_birth": "1987-03-14",
    "place_of_birth": "Services Hospital, Lahore",
    "father_full_name": "Tariq Mahmood Malik",
    "father_id_number": "35201-1234567-1",
    "mother_full_name": "Nasreen Akhtar",
    "mother_id_number": "35201-7654321-8",
    "date_of_issue": "2019-04-09",
}

GOOD_DEATH = {
    "certificate_number": "DC-2021-000913",
    "registration_date": "2021-11-20",
    "deceased_full_name": "Abdul Rehman Qureshi",
    "date_of_birth": "1948-06-30",
    "date_of_death": "2021-11-17",
    "age_at_death": "73",
    "deceased_id_number": "42101-9988776-5",
}

GOOD_MARRIAGE = {
    "certificate_number": "MC-2015-007788",
    "date_of_marriage": "2015-09-12",
    "groom_full_name": "Hamza Bin Yousaf",
    "groom_date_of_birth": "1988-01-22",
    "bride_full_name": "Sana Fatima",
    "bride_date_of_birth": "1991-07-08",
}


def check(
    values: dict[str, str],
    *,
    certificate_type: CertificateType = CertificateType.BIRTH,
    **facts: object,
) -> list[ValidationFlag]:
    return validate_row(
        RowFacts(
            certificate_type=certificate_type,
            values=values,
            today=TODAY,
            **facts,  # type: ignore[arg-type]
        )
    ).flags


class TestAGoodRecord:
    @pytest.mark.parametrize(
        ("values", "certificate_type"),
        [
            (GOOD_BIRTH, CertificateType.BIRTH),
            (GOOD_DEATH, CertificateType.DEATH),
            (GOOD_MARRIAGE, CertificateType.MARRIAGE),
        ],
    )
    def test_nothing_is_flagged(
        self, values: dict[str, str], certificate_type: CertificateType
    ) -> None:
        assert check(values, certificate_type=certificate_type) == []

    def test_an_optional_field_that_is_missing_is_not_a_problem(self) -> None:
        # Not every certificate prints a time of birth or an informant.
        assert check(GOOD_BIRTH) == []


class TestCompleteness:
    def test_a_certificate_without_its_number_is_incomplete(self) -> None:
        values = {name: value for name, value in GOOD_BIRTH.items() if name != "certificate_number"}
        assert ValidationFlag.MISSING_REQUIRED in check(values)

    def test_a_certificate_without_its_subject_is_incomplete(self) -> None:
        values = {name: value for name, value in GOOD_BIRTH.items() if name != "child_full_name"}
        assert ValidationFlag.MISSING_REQUIRED in check(values)

    def test_a_row_that_read_nothing_says_so_once(self) -> None:
        flags = check({})
        assert flags == [ValidationFlag.NO_FIELDS_EXTRACTED], (
            "a blank row needs one clear reason, not one per missing field"
        )

    def test_a_blank_string_is_missing_not_present(self) -> None:
        values = {**GOOD_BIRTH, "certificate_number": "   "}
        assert ValidationFlag.MISSING_REQUIRED in check(values)


class TestDates:
    def test_a_date_that_did_not_parse_is_reported(self) -> None:
        values = {**GOOD_BIRTH, "date_of_birth": "sometime in 1987"}
        assert ValidationFlag.DATE_UNPARSEABLE in check(values)

    def test_a_date_that_cannot_exist_is_reported(self) -> None:
        values = {**GOOD_BIRTH, "registration_date": "31-02-2019"}
        assert ValidationFlag.DATE_IMPOSSIBLE in check(values)

    def test_a_date_in_the_future_is_reported(self) -> None:
        values = {**GOOD_BIRTH, "date_of_issue": "2030-01-01"}
        assert ValidationFlag.DATE_IN_FUTURE in check(values)

    def test_a_date_that_reads_two_ways_is_reported(self) -> None:
        # Stored as ISO, so the ambiguity is only visible in what was printed.
        flags = check(
            {**GOOD_BIRTH, "date_of_birth": "2019-04-03"},
            printed={"date_of_birth": "03-04-2019"},
        )
        assert ValidationFlag.DATE_AMBIGUOUS in flags

    def test_a_date_that_reads_only_one_way_is_not_reported(self) -> None:
        flags = check(
            {**GOOD_BIRTH, "date_of_birth": "2019-12-25"},
            printed={"date_of_birth": "25-12-2019"},
        )
        assert ValidationFlag.DATE_AMBIGUOUS not in flags

    def test_a_date_written_in_words_is_not_ambiguous(self) -> None:
        flags = check(
            {**GOOD_BIRTH, "date_of_birth": "2019-03-14"},
            printed={"date_of_birth": "14 March 2019"},
        )
        assert ValidationFlag.DATE_AMBIGUOUS not in flags


class TestConsistency:
    def test_a_death_before_a_birth(self) -> None:
        values = {**GOOD_DEATH, "date_of_death": "1940-01-01", "age_at_death": "0"}
        assert ValidationFlag.DOD_BEFORE_DOB in check(
            values, certificate_type=CertificateType.DEATH
        )

    def test_a_marriage_before_the_groom_was_born(self) -> None:
        values = {**GOOD_MARRIAGE, "groom_date_of_birth": "2020-01-22"}
        flags = check(values, certificate_type=CertificateType.MARRIAGE)
        assert ValidationFlag.MARRIAGE_BEFORE_BIRTH in flags

    def test_a_marriage_before_the_bride_was_born(self) -> None:
        values = {**GOOD_MARRIAGE, "bride_date_of_birth": "2020-07-08"}
        flags = check(values, certificate_type=CertificateType.MARRIAGE)
        assert ValidationFlag.MARRIAGE_BEFORE_BIRTH in flags

    def test_a_registration_before_the_event_it_records(self) -> None:
        values = {**GOOD_BIRTH, "registration_date": "1980-01-01"}
        assert ValidationFlag.REGISTRATION_BEFORE_EVENT in check(values)

    def test_a_certificate_issued_before_the_entry_was_registered(self) -> None:
        values = {**GOOD_BIRTH, "date_of_issue": "2019-04-01"}
        assert ValidationFlag.ISSUE_BEFORE_REGISTRATION in check(values)

    def test_a_registration_on_the_day_of_the_event_is_fine(self) -> None:
        values = {**GOOD_BIRTH, "date_of_birth": "2019-04-02", "registration_date": "2019-04-02"}
        assert ValidationFlag.REGISTRATION_BEFORE_EVENT not in check(values)

    def test_one_date_missing_means_no_comparison_is_made(self) -> None:
        values = {name: value for name, value in GOOD_DEATH.items() if name != "date_of_birth"}
        values.pop("age_at_death")
        flags = check(values, certificate_type=CertificateType.DEATH)
        assert ValidationFlag.DOD_BEFORE_DOB not in flags


class TestAge:
    def test_an_age_that_contradicts_the_dates(self) -> None:
        values = {**GOOD_DEATH, "age_at_death": "42"}
        assert ValidationFlag.AGE_INCONSISTENT in check(
            values, certificate_type=CertificateType.DEATH
        )

    def test_an_age_a_year_out_is_accepted(self) -> None:
        # A printed age is whole years and may be rounded either way.
        values = {**GOOD_DEATH, "age_at_death": "72"}
        assert ValidationFlag.AGE_INCONSISTENT not in check(
            values, certificate_type=CertificateType.DEATH
        )

    def test_an_impossible_age(self) -> None:
        values = {**GOOD_DEATH, "age_at_death": "173"}
        assert ValidationFlag.AGE_INCONSISTENT in check(
            values, certificate_type=CertificateType.DEATH
        )

    def test_an_age_that_is_not_a_number(self) -> None:
        values = {**GOOD_DEATH, "age_at_death": "seventy three"}
        assert ValidationFlag.VALUE_TRUNCATED in check(
            values, certificate_type=CertificateType.DEATH
        )

    def test_an_age_without_dates_to_check_it_against(self) -> None:
        values = {
            "certificate_number": "DC-1",
            "deceased_full_name": "X",
            "date_of_death": "2021-11-17",
            "age_at_death": "73",
        }
        assert ValidationFlag.AGE_INCONSISTENT not in check(
            values, certificate_type=CertificateType.DEATH
        )


class TestIdentityNumbers:
    def test_a_number_that_is_not_a_cnic(self) -> None:
        values = {**GOOD_BIRTH, "father_id_number": "3520-123-1"}
        assert ValidationFlag.CNIC_INVALID in check(values)

    def test_two_people_sharing_one_identity_number(self) -> None:
        # Almost always a label read onto the wrong value.
        values = {**GOOD_BIRTH, "mother_id_number": GOOD_BIRTH["father_id_number"]}
        assert ValidationFlag.DUPLICATE_ID_IN_ROW in check(values)

    def test_a_missing_identity_number_is_not_invalid(self) -> None:
        values = {name: value for name, value in GOOD_BIRTH.items() if name != "father_id_number"}
        assert ValidationFlag.CNIC_INVALID not in check(values)


class TestEnumerations:
    def test_a_sex_that_could_not_be_read(self) -> None:
        values = {**GOOD_BIRTH, "sex": "?"}
        assert ValidationFlag.SEX_UNRECOGNISED in check(values)

    @pytest.mark.parametrize("value", ["M", "F"])
    def test_a_canonical_sex_is_accepted(self, value: str) -> None:
        assert ValidationFlag.SEX_UNRECOGNISED not in check({**GOOD_BIRTH, "sex": value})


class TestHowItWasRead:
    def test_an_unclassified_certificate_is_flagged(self) -> None:
        flags = check(
            {"certificate_number": "BC-1", "issuing_authority": "Union Council 42"},
            certificate_type=CertificateType.OTHER,
        )
        assert ValidationFlag.UNKNOWN_CERTIFICATE_TYPE in flags

    def test_an_uncertain_classification_is_flagged(self) -> None:
        assert ValidationFlag.LOW_TYPE_CONFIDENCE in check(GOOD_BIRTH, type_confidence=0.3)

    def test_an_uncertain_page_range_is_flagged(self) -> None:
        # The split may have put two certificates in one row.
        assert ValidationFlag.LOW_BOUNDARY_CONFIDENCE in check(GOOD_BIRTH, boundary_confidence=0.55)

    def test_a_hard_to_read_scan_is_flagged(self) -> None:
        flags = check(GOOD_BIRTH, ocr_used=True, ocr_mean_confidence=40.0)
        assert ValidationFlag.LOW_OCR_CONFIDENCE in flags

    def test_a_clean_scan_is_not_flagged(self) -> None:
        flags = check(GOOD_BIRTH, ocr_used=True, ocr_mean_confidence=92.0)
        assert ValidationFlag.LOW_OCR_CONFIDENCE not in flags

    def test_a_scan_that_read_nothing(self) -> None:
        flags = check(GOOD_BIRTH, ocr_used=True, ocr_mean_confidence=0.0)
        assert ValidationFlag.OCR_EMPTY in flags

    def test_a_value_read_with_low_confidence_is_flagged_by_name(self) -> None:
        outcome = validate_row(
            RowFacts(
                certificate_type=CertificateType.BIRTH,
                values=GOOD_BIRTH,
                field_confidences={"father_full_name": 0.4},
                today=TODAY,
            )
        )
        assert ValidationFlag.LOW_FIELD_CONFIDENCE in outcome.flags
        assert "father_full_name" in outcome.flagged_fields


class TestWhatAReviewerSees:
    def test_every_issue_names_a_field_and_explains_itself(self) -> None:
        outcome = validate_row(
            RowFacts(
                certificate_type=CertificateType.DEATH,
                values={**GOOD_DEATH, "age_at_death": "42"},
                today=TODAY,
            )
        )
        (issue,) = outcome.issues
        assert issue.field == "age_at_death"
        assert "73 years" in issue.detail

    def test_the_same_flag_is_listed_once_however_often_it_is_raised(self) -> None:
        values = {
            **GOOD_BIRTH,
            "father_id_number": "not a number",
            "mother_id_number": "also not one",
        }
        outcome = validate_row(
            RowFacts(certificate_type=CertificateType.BIRTH, values=values, today=TODAY)
        )
        assert outcome.flags.count(ValidationFlag.CNIC_INVALID) == 1
        assert len([issue for issue in outcome.issues if issue.field.endswith("id_number")]) == 2

    def test_issues_serialise_for_storage(self) -> None:
        outcome = validate_row(
            RowFacts(
                certificate_type=CertificateType.BIRTH,
                values={**GOOD_BIRTH, "sex": "?"},
                today=TODAY,
            )
        )
        assert outcome.as_json() == [
            {
                "field": "sex",
                "flag": "SEX_UNRECOGNISED",
                "detail": "Sex reads '?', which is neither male nor female.",
            }
        ]
