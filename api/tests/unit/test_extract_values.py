"""Turning what is printed into what is stored.

A CSV holding "14-03-2019", "14/03/2019" and "14 March 2019" is not a dataset, so each
field kind has one written form. Two behaviours matter more than the conversions
themselves: a value that cannot be understood is kept exactly as printed rather than
dropped, and a date that could be read two ways says so.
"""

from __future__ import annotations

import pytest

from certex.fields import FieldKind
from certex.pipeline.extract.values import (
    format_cnic,
    normalize_field_value,
    read_date,
    read_sex,
    read_time,
)

pytestmark = pytest.mark.unit


def eastern(text: str) -> str:
    """The same digits as an Urdu form prints them (extended Arabic-Indic)."""
    return text.translate({ord(str(digit)): chr(0x06F0 + digit) for digit in range(10)})


class TestDates:
    @pytest.mark.parametrize(
        ("printed", "iso"),
        [
            ("14-03-2019", "2019-03-14"),
            ("14/03/2019", "2019-03-14"),
            ("14.03.2019", "2019-03-14"),
            ("2019-03-14", "2019-03-14"),
            ("2019/3/14", "2019-03-14"),
            ("14 March 2019", "2019-03-14"),
            ("14 Mar 2019", "2019-03-14"),
            ("1 January 2020", "2020-01-01"),
        ],
    )
    def test_a_printed_date_becomes_iso(self, printed: str, iso: str) -> None:
        assert read_date(printed).iso == iso

    def test_pakistani_forms_are_read_day_first(self) -> None:
        # The third of April, not the fourth of March.
        assert read_date("03-04-2019").iso == "2019-04-03"

    def test_a_date_that_could_be_read_either_way_says_so(self) -> None:
        reading = read_date("03-04-2019")
        assert reading.ambiguous is True

    @pytest.mark.parametrize("printed", ["14-03-2019", "25/12/2020", "2019-03-14"])
    def test_a_date_that_can_only_be_read_one_way_is_not_ambiguous(self, printed: str) -> None:
        assert read_date(printed).ambiguous is False

    def test_the_same_number_twice_is_not_ambiguous(self) -> None:
        # 12-12-2019 reads the same either way round.
        assert read_date("12-12-2019").ambiguous is False

    def test_a_date_that_is_not_a_date_is_reported(self) -> None:
        reading = read_date("31-02-2019")
        assert reading.iso is None
        assert reading.impossible is True

    def test_only_one_valid_reading_is_taken_without_complaint(self) -> None:
        # 25 cannot be a month, so day-first is the only reading.
        reading = read_date("25-12-2020")
        assert reading.iso == "2020-12-25"

    def test_urdu_digits_are_read(self) -> None:
        assert read_date(eastern("14-03-2019")).iso == "2019-03-14"

    @pytest.mark.parametrize("printed", ["", "   ", "not a date", "Lahore"])
    def test_text_that_is_not_a_date(self, printed: str) -> None:
        assert read_date(printed).iso is None


class TestTimes:
    @pytest.mark.parametrize(
        ("printed", "expected"),
        [
            ("04:25", "04:25"),
            ("4:25", "04:25"),
            ("16:30", "16:30"),
            ("4:25 PM", "16:25"),
            ("4:25 p.m.", "16:25"),
            ("12:00 am", "00:00"),
            ("12:00 pm", "12:00"),
            ("Time: 09:05 hrs", "09:05"),
        ],
    )
    def test_a_printed_time_becomes_24_hour(self, printed: str, expected: str) -> None:
        assert read_time(printed) == expected

    def test_urdu_digits_are_read(self) -> None:
        assert read_time(eastern("04:25")) == "04:25"

    @pytest.mark.parametrize("printed", ["", "morning", "25:99", "99:99"])
    def test_text_that_is_not_a_time(self, printed: str) -> None:
        assert read_time(printed) is None


class TestSex:
    @pytest.mark.parametrize("printed", ["M", "m", "Male", "MALE", "boy", "مرد", "لڑکا"])
    def test_male(self, printed: str) -> None:
        assert read_sex(printed) == "M"

    @pytest.mark.parametrize("printed", ["F", "Female", "girl", "عورت", "لڑکی", "خاتون"])
    def test_female(self, printed: str) -> None:
        assert read_sex(printed) == "F"

    def test_a_word_inside_a_phrase_is_found(self) -> None:
        assert read_sex("Sex: Female") == "F"

    @pytest.mark.parametrize("printed", ["", "unknown", "not stated", "X"])
    def test_anything_else_is_not_guessed_at(self, printed: str) -> None:
        assert read_sex(printed) is None


class TestIdentityNumbers:
    @pytest.mark.parametrize(
        "printed",
        ["35201-1234567-1", "3520112345671", "35201 1234567 1", "CNIC 35201-1234567-1"],
    )
    def test_a_cnic_is_written_one_way(self, printed: str) -> None:
        assert format_cnic(printed) == "35201-1234567-1"

    def test_urdu_digits_are_read(self) -> None:
        assert format_cnic(eastern("3520112345671")) == "35201-1234567-1"

    @pytest.mark.parametrize("printed", ["123", "", "no number here", "35201-1234567"])
    def test_something_that_is_not_thirteen_digits(self, printed: str) -> None:
        assert format_cnic(printed) is None


class TestFieldValues:
    def test_a_date_field_is_normalised(self) -> None:
        value, reading = normalize_field_value("14-03-2019", FieldKind.DATE)
        assert value == "2019-03-14"
        assert reading is not None and reading.iso == "2019-03-14"

    def test_a_number_field_keeps_the_quantity(self) -> None:
        assert normalize_field_value("PKR 500,000", FieldKind.NUMBER)[0] == "500000"
        assert normalize_field_value("73 years", FieldKind.NUMBER)[0] == "73"

    def test_a_name_is_left_as_written(self) -> None:
        assert normalize_field_value("Ayesha Noor Malik", FieldKind.NAME)[0] == "Ayesha Noor Malik"

    def test_an_urdu_name_is_canonicalised_so_a_typed_correction_matches(self) -> None:
        # Arabic heh as some fonts hand it back, against the Urdu heh goal a person types.
        extracted = "عائش" + chr(0x0647)
        typed = "عائش" + chr(0x06C1)
        assert normalize_field_value(extracted, FieldKind.NAME)[0] == typed

    @pytest.mark.parametrize(
        ("kind", "printed"),
        [
            (FieldKind.DATE, "sometime in 2019"),
            (FieldKind.TIME, "early morning"),
            (FieldKind.SEX, "not recorded"),
            (FieldKind.ID_NUMBER, "applied for"),
            (FieldKind.NUMBER, "not known"),
        ],
    )
    def test_a_value_that_cannot_be_read_is_kept_as_printed(
        self, kind: FieldKind, printed: str
    ) -> None:
        # A reviewer can correct a value they can see, and nothing at all is worse.
        assert normalize_field_value(printed, kind)[0] == printed

    def test_an_empty_value_stays_empty(self) -> None:
        assert normalize_field_value("   ", FieldKind.DATE) == ("", None)
