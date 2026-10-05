"""Certificate types and schema versions over HTTP.

Two properties carry most of the weight here. A published version is immutable,
because a certificate records the version it was read under and an edit would
silently change what old records mean. And every route is scoped to one
workspace, because a schema names the fields of another office's records.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from certex.core.security import hash_password
from certex.db.models import User, Workspace
from certex.enums import CertificateType, FieldRole, SchemaVersionStatus, UserRole
from tests.conftest import TEST_PASSWORD, authenticate

pytestmark = [pytest.mark.integration]

TYPES = "/api/v1/certificate-types"
SCHEMAS = "/api/v1/schemas"
VERSIONS = "/api/v1/schema-versions"


def field(name: str, role: FieldRole = FieldRole.NONE, **extra: Any) -> dict[str, Any]:
    return {
        "name": name,
        "label": name.replace("_", " ").title(),
        "kind": "text",
        "role": role.value,
        **extra,
    }


def identifier(name: str = "certificate_number") -> dict[str, Any]:
    return field(name, FieldRole.IDENTIFIER, kind="reference", required=True, unique=True)


async def seed_types(client: AsyncClient) -> list[dict[str, Any]]:
    """The first read of the navigation, which also seeds it."""
    response = await client.get(TYPES)
    assert response.status_code == 200, response.text
    body: list[dict[str, Any]] = response.json()
    return body


async def a_schema(client: AsyncClient, *, name: str = "Custom register") -> dict[str, Any]:
    types = await seed_types(client)
    response = await client.post(
        SCHEMAS, json={"certificate_type_id": types[0]["id"], "name": name}
    )
    assert response.status_code == 201, response.text
    created: dict[str, Any] = response.json()
    return created


async def add_version(
    client: AsyncClient,
    schema_id: str,
    fields: list[dict[str, Any]],
    *,
    publish: bool = True,
) -> dict[str, Any]:
    response = await client.post(
        f"{SCHEMAS}/{schema_id}/versions",
        json={"fields": fields, "publish": publish},
    )
    assert response.status_code == 201, response.text
    created: dict[str, Any] = response.json()
    return created


async def a_user(session: AsyncSession, workspace: Workspace, role: UserRole) -> User:
    """Another member of the same office, in the given role."""
    user = User(
        workspace_id=workspace.id,
        email=f"{role.value.lower()}-{uuid.uuid4().hex[:8]}@example.com",
        password_hash=hash_password(TEST_PASSWORD),
        role=role,
        is_active=True,
    )
    session.add(user)
    await session.flush()
    return user


async def other_workspace_admin(session: AsyncSession) -> User:
    """An administrator of a different office, for the tenancy checks."""
    workspace = Workspace(name=f"Other Office {uuid.uuid4().hex[:8]}", settings_json={})
    session.add(workspace)
    await session.flush()
    user = User(
        workspace_id=workspace.id,
        email=f"outsider-{uuid.uuid4().hex[:8]}@example.com",
        password_hash=hash_password(TEST_PASSWORD),
        role=UserRole.ADMIN,
        is_active=True,
    )
    session.add(user)
    await session.flush()
    return user


class TestNavigation:
    async def test_a_new_workspace_opens_onto_the_four_standard_categories(
        self, api_client: AsyncClient, admin_user: User
    ) -> None:
        """In this order: it is the order of the navigation, and "Other" is last
        because it is where a document goes when none of the three fit."""
        await authenticate(api_client, admin_user)
        types = await seed_types(api_client)
        assert [item["key"] for item in types] == ["BIRTH", "MARRIAGE", "DEATH", "OTHER"]

    async def test_types_carry_their_classifier_key(
        self, api_client: AsyncClient, admin_user: User
    ) -> None:
        """The link between a registry type and what the classifier calls it."""
        await authenticate(api_client, admin_user)
        types = await seed_types(api_client)
        assert {item["classifier_key"] for item in types} == {
            CertificateType.BIRTH.value,
            CertificateType.MARRIAGE.value,
            CertificateType.DEATH.value,
            CertificateType.OTHER.value,
        }

    async def test_seeding_happens_once(self, api_client: AsyncClient, admin_user: User) -> None:
        await authenticate(api_client, admin_user)
        first = await seed_types(api_client)
        second = await seed_types(api_client)
        assert [item["id"] for item in first] == [item["id"] for item in second]

    async def test_a_viewer_can_read_the_navigation(
        self, api_client: AsyncClient, viewer_user: User
    ) -> None:
        await authenticate(api_client, viewer_user)
        assert len(await seed_types(api_client)) == 4

    async def test_signing_in_is_required(self, api_client: AsyncClient) -> None:
        assert (await api_client.get(TYPES)).status_code == 401

    async def test_each_workspace_gets_its_own_types(
        self, api_client: AsyncClient, admin_user: User, db_session: AsyncSession
    ) -> None:
        outsider = await other_workspace_admin(db_session)

        await authenticate(api_client, admin_user)
        mine = {item["id"] for item in await seed_types(api_client)}

        await authenticate(api_client, outsider)
        theirs = {item["id"] for item in await seed_types(api_client)}

        assert mine.isdisjoint(theirs), "a type belongs to one office only"


class TestCreatingTypes:
    async def test_an_administrator_can_add_a_type(
        self, api_client: AsyncClient, admin_user: User
    ) -> None:
        await authenticate(api_client, admin_user)
        response = await api_client.post(
            TYPES, json={"key": "domicile", "name": "Domicile", "position": 4}
        )
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["key"] == "DOMICILE", "keys are normalised"
        assert body["classifier_key"] is None, "a custom type has no built-in classifier label"

    async def test_a_custom_type_joins_the_navigation(
        self, api_client: AsyncClient, admin_user: User
    ) -> None:
        """The proof that the three standard types are not special-cased."""
        await authenticate(api_client, admin_user)
        await seed_types(api_client)
        await api_client.post(TYPES, json={"key": "succession", "name": "Succession"})

        keys = [item["key"] for item in await seed_types(api_client)]
        assert "SUCCESSION" in keys

    @pytest.mark.parametrize("role", [UserRole.OPERATOR, UserRole.VIEWER])
    async def test_only_administrators_may_add_one(
        self,
        api_client: AsyncClient,
        db_session: AsyncSession,
        workspace: Workspace,
        role: UserRole,
    ) -> None:
        await authenticate(api_client, await a_user(db_session, workspace, role))
        response = await api_client.post(TYPES, json={"key": "domicile", "name": "Domicile"})
        assert response.status_code == 403

    async def test_a_duplicate_key_is_a_conflict(
        self, api_client: AsyncClient, admin_user: User
    ) -> None:
        await authenticate(api_client, admin_user)
        await api_client.post(TYPES, json={"key": "domicile", "name": "Domicile"})
        response = await api_client.post(TYPES, json={"key": "DOMICILE", "name": "Domicile again"})
        assert response.status_code == 409
        assert response.json()["remediation"]


class TestCreatingSchemas:
    async def test_an_administrator_can_create_one(
        self, api_client: AsyncClient, admin_user: User
    ) -> None:
        await authenticate(api_client, admin_user)
        schema = await a_schema(api_client)
        assert schema["latest_version"] is None, "a schema starts with no versions"

    @pytest.mark.parametrize("role", [UserRole.OPERATOR, UserRole.VIEWER])
    async def test_only_administrators_may_create_one(
        self,
        api_client: AsyncClient,
        admin_user: User,
        db_session: AsyncSession,
        workspace: Workspace,
        role: UserRole,
    ) -> None:
        """A schema decides how every record under it is read, so it is an admin's job."""
        await authenticate(api_client, admin_user)
        types = await seed_types(api_client)

        await authenticate(api_client, await a_user(db_session, workspace, role))
        response = await api_client.post(
            SCHEMAS, json={"certificate_type_id": types[0]["id"], "name": "Sneaky"}
        )
        assert response.status_code == 403

    async def test_an_unknown_type_is_not_found(
        self, api_client: AsyncClient, admin_user: User
    ) -> None:
        await authenticate(api_client, admin_user)
        response = await api_client.post(
            SCHEMAS, json={"certificate_type_id": str(uuid.uuid4()), "name": "Nowhere"}
        )
        assert response.status_code == 404

    async def test_another_workspaces_type_is_not_found(
        self, api_client: AsyncClient, admin_user: User, db_session: AsyncSession
    ) -> None:
        """Not 403: a type in another office should not be distinguishable from absent."""
        outsider = await other_workspace_admin(db_session)
        await authenticate(api_client, outsider)
        theirs = await seed_types(api_client)

        await authenticate(api_client, admin_user)
        response = await api_client.post(
            SCHEMAS, json={"certificate_type_id": theirs[0]["id"], "name": "Borrowed"}
        )
        assert response.status_code == 404

    async def test_a_repeated_name_is_a_conflict(
        self, api_client: AsyncClient, admin_user: User
    ) -> None:
        await authenticate(api_client, admin_user)
        await a_schema(api_client, name="Register A")
        types = await seed_types(api_client)
        response = await api_client.post(
            SCHEMAS, json={"certificate_type_id": types[0]["id"], "name": "Register A"}
        )
        assert response.status_code == 409

    async def test_the_seeded_schemas_are_listed_per_type(
        self, api_client: AsyncClient, admin_user: User
    ) -> None:
        await authenticate(api_client, admin_user)
        types = await seed_types(api_client)
        response = await api_client.get(SCHEMAS, params={"certificate_type_id": types[0]["id"]})
        assert response.status_code == 200
        listed = response.json()
        assert [item["name"] for item in listed] == ["Birth certificate"]
        assert listed[0]["is_default"] is True
        assert listed[0]["latest_version"] == 1, "the shipped schema is published at v1"


class TestVersions:
    async def test_a_version_records_the_fields_it_was_given(
        self, api_client: AsyncClient, admin_user: User
    ) -> None:
        await authenticate(api_client, admin_user)
        schema = await a_schema(api_client)
        body = await add_version(
            api_client,
            schema["id"],
            [
                identifier(),
                field("holder_name", FieldRole.SUBJECT_NAME, kind="name", searchable=True),
                field("remarks"),
            ],
        )
        assert body["version"] == 1
        assert body["status"] == SchemaVersionStatus.PUBLISHED
        assert [item["name"] for item in body["fields"]] == [
            "certificate_number",
            "holder_name",
            "remarks",
        ]

    async def test_versions_are_numbered_in_sequence(
        self, api_client: AsyncClient, admin_user: User
    ) -> None:
        await authenticate(api_client, admin_user)
        schema = await a_schema(api_client)
        numbers = [
            (await add_version(api_client, schema["id"], [identifier(), field(extra)]))["version"]
            for extra in ("village", "tehsil")
        ]
        assert numbers == [1, 2]

    async def test_a_new_version_leaves_the_old_one_untouched(
        self, api_client: AsyncClient, admin_user: User
    ) -> None:
        """The whole point of versioning: an old record keeps meaning what it meant."""
        await authenticate(api_client, admin_user)
        schema = await a_schema(api_client)

        first = await add_version(api_client, schema["id"], [identifier(), field("village")])
        await add_version(
            api_client, schema["id"], [identifier(), field("village"), field("tehsil")]
        )

        reread = await api_client.get(f"{VERSIONS}/{first['id']}")
        assert reread.status_code == 200
        assert [item["name"] for item in reread.json()["fields"]] == [
            "certificate_number",
            "village",
        ]

    async def test_a_version_can_be_saved_as_a_draft(
        self, api_client: AsyncClient, admin_user: User
    ) -> None:
        await authenticate(api_client, admin_user)
        schema = await a_schema(api_client)
        body = await add_version(api_client, schema["id"], [identifier()], publish=False)
        assert body["status"] == SchemaVersionStatus.DRAFT
        assert body["published_at"] is None

    async def test_publishing_a_draft_freezes_it(
        self, api_client: AsyncClient, admin_user: User
    ) -> None:
        await authenticate(api_client, admin_user)
        schema = await a_schema(api_client)
        draft = await add_version(api_client, schema["id"], [identifier()], publish=False)

        response = await api_client.post(f"{VERSIONS}/{draft['id']}/publish")
        assert response.status_code == 200, response.text
        assert response.json()["status"] == SchemaVersionStatus.PUBLISHED
        assert response.json()["published_at"] is not None

    async def test_publishing_twice_is_harmless(
        self, api_client: AsyncClient, admin_user: User
    ) -> None:
        await authenticate(api_client, admin_user)
        schema = await a_schema(api_client)
        version = await add_version(api_client, schema["id"], [identifier()])
        response = await api_client.post(f"{VERSIONS}/{version['id']}/publish")
        assert response.status_code == 200
        assert response.json()["version"] == version["version"]

    async def test_roles_survive_the_round_trip(
        self, api_client: AsyncClient, admin_user: User
    ) -> None:
        """Roles are what generic validation reads; losing them loses the rules."""
        await authenticate(api_client, admin_user)
        schema = await a_schema(api_client)
        created = await add_version(
            api_client,
            schema["id"],
            [
                identifier(),
                field("groom_dob", FieldRole.BIRTH_DATE, kind="date", searchable=True),
                field("bride_dob", FieldRole.BIRTH_DATE, kind="date", searchable=True),
            ],
        )

        fields = (await api_client.get(f"{VERSIONS}/{created['id']}")).json()["fields"]
        births = [item["name"] for item in fields if item["role"] == FieldRole.BIRTH_DATE.value]
        assert births == ["groom_dob", "bride_dob"], "a role may belong to more than one field"

    async def test_synonyms_are_kept_for_the_rules_engine(
        self, api_client: AsyncClient, admin_user: User
    ) -> None:
        await authenticate(api_client, admin_user)
        schema = await a_schema(api_client)
        created = await add_version(
            api_client,
            schema["id"],
            [
                identifier(),
                field(
                    "holder_name",
                    FieldRole.SUBJECT_NAME,
                    labels_en=["name of holder", "holder"],
                    labels_ur=["نام"],
                ),
            ],
        )

        fields = (await api_client.get(f"{VERSIONS}/{created['id']}")).json()["fields"]
        holder = next(item for item in fields if item["name"] == "holder_name")
        assert holder["labels_en"] == ["name of holder", "holder"]
        assert holder["labels_ur"] == ["نام"]

    @pytest.mark.parametrize("role", [UserRole.OPERATOR, UserRole.VIEWER])
    async def test_only_administrators_may_add_a_version(
        self,
        api_client: AsyncClient,
        admin_user: User,
        db_session: AsyncSession,
        workspace: Workspace,
        role: UserRole,
    ) -> None:
        await authenticate(api_client, admin_user)
        schema = await a_schema(api_client)

        await authenticate(api_client, await a_user(db_session, workspace, role))
        response = await api_client.post(
            f"{SCHEMAS}/{schema['id']}/versions", json={"fields": [identifier()]}
        )
        assert response.status_code == 403

    async def test_listing_versions_is_newest_first(
        self, api_client: AsyncClient, admin_user: User
    ) -> None:
        await authenticate(api_client, admin_user)
        schema = await a_schema(api_client)
        for extra in ("a_field", "b_field"):
            await add_version(api_client, schema["id"], [identifier(), field(extra)])
        response = await api_client.get(f"{SCHEMAS}/{schema['id']}/versions")
        assert [item["version"] for item in response.json()] == [2, 1]


class TestRejectedDrafts:
    async def test_a_schema_with_no_identifier_is_refused(
        self, api_client: AsyncClient, admin_user: User
    ) -> None:
        await authenticate(api_client, admin_user)
        schema = await a_schema(api_client)
        response = await api_client.post(
            f"{SCHEMAS}/{schema['id']}/versions",
            json={"fields": [field("holder_name", FieldRole.SUBJECT_NAME)]},
        )
        assert response.status_code == 422
        assert "missing_identifier" in {item["code"] for item in response.json()["errors"]}

    async def test_an_unusable_field_key_is_refused(
        self, api_client: AsyncClient, admin_user: User
    ) -> None:
        await authenticate(api_client, admin_user)
        schema = await a_schema(api_client)
        response = await api_client.post(
            f"{SCHEMAS}/{schema['id']}/versions",
            json={"fields": [identifier(), field("Bad Key")]},
        )
        assert response.status_code == 422

    async def test_nothing_is_written_when_a_draft_is_refused(
        self, api_client: AsyncClient, admin_user: User
    ) -> None:
        await authenticate(api_client, admin_user)
        schema = await a_schema(api_client)
        await api_client.post(
            f"{SCHEMAS}/{schema['id']}/versions",
            json={"fields": [field("holder_name", FieldRole.SUBJECT_NAME)]},
        )
        listed = await api_client.get(f"{SCHEMAS}/{schema['id']}/versions")
        assert listed.status_code == 404, "a refused draft must not leave a version behind"


class TestTenancy:
    async def test_another_workspaces_version_is_not_readable(
        self, api_client: AsyncClient, admin_user: User, db_session: AsyncSession
    ) -> None:
        outsider = await other_workspace_admin(db_session)
        await authenticate(api_client, outsider)
        their_schema = await a_schema(api_client, name="Their register")
        their_version = await add_version(api_client, their_schema["id"], [identifier()])

        await authenticate(api_client, admin_user)
        assert (await api_client.get(f"{VERSIONS}/{their_version['id']}")).status_code == 404
        assert (await api_client.get(f"{SCHEMAS}/{their_schema['id']}/versions")).status_code == 404

    async def test_another_workspaces_version_cannot_be_published(
        self, api_client: AsyncClient, admin_user: User, db_session: AsyncSession
    ) -> None:
        outsider = await other_workspace_admin(db_session)
        await authenticate(api_client, outsider)
        their_schema = await a_schema(api_client, name="Their register")
        their_draft = await add_version(
            api_client, their_schema["id"], [identifier()], publish=False
        )

        await authenticate(api_client, admin_user)
        response = await api_client.post(f"{VERSIONS}/{their_draft['id']}/publish")
        assert response.status_code == 404

    async def test_another_workspaces_schemas_are_not_listed(
        self, api_client: AsyncClient, admin_user: User, db_session: AsyncSession
    ) -> None:
        outsider = await other_workspace_admin(db_session)
        await authenticate(api_client, outsider)
        their_schema = await a_schema(api_client, name="Their register")

        await authenticate(api_client, admin_user)
        listed = (await api_client.get(SCHEMAS)).json()
        assert their_schema["id"] not in {item["id"] for item in listed}
