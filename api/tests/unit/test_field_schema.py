"""The schema abstraction and its roles.

Roles are what let one implementation serve every certificate type, including
types this code has never heard of. The cases that matter are the ones where a
role appears more than once - a marriage certificate has two parties and two
dates of birth - because a lookup that quietly returned only the first would skip
half the checks and nobody would see it happen.
"""

from __future__ import annotations

import pytest

from certex.enums import CertificateType, FieldRole
from certex.fields import (
    FieldKind,
    FieldSchema,
    FieldSpec,
    builtin_schema,
    schema_or_builtin,
)

pytestmark = pytest.mark.unit


def spec(
    name: str,
    role: FieldRole = FieldRole.NONE,
    *,
    kind: FieldKind = FieldKind.TEXT,
    required: bool = False,
    searchable: bool = False,
    unique: bool = False,
) -> FieldSpec:
    return FieldSpec(
        name=name,
        label=name.replace("_", " ").title(),
        kind=kind,
        role=role,
        required=required,
        searchable=searchable,
        unique=unique,
    )


class TestLookups:
    def test_names_preserve_order(self) -> None:
        schema = FieldSchema(fields=(spec("b"), spec("a"), spec("c")))
        assert schema.names == ("b", "a", "c")

    def test_by_name(self) -> None:
        schema = FieldSchema(fields=(spec("child_name"),))
        assert schema.get("child_name") is not None
        assert schema.get("absent") is None
        assert "child_name" in schema
        assert "absent" not in schema

    def test_length_and_iteration(self) -> None:
        schema = FieldSchema(fields=(spec("a"), spec("b")))
        assert len(schema) == 2
        assert [item.name for item in schema] == ["a", "b"]


class TestRoles:
    def test_a_role_can_appear_more_than_once(self) -> None:
        """A marriage certificate has two parties; both must come back."""
        schema = FieldSchema(
            fields=(
                spec("groom_name", FieldRole.PARTY_NAME),
                spec("bride_name", FieldRole.PARTY_NAME),
            )
        )
        assert schema.names_for(FieldRole.PARTY_NAME) == ("groom_name", "bride_name")

    def test_first_returns_one(self) -> None:
        schema = FieldSchema(
            fields=(spec("a", FieldRole.BIRTH_DATE), spec("b", FieldRole.BIRTH_DATE))
        )
        first = schema.first(FieldRole.BIRTH_DATE)
        assert first is not None
        assert first.name == "a"

    def test_first_is_none_when_absent(self) -> None:
        assert FieldSchema(fields=(spec("a"),)).first(FieldRole.DEATH_DATE) is None

    def test_several_roles_at_once_keeps_schema_order(self) -> None:
        schema = FieldSchema(
            fields=(
                spec("subject", FieldRole.SUBJECT_NAME),
                spec("unrelated"),
                spec("party", FieldRole.PARTY_NAME),
            )
        )
        assert schema.names_for(FieldRole.PARTY_NAME, FieldRole.SUBJECT_NAME) == (
            "subject",
            "party",
        )

    def test_identifier(self) -> None:
        schema = FieldSchema(
            fields=(spec("number", FieldRole.IDENTIFIER), spec("name", FieldRole.SUBJECT_NAME))
        )
        assert schema.identifier is not None
        assert schema.identifier.name == "number"

    def test_no_identifier_is_allowed_at_this_layer(self) -> None:
        """The schema object describes; the service layer is what insists on one."""
        assert FieldSchema(fields=(spec("name"),)).identifier is None

    def test_roleless_fields_take_no_part(self) -> None:
        schema = FieldSchema(fields=(spec("blood_group"), spec("dob", FieldRole.BIRTH_DATE)))
        assert schema.names_for(FieldRole.BIRTH_DATE) == ("dob",)
        assert "blood_group" in schema


class TestDerivedViews:
    def test_required_names(self) -> None:
        schema = FieldSchema(fields=(spec("a", required=True), spec("b")))
        assert schema.required_names == ("a",)

    def test_searchable(self) -> None:
        schema = FieldSchema(fields=(spec("a", FieldRole.SUBJECT_NAME, searchable=True), spec("b")))
        assert [item.name for item in schema.searchable] == ["a"]

    def test_subset_keeps_order_and_roles(self) -> None:
        schema = FieldSchema(
            fields=(
                spec("number", FieldRole.IDENTIFIER),
                spec("name", FieldRole.SUBJECT_NAME),
                spec("notes"),
            ),
            certificate_type=CertificateType.BIRTH,
        )
        narrowed = schema.subset(["notes", "number"])
        assert narrowed.names == ("number", "notes")
        assert narrowed.identifier is not None
        assert narrowed.certificate_type is CertificateType.BIRTH


class TestBuiltins:
    @pytest.mark.parametrize(
        "certificate_type",
        [CertificateType.BIRTH, CertificateType.DEATH, CertificateType.MARRIAGE],
    )
    def test_every_builtin_declares_an_identifier(self, certificate_type: CertificateType) -> None:
        """Without one, nothing can be indexed or duplicate-checked."""
        schema = builtin_schema(certificate_type)
        assert schema.identifier is not None
        assert schema.identifier.name == "certificate_number"
        assert schema.identifier.required

    def test_marriage_carries_two_parties_and_two_birth_dates(self) -> None:
        schema = builtin_schema(CertificateType.MARRIAGE)
        assert len(schema.names_for(FieldRole.PARTY_NAME)) == 2
        assert len(schema.names_for(FieldRole.BIRTH_DATE)) == 2

    def test_death_has_the_roles_its_rules_need(self) -> None:
        schema = builtin_schema(CertificateType.DEATH)
        assert schema.names_for(FieldRole.DEATH_DATE)
        assert schema.names_for(FieldRole.BIRTH_DATE)
        assert schema.names_for(FieldRole.AGE)

    def test_unknown_type_still_gets_the_common_fields(self) -> None:
        schema = builtin_schema(CertificateType.OTHER)
        assert schema.identifier is not None

    @pytest.mark.parametrize(
        "certificate_type",
        [CertificateType.BIRTH, CertificateType.DEATH, CertificateType.MARRIAGE],
    )
    def test_searchable_fields_all_carry_a_role(self, certificate_type: CertificateType) -> None:
        """Search columns are role-mapped, so a searchable field needs a role."""
        for field in builtin_schema(certificate_type).searchable:
            assert field.role is not FieldRole.NONE, field.name


class TestResolver:
    def test_explicit_schema_wins(self) -> None:
        explicit = FieldSchema(fields=(spec("only_field"),))
        assert schema_or_builtin(CertificateType.BIRTH, explicit) is explicit

    def test_falls_back_to_the_builtin(self) -> None:
        resolved = schema_or_builtin(CertificateType.DEATH, None)
        assert resolved.certificate_type is CertificateType.DEATH
        assert "deceased_full_name" in resolved
