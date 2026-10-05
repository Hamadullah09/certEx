"""The columns a batch is created with, and what they govern.

This is the rule the whole feature rests on: whatever columns are defined when a batch
is created are the columns every document uploaded into it is read for - the first one
and the millionth, with nobody configuring anything again. So the tests here are less
about the HTTP shapes than about *persistence and precedence*: that the columns are
pinned rather than looked up, that editing the schema afterwards cannot reach back into
a batch that already used it, and that two batches with different columns do not
interfere.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from certex.db.models import Batch, User
from tests.conftest import authenticate

pytestmark = [pytest.mark.integration]

BATCHES = "/api/v1/batches"
TYPES = "/api/v1/certificate-types"

# Named the way an office would name them, not the way the built-in schema does. The
# identifier role is the one thing the register insists on - it is keyed by certificate
# number - but *which* column carries it is the office's choice, and it can be called
# whatever their forms call it.
BIRTH_COLUMNS: list[dict[str, Any]] = [
    {
        "name": "certificate_no",
        "label": "Certificate No",
        "kind": "reference",
        "role": "identifier",
        "required": True,
    },
    {"name": "child_name", "label": "Name", "kind": "name"},
    {"name": "dob", "label": "DOB", "kind": "date"},
    {"name": "father_name", "label": "Father Name", "kind": "name"},
]


async def type_id(client: AsyncClient, key: str = "BIRTH") -> str:
    response = await client.get(TYPES)
    assert response.status_code == 200, response.text
    return next(item["id"] for item in response.json() if item["key"] == key)


async def create_batch(
    client: AsyncClient,
    *,
    name: str = "Birth Certificates 2020",
    expect: int = 201,
    **extra: Any,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"name": name, **extra}
    response = await client.post(BATCHES, json=payload)
    assert response.status_code == expect, response.text
    body: dict[str, Any] = response.json()
    return body


class TestDefiningColumns:
    async def test_the_columns_given_become_the_batch_columns_in_order(
        self, api_client: AsyncClient, operator_user: User
    ) -> None:
        await authenticate(api_client, operator_user)
        batch = await create_batch(
            api_client,
            certificate_type_id=await type_id(api_client),
            fields=BIRTH_COLUMNS,
        )

        assert [column["name"] for column in batch["columns"]] == [
            "certificate_no",
            "child_name",
            "dob",
            "father_name",
        ]
        assert [column["label"] for column in batch["columns"]] == [
            "Certificate No",
            "Name",
            "DOB",
            "Father Name",
        ]
        assert [column["position"] for column in batch["columns"]] == [0, 1, 2, 3]

    async def test_the_columns_are_still_there_when_the_batch_is_read_back(
        self, api_client: AsyncClient, operator_user: User
    ) -> None:
        """Pinned, not remembered by the request that created it."""
        await authenticate(api_client, operator_user)
        batch = await create_batch(
            api_client, certificate_type_id=await type_id(api_client), fields=BIRTH_COLUMNS
        )

        reread = await api_client.get(f"{BATCHES}/{batch['id']}")
        assert reread.status_code == 200, reread.text
        assert [column["name"] for column in reread.json()["columns"]] == [
            "certificate_no",
            "child_name",
            "dob",
            "father_name",
        ]

    async def test_a_schema_version_is_pinned_onto_the_batch(
        self, api_client: AsyncClient, operator_user: User, db_session: AsyncSession
    ) -> None:
        """The extractor reads the pinned version, so it has to actually be set.

        Without this the batch would fall back to the built-in fields for its type and
        the configured columns would be silently ignored by every upload.
        """
        await authenticate(api_client, operator_user)
        batch = await create_batch(
            api_client, certificate_type_id=await type_id(api_client), fields=BIRTH_COLUMNS
        )

        row = await db_session.scalar(select(Batch).where(Batch.id == uuid.UUID(batch["id"])))
        assert row is not None
        assert row.schema_version_id is not None
        assert row.certificate_type_id is not None

    async def test_two_batches_can_have_completely_different_columns(
        self, api_client: AsyncClient, operator_user: User
    ) -> None:
        """A birth register and a marriage register share nothing but a certificate
        number, and both have to work at once."""
        await authenticate(api_client, operator_user)
        birth = await create_batch(
            api_client,
            name="Birth Certificates 2020",
            certificate_type_id=await type_id(api_client, "BIRTH"),
            fields=BIRTH_COLUMNS,
        )
        marriage = await create_batch(
            api_client,
            name="Marriage Certificates 2020",
            certificate_type_id=await type_id(api_client, "MARRIAGE"),
            fields=[
                {
                    "name": "certificate_no",
                    "label": "Certificate No",
                    "kind": "reference",
                    "role": "identifier",
                },
                {"name": "husband_name", "label": "Husband Name", "kind": "name"},
                {"name": "wife_name", "label": "Wife Name", "kind": "name"},
                {"name": "marriage_date", "label": "Marriage Date", "kind": "date"},
            ],
        )

        assert [column["name"] for column in birth["columns"]] != [
            column["name"] for column in marriage["columns"]
        ]
        assert "husband_name" in {column["name"] for column in marriage["columns"]}
        assert "husband_name" not in {column["name"] for column in birth["columns"]}

    async def test_two_batches_may_share_a_name(
        self, api_client: AsyncClient, operator_user: User
    ) -> None:
        """Schemas are named after their batch and names are unique per type, so two
        batches called the same thing must not collide on the way in."""
        await authenticate(api_client, operator_user)
        birth = await type_id(api_client)
        first = await create_batch(
            api_client, name="2020 register", certificate_type_id=birth, fields=BIRTH_COLUMNS
        )
        second = await create_batch(
            api_client, name="2020 register", certificate_type_id=birth, fields=BIRTH_COLUMNS
        )
        assert first["id"] != second["id"]
        assert len(second["columns"]) == len(BIRTH_COLUMNS)


class TestWithoutColumns:
    async def test_a_category_alone_pins_that_category_default_columns(
        self, api_client: AsyncClient, operator_user: User
    ) -> None:
        """An office that has not customised anything still gets a configured batch."""
        await authenticate(api_client, operator_user)
        batch = await create_batch(api_client, certificate_type_id=await type_id(api_client))

        names = {column["name"] for column in batch["columns"]}
        assert "child_full_name" in names, "the built-in birth columns should be pinned"

    async def test_a_batch_with_no_category_still_reports_columns(
        self, api_client: AsyncClient, operator_user: User
    ) -> None:
        """How every batch created before categories existed behaves. It reads for the
        common fields, and the screen must be able to say so rather than show nothing."""
        await authenticate(api_client, operator_user)
        batch = await create_batch(api_client, name="Loose files")

        assert batch["columns"], "a batch always reads for something"
        assert "certificate_number" in {column["name"] for column in batch["columns"]}


class TestRefusals:
    async def test_columns_without_a_category_are_refused(
        self, api_client: AsyncClient, operator_user: User
    ) -> None:
        await authenticate(api_client, operator_user)
        await create_batch(api_client, fields=BIRTH_COLUMNS, expect=422)

    async def test_columns_with_nothing_marked_as_the_certificate_number_are_refused(
        self, api_client: AsyncClient, operator_user: User
    ) -> None:
        """The register is keyed by certificate number, so one column has to be it.

        Not a hardcoded field: the column can be called anything and sit anywhere. What
        is required is that the office says which of their columns it is, because a
        batch whose records cannot be filed would only be discovered after a thousand
        uploads had been read.
        """
        await authenticate(api_client, operator_user)
        response = await api_client.post(
            BATCHES,
            json={
                "name": "No identifier",
                "certificate_type_id": await type_id(api_client),
                "fields": [{"name": "child_name", "label": "Name", "kind": "name"}],
            },
        )
        assert response.status_code == 422
        assert "number" in response.text.lower()

    async def test_an_empty_column_list_is_refused(
        self, api_client: AsyncClient, operator_user: User
    ) -> None:
        """Better than accepting it: a batch with no columns extracts nothing, and
        would only be discovered after somebody uploaded into it."""
        await authenticate(api_client, operator_user)
        await create_batch(
            api_client, certificate_type_id=await type_id(api_client), fields=[], expect=422
        )

    async def test_defining_columns_and_reusing_a_schema_at_once_is_refused(
        self, api_client: AsyncClient, operator_user: User
    ) -> None:
        await authenticate(api_client, operator_user)
        await create_batch(
            api_client,
            certificate_type_id=await type_id(api_client),
            fields=BIRTH_COLUMNS,
            schema_version_id=str(uuid.uuid4()),
            expect=422,
        )

    async def test_two_columns_with_the_same_key_are_refused(
        self, api_client: AsyncClient, operator_user: User
    ) -> None:
        await authenticate(api_client, operator_user)
        await create_batch(
            api_client,
            certificate_type_id=await type_id(api_client),
            fields=[
                {
                    "name": "child_name",
                    "label": "Name",
                    "kind": "name",
                    "role": "identifier",
                },
                {"name": "child_name", "label": "Name again", "kind": "name"},
            ],
            expect=422,
        )

    async def test_a_schema_from_another_workspace_is_not_found(
        self, api_client: AsyncClient, operator_user: User
    ) -> None:
        await authenticate(api_client, operator_user)
        await create_batch(api_client, schema_version_id=str(uuid.uuid4()), expect=404)

    async def test_a_viewer_cannot_create_a_batch(
        self, api_client: AsyncClient, viewer_user: User
    ) -> None:
        await authenticate(api_client, viewer_user)
        await create_batch(api_client, expect=403)


class TestListingByCategory:
    async def test_a_category_lists_only_its_own_batches(
        self, api_client: AsyncClient, operator_user: User
    ) -> None:
        await authenticate(api_client, operator_user)
        birth = await type_id(api_client, "BIRTH")
        death = await type_id(api_client, "DEATH")
        await create_batch(api_client, name="Birth 2020", certificate_type_id=birth)
        await create_batch(api_client, name="Death 2020", certificate_type_id=death)

        listed = await api_client.get(f"{BATCHES}?filter[certificate_type_id]={birth}")
        assert listed.status_code == 200, listed.text
        assert [item["name"] for item in listed.json()["items"]] == ["Birth 2020"]

    async def test_the_total_counts_only_that_category(
        self, api_client: AsyncClient, operator_user: User
    ) -> None:
        await authenticate(api_client, operator_user)
        birth = await type_id(api_client, "BIRTH")
        await create_batch(api_client, name="Birth 2020", certificate_type_id=birth)
        await create_batch(api_client, name="Birth 2021", certificate_type_id=birth)
        await create_batch(
            api_client, name="Death 2020", certificate_type_id=await type_id(api_client, "DEATH")
        )

        counted = await api_client.get(
            f"{BATCHES}?filter[certificate_type_id]={birth}&include_total=true"
        )
        assert counted.json()["meta"]["total"] == 2


class TestTheExportFollowsTheColumns:
    """The CSV a batch produces is the batch's columns, and nothing else.

    This is what the whole configuration is for: an office defines four columns and
    gets a four-column spreadsheet. Appending the built-in fields for the certificate
    type underneath them turned a four-column register into a forty-column one with the
    four somebody asked for buried in the middle.
    """

    async def test_the_csv_holds_the_configured_columns_and_no_builtin_ones(
        self, api_client: AsyncClient, operator_user: User
    ) -> None:
        await authenticate(api_client, operator_user)
        batch = await create_batch(
            api_client,
            certificate_type_id=await type_id(api_client),
            fields=[
                {
                    "name": "cert_no",
                    "label": "Certificate No",
                    "kind": "reference",
                    "role": "identifier",
                },
                {"name": "baby", "label": "Name", "kind": "name"},
            ],
        )

        preview = await api_client.get(f"{BATCHES}/{batch['id']}/export/preview")
        assert preview.status_code == 200, preview.text
        columns = preview.json()["columns"]

        assert "Certificate No" in columns
        assert "Name" in columns
        # The built-in birth columns this batch did not ask for.
        assert "Name of child" not in columns
        assert "Mother's CNIC" not in columns
        assert "Time of birth" not in columns

    async def test_the_serial_and_type_still_come_first(
        self, api_client: AsyncClient, operator_user: User
    ) -> None:
        """Required of every export, whatever the batch configured."""
        await authenticate(api_client, operator_user)
        batch = await create_batch(
            api_client, certificate_type_id=await type_id(api_client), fields=BIRTH_COLUMNS
        )

        preview = await api_client.get(f"{BATCHES}/{batch['id']}/export/preview")
        assert preview.json()["columns"][:2] == ["Serial no", "Certificate type"]

    async def test_a_batch_that_configured_nothing_still_exports_its_type_columns(
        self, api_client: AsyncClient, operator_user: User
    ) -> None:
        """The fallback has to keep working, or every batch made before this does not
        export what it used to."""
        await authenticate(api_client, operator_user)
        batch = await create_batch(api_client, name="Loose files")

        preview = await api_client.get(f"{BATCHES}/{batch['id']}/export/preview")
        assert "Certificate number" in preview.json()["columns"]
