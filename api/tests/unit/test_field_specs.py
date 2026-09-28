"""The field schema.

This is the one place a field is declared, and four other things derive from it: the CSV
columns, the review grid, the validation rules and the generated TypeScript types. So the
tests here are invariants rather than examples - a duplicate name or a field with no label
would break an export nobody is looking at yet.
"""

from __future__ import annotations

import pytest

from certex.enums import CertificateType
from certex.fields import (
    FIELD_SCHEMA_VERSION,
    FieldKind,
    common_fields,
    field_names_for,
    fields_for,
    spec_for,
)
from certex.pipeline.text.normalize import contains_arabic_script
from tests.fixtures.builders import CERTIFICATE_SAMPLES
from tests.fixtures.corpus import URDU_BIRTH

pytestmark = pytest.mark.unit

TYPES = (CertificateType.BIRTH, CertificateType.DEATH, CertificateType.MARRIAGE)


class TestShape:
    @pytest.mark.parametrize("certificate_type", list(CertificateType))
    def test_field_names_are_unique(self, certificate_type: CertificateType) -> None:
        names = field_names_for(certificate_type)
        assert len(names) == len(set(names))

    @pytest.mark.parametrize("certificate_type", list(CertificateType))
    def test_common_fields_come_first(self, certificate_type: CertificateType) -> None:
        # Export order starts with the columns every row has, whatever its type.
        names = field_names_for(certificate_type)
        assert names[: len(common_fields())] == tuple(spec.name for spec in common_fields())

    @pytest.mark.parametrize("certificate_type", list(CertificateType))
    def test_every_field_has_a_label_and_a_machine_name(
        self, certificate_type: CertificateType
    ) -> None:
        for spec in fields_for(certificate_type):
            assert spec.name == spec.name.lower()
            assert " " not in spec.name
            assert spec.label.strip()

    def test_an_unknown_type_still_has_the_common_fields(self) -> None:
        # A certificate the classifier could not place still has a number and an issuer.
        assert field_names_for(CertificateType.OTHER) == tuple(
            spec.name for spec in common_fields()
        )

    def test_the_schema_is_versioned(self) -> None:
        assert FIELD_SCHEMA_VERSION.count(".") == 1


class TestLabels:
    @pytest.mark.parametrize("certificate_type", TYPES)
    def test_every_field_can_be_found_in_english(self, certificate_type: CertificateType) -> None:
        for spec in fields_for(certificate_type):
            english = [item for item in spec.synonyms if not contains_arabic_script(item)]
            assert english, f"{spec.name} has no English label to match"

    @pytest.mark.parametrize("certificate_type", TYPES)
    def test_the_fields_a_certificate_turns_on_can_be_found_in_urdu(
        self, certificate_type: CertificateType
    ) -> None:
        # Not every field is printed in Urdu, but the ones that identify a person are.
        identifying = {"child_full_name", "deceased_full_name", "groom_full_name", "date_of_birth"}
        for spec in fields_for(certificate_type):
            if spec.name not in identifying:
                continue
            assert any(contains_arabic_script(item) for item in spec.synonyms), spec.name

    @pytest.mark.parametrize("certificate_type", TYPES)
    def test_no_label_is_blank(self, certificate_type: CertificateType) -> None:
        for spec in fields_for(certificate_type):
            assert all(item.strip() for item in spec.synonyms)


class TestKinds:
    @pytest.mark.parametrize(
        ("certificate_type", "name", "kind"),
        [
            (CertificateType.BIRTH, "date_of_birth", FieldKind.DATE),
            (CertificateType.BIRTH, "time_of_birth", FieldKind.TIME),
            (CertificateType.BIRTH, "sex", FieldKind.SEX),
            (CertificateType.BIRTH, "father_id_number", FieldKind.ID_NUMBER),
            (CertificateType.BIRTH, "certificate_number", FieldKind.REFERENCE),
            (CertificateType.DEATH, "age_at_death", FieldKind.NUMBER),
            (CertificateType.MARRIAGE, "dower_amount", FieldKind.NUMBER),
            (CertificateType.MARRIAGE, "groom_full_name", FieldKind.NAME),
        ],
    )
    def test_a_field_declares_what_sort_of_value_it_holds(
        self, certificate_type: CertificateType, name: str, kind: FieldKind
    ) -> None:
        spec = spec_for(certificate_type, name)
        assert spec is not None and spec.kind is kind

    def test_an_unknown_field_is_not_invented(self) -> None:
        assert spec_for(CertificateType.BIRTH, "blood_group") is None


class TestRequiredFields:
    @pytest.mark.parametrize("certificate_type", TYPES)
    def test_a_certificate_requires_what_it_is_useless_without(
        self, certificate_type: CertificateType
    ) -> None:
        required = {spec.name for spec in fields_for(certificate_type) if spec.required}
        assert "certificate_number" in required
        assert required, "a type with no required field can never be incomplete"

    @pytest.mark.parametrize("certificate_type", TYPES)
    def test_required_fields_are_few(self, certificate_type: CertificateType) -> None:
        # A flag on every row is a flag on none.
        specs = fields_for(certificate_type)
        required = [spec for spec in specs if spec.required]
        assert len(required) <= len(specs) // 3

    def test_each_type_requires_its_own_event(self) -> None:
        assert spec_for(CertificateType.BIRTH, "date_of_birth").required  # type: ignore[union-attr]
        assert spec_for(CertificateType.DEATH, "date_of_death").required  # type: ignore[union-attr]
        assert spec_for(CertificateType.MARRIAGE, "date_of_marriage").required  # type: ignore[union-attr]


class TestAgainstTheFixtures:
    """The hand-labelled fixtures are the contract: every value they expect has a field."""

    @pytest.mark.parametrize("sample", CERTIFICATE_SAMPLES, ids=lambda item: item.key)
    def test_every_expected_value_has_a_field_to_land_in(self, sample: object) -> None:
        certificate_type = CertificateType(sample.certificate_type)
        known = set(field_names_for(certificate_type))
        unknown = set(sample.expected) - known
        assert not unknown, f"{sorted(unknown)} have no field in the schema"

    def test_the_urdu_fixture_lands_in_the_birth_schema(self) -> None:
        unknown = set(URDU_BIRTH.expected) - set(field_names_for(CertificateType.BIRTH))
        assert not unknown
