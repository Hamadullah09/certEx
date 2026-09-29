"""Reading a row into a register entry.

The interesting cases are the ones where role and field name come apart: a
marriage certificate with two parties and two dates of birth, a death certificate
that prints a date of birth as well as a date of death, and a schema whose fields
this code has never seen. If the draft gets those right, every screen, search and
duplicate check downstream gets them right too.
"""

from __future__ import annotations

import datetime as dt

import pytest

from certex.certificates import build_draft
from certex.enums import CertificateType, FieldRole
from certex.fields import FieldKind, FieldSchema, FieldSpec, builtin_schema

pytestmark = pytest.mark.unit


def spec(
    name: str,
    role: FieldRole = FieldRole.NONE,
    kind: FieldKind = FieldKind.TEXT,
) -> FieldSpec:
    return FieldSpec(name=name, label=name.replace("_", " ").title(), kind=kind, role=role)


def custom_schema() -> FieldSchema:
    """A domicile certificate, which this codebase knows nothing about."""
    return FieldSchema(
        fields=(
            spec("domicile_no", FieldRole.IDENTIFIER, FieldKind.REFERENCE),
            spec("applicant", FieldRole.SUBJECT_NAME, FieldKind.NAME),
            spec("guardian", FieldRole.FATHER_NAME, FieldKind.NAME),
            spec("born_on", FieldRole.BIRTH_DATE, FieldKind.DATE),
            spec("issued_on", FieldRole.ISSUE_DATE, FieldKind.DATE),
            spec("district", FieldRole.PLACE),
            spec("remarks"),
        ),
        certificate_type=CertificateType.OTHER,
        name="Domicile",
    )


class TestIdentifier:
    def test_the_number_is_taken_from_the_identifier_role(self) -> None:
        draft = build_draft(custom_schema(), {"domicile_no": "DOM/2019/17"})
        assert draft.certificate_number == "DOM/2019/17"
        assert draft.certificate_number_key == "dom201917"
        assert draft.has_identifier

    def test_a_row_without_one_says_so(self) -> None:
        draft = build_draft(custom_schema(), {"applicant": "Ahmed Ali"})
        assert not draft.has_identifier
        assert draft.certificate_number == ""

    def test_the_registration_number_is_kept_separately(self) -> None:
        draft = build_draft(
            builtin_schema(CertificateType.BIRTH),
            {"certificate_number": "BC-1", "registration_number": "REG/2019/9"},
        )
        assert draft.registration_number == "REG/2019/9"
        assert draft.registration_number_key == "reg20199"


class TestNames:
    def test_the_subject_is_who_the_entry_is_filed_under(self) -> None:
        draft = build_draft(
            builtin_schema(CertificateType.BIRTH),
            {
                "certificate_number": "BC-1",
                "child_full_name": "Ayesha Noor Malik",
                "father_full_name": "Tariq Mahmood Malik",
            },
        )
        assert draft.primary_name == "Ayesha Noor Malik"
        assert draft.primary_name_key == "ayesha noor malik"
        assert draft.secondary_name is None

    def test_a_parent_is_recorded_but_not_filed_under(self) -> None:
        draft = build_draft(
            builtin_schema(CertificateType.BIRTH),
            {
                "certificate_number": "BC-1",
                "child_full_name": "Ayesha Noor",
                "father_full_name": "Tariq Mahmood",
                "mother_full_name": "Nasreen Akhtar",
            },
        )
        fathers = draft.names_for(FieldRole.FATHER_NAME)
        assert [entry.value for entry in fathers] == ["Tariq Mahmood"]
        assert draft.primary_name == "Ayesha Noor"

    def test_both_parties_to_a_marriage_are_kept(self) -> None:
        """A register that lost the second spouse would be searchable by one only."""
        schema = builtin_schema(CertificateType.MARRIAGE)
        party_fields = schema.names_for(FieldRole.PARTY_NAME)
        values = {
            "certificate_number": "NK-1",
            party_fields[0]: "Bilal Hussain",
            party_fields[1]: "Sana Bilal",
        }
        draft = build_draft(schema, values)
        assert draft.primary_name == "Bilal Hussain"
        assert draft.secondary_name == "Sana Bilal"
        assert len(draft.names_for(FieldRole.PARTY_NAME)) == 2

    def test_positions_number_the_occurrences_of_a_role(self) -> None:
        schema = builtin_schema(CertificateType.MARRIAGE)
        party_fields = schema.names_for(FieldRole.PARTY_NAME)
        draft = build_draft(
            schema,
            {
                "certificate_number": "NK-1",
                party_fields[0]: "Bilal Hussain",
                party_fields[1]: "Sana Bilal",
            },
        )
        assert [entry.position for entry in draft.names_for(FieldRole.PARTY_NAME)] == [0, 1]

    def test_a_blank_name_is_not_recorded(self) -> None:
        draft = build_draft(
            builtin_schema(CertificateType.BIRTH),
            {"certificate_number": "BC-1", "child_full_name": "   ", "father_full_name": "-"},
        )
        assert draft.primary_name is None
        assert draft.names == ()


class TestDates:
    def test_dates_are_parsed_into_real_dates(self) -> None:
        draft = build_draft(
            builtin_schema(CertificateType.BIRTH),
            {"certificate_number": "BC-1", "date_of_birth": "2019-04-03"},
        )
        births = draft.dates_for(FieldRole.BIRTH_DATE)
        assert births[0].value == dt.date(2019, 4, 3)

    def test_the_event_date_of_a_birth_is_the_birth(self) -> None:
        draft = build_draft(
            builtin_schema(CertificateType.BIRTH),
            {
                "certificate_number": "BC-1",
                "date_of_birth": "2019-04-03",
                "registration_date": "2019-05-01",
            },
        )
        assert draft.event_date == dt.date(2019, 4, 3)
        assert draft.event_date_role is FieldRole.BIRTH_DATE
        assert draft.registration_date == dt.date(2019, 5, 1)

    def test_the_event_date_of_a_death_is_the_death_not_the_birth(self) -> None:
        """A death certificate prints both; the entry is found by the death."""
        schema = builtin_schema(CertificateType.DEATH)
        draft = build_draft(
            schema,
            {
                "certificate_number": "DC-1",
                schema.names_for(FieldRole.BIRTH_DATE)[0]: "1946-01-09",
                schema.names_for(FieldRole.DEATH_DATE)[0]: "2019-02-14",
            },
        )
        assert draft.event_date == dt.date(2019, 2, 14)
        assert draft.event_date_role is FieldRole.DEATH_DATE

    def test_a_marriage_keeps_both_dates_of_birth(self) -> None:
        schema = builtin_schema(CertificateType.MARRIAGE)
        birth_fields = schema.names_for(FieldRole.BIRTH_DATE)
        draft = build_draft(
            schema,
            {
                "certificate_number": "NK-1",
                birth_fields[0]: "1992-03-11",
                birth_fields[1]: "1995-07-22",
                schema.names_for(FieldRole.MARRIAGE_DATE)[0]: "2018-12-02",
            },
        )
        assert len(draft.dates_for(FieldRole.BIRTH_DATE)) == 2
        assert draft.event_date == dt.date(2018, 12, 2)
        assert draft.event_date_role is FieldRole.MARRIAGE_DATE

    def test_an_ambiguous_date_is_read_and_flagged(self) -> None:
        """03/04/2019 is a real date read either way round; the doubt is kept."""
        draft = build_draft(
            builtin_schema(CertificateType.BIRTH),
            {"certificate_number": "BC-1", "date_of_birth": "03/04/2019"},
        )
        assert draft.event_date is not None
        assert "date_of_birth" in draft.ambiguous_dates

    def test_an_unreadable_date_is_left_out_of_the_columns(self) -> None:
        draft = build_draft(
            builtin_schema(CertificateType.BIRTH),
            {"certificate_number": "BC-1", "date_of_birth": "not a date"},
        )
        assert draft.event_date is None
        assert draft.dates == ()

    def test_the_unreadable_value_is_still_kept(self) -> None:
        """The reviewer has to see what was printed to correct it."""
        draft = build_draft(
            builtin_schema(CertificateType.BIRTH),
            {"certificate_number": "BC-1", "date_of_birth": "not a date"},
        )
        assert draft.values["date_of_birth"] == "not a date"


class TestValues:
    def test_only_fields_the_schema_defines_are_kept(self) -> None:
        draft = build_draft(
            custom_schema(),
            {"domicile_no": "DOM-1", "district": "Lahore", "smuggled_in": "value"},
        )
        assert "smuggled_in" not in draft.values
        assert draft.values["district"] == "Lahore"

    def test_blank_values_are_dropped(self) -> None:
        draft = build_draft(custom_schema(), {"domicile_no": "DOM-1", "remarks": "  "})
        assert "remarks" not in draft.values

    def test_values_are_not_re_normalised(self) -> None:
        """Whatever produced them already normalised them, and a reviewer approved it."""
        draft = build_draft(custom_schema(), {"domicile_no": "DOM-1", "applicant": "Ahmed  Ali"})
        assert draft.values["applicant"] == "Ahmed  Ali"


class TestUnknownSchema:
    def test_a_type_this_code_never_heard_of_still_files_correctly(self) -> None:
        draft = build_draft(
            custom_schema(),
            {
                "domicile_no": "DOM/2019/17",
                "applicant": "Mr Ahmed Ali",
                "guardian": "Tariq Mahmood",
                "born_on": "1992-03-11",
                "issued_on": "2019-06-04",
                "district": "Lahore",
            },
        )
        assert draft.certificate_number_key == "dom201917"
        assert draft.primary_name_key == "ahmed ali", "the honorific is dropped from the key"
        assert draft.primary_name == "Mr Ahmed Ali", "but not from what was printed"
        assert draft.event_date == dt.date(1992, 3, 11), "its only event date is the birth"
        assert draft.issue_date == dt.date(2019, 6, 4)
        assert [entry.field_name for entry in draft.names] == ["applicant", "guardian"]

    def test_a_schema_with_no_roles_at_all_yields_no_keys(self) -> None:
        """Roles are what make an entry findable; without them there is nothing to file."""
        bare = FieldSchema(fields=(spec("a"), spec("b")))
        draft = build_draft(bare, {"a": "1", "b": "2"})
        assert not draft.has_identifier
        assert draft.names == ()
        assert draft.event_date is None
        assert draft.values == {"a": "1", "b": "2"}
