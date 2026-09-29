"""What the schema builder refuses to save.

Each of these rules exists because the alternative is a failure much later and
much further from its cause: a CSV with two identical headers, a registry with no
way to tell one certificate from another, or a field marked searchable that
silently is not.
"""

from __future__ import annotations

import pytest

from certex.core.errors import ValidationFailedError
from certex.enums import FieldRole
from certex.fields import FieldKind
from certex.services.schema_service import (
    MAX_FIELDS_PER_SCHEMA,
    SchemaDraftField,
    validate_draft_fields,
)

pytestmark = pytest.mark.unit


def draft(name: str, role: FieldRole = FieldRole.NONE, **kwargs: bool) -> SchemaDraftField:
    return SchemaDraftField(
        name=name,
        label=name.replace("_", " ").title(),
        kind=FieldKind.TEXT,
        role=role,
        **kwargs,
    )


def identifier(name: str = "certificate_number") -> SchemaDraftField:
    return draft(name, FieldRole.IDENTIFIER)


def codes(error: ValidationFailedError) -> list[str | None]:
    return [item.code for item in (error.errors or [])]


def raised(fields: list[SchemaDraftField]) -> ValidationFailedError:
    with pytest.raises(ValidationFailedError) as caught:
        validate_draft_fields(fields)
    return caught.value


class TestAccepted:
    def test_the_smallest_usable_schema(self) -> None:
        validate_draft_fields([identifier()])

    def test_roles_may_repeat(self) -> None:
        """Two parties to a marriage is the normal case, not an error."""
        validate_draft_fields(
            [
                identifier(),
                draft("groom_name", FieldRole.PARTY_NAME, searchable=True),
                draft("bride_name", FieldRole.PARTY_NAME, searchable=True),
            ]
        )

    def test_fields_with_no_role_are_fine(self) -> None:
        validate_draft_fields([identifier(), draft("remarks")])


class TestIdentifier:
    def test_a_schema_without_one_is_refused(self) -> None:
        error = raised([draft("child_name", FieldRole.SUBJECT_NAME)])
        assert "missing_identifier" in codes(error)

    def test_two_identifiers_are_refused(self) -> None:
        error = raised([identifier("cert_no"), identifier("registration_no")])
        assert "multiple_identifiers" in codes(error)

    def test_the_message_names_both_offenders(self) -> None:
        """An operator has to know which two fields to choose between."""
        error = raised([identifier("cert_no"), identifier("register_no")])
        message = " ".join(item.message for item in error.errors or [])
        assert "cert_no" in message
        assert "register_no" in message


class TestFieldKeys:
    @pytest.mark.parametrize(
        "name",
        [
            "Certificate_Number",
            "1st_name",
            "name with spaces",
            "name-with-dashes",
            "trailing_",
            "_leading",
            "x",
            "naam\u06d2",
        ],
    )
    def test_unusable_keys_are_refused(self, name: str) -> None:
        error = raised([identifier(), draft(name)])
        assert "invalid_field_key" in codes(error)

    @pytest.mark.parametrize("name", ["child_name", "dob", "witness_2_name", "a1"])
    def test_usable_keys_are_accepted(self, name: str) -> None:
        validate_draft_fields([identifier(), draft(name)])

    def test_duplicates_are_refused(self) -> None:
        error = raised([identifier(), draft("village"), draft("village")])
        assert "duplicate_field_key" in codes(error)

    def test_the_duplicate_is_reported_against_the_second_one(self) -> None:
        error = raised([identifier(), draft("village"), draft("village")])
        duplicate = next(item for item in error.errors or [] if item.code == "duplicate_field_key")
        assert duplicate.field == "fields.2.name"
        assert "position 2" in duplicate.message, "should point at the field it collides with"


class TestOtherRules:
    def test_searchable_needs_a_role(self) -> None:
        """Search columns are role-mapped; without a role there is nothing to index."""
        error = raised([identifier(), draft("village", searchable=True)])
        assert "searchable_without_role" in codes(error)

    def test_a_blank_label_is_refused(self) -> None:
        error = raised(
            [
                identifier(),
                SchemaDraftField(name="village", label="   ", kind=FieldKind.TEXT),
            ]
        )
        assert any(item.field == "fields.1.label" for item in error.errors or [])

    def test_an_empty_schema_is_refused(self) -> None:
        with pytest.raises(ValidationFailedError, match="at least one field"):
            validate_draft_fields([])

    def test_too_many_fields_is_refused(self) -> None:
        fields = [identifier()] + [
            draft(f"field_{index}") for index in range(MAX_FIELDS_PER_SCHEMA)
        ]
        with pytest.raises(ValidationFailedError, match="at most"):
            validate_draft_fields(fields)


class TestReporting:
    def test_every_problem_comes_back_at_once(self) -> None:
        """One round trip per save, not one per mistake."""
        error = raised(
            [
                draft("Bad Key"),
                draft("village", searchable=True),
                draft("village"),
            ]
        )
        assert set(codes(error)) >= {
            "invalid_field_key",
            "searchable_without_role",
            "duplicate_field_key",
            "missing_identifier",
        }

    def test_paths_point_at_the_submitted_position(self) -> None:
        error = raised([identifier(), draft("ok_field"), draft("Bad Key")])
        assert [item.field for item in error.errors or []] == ["fields.2.name"]
