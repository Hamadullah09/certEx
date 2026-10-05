"""The templates list over HTTP.

A template is written by the pipeline, never posted by a client, so the rules worth
testing are the ones about reading: that one office cannot see another's learned forms,
that the rule count reflects what is really inside the rule set, and that a workspace
which has learned nothing gets an empty list rather than an error. That last one
matters more than it sounds - the screen has to be able to tell "nothing learned yet"
apart from "this route is broken", and for a long time it could not, because the route
did not exist.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from certex.core.security import hash_password
from certex.db.models import Template, User, Workspace
from certex.enums import CertificateType, UserRole
from tests.conftest import TEST_PASSWORD, authenticate

pytestmark = [pytest.mark.integration]

TEMPLATES = "/api/v1/templates"


async def a_template(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    name: str = "Union Council 42 birth form",
    certificate_type: CertificateType = CertificateType.BIRTH,
    rules: int = 3,
    hit_count: int = 0,
    is_active: bool = True,
) -> Template:
    template = Template(
        workspace_id=workspace_id,
        name=name,
        fingerprint=uuid.uuid4().hex,
        certificate_type=certificate_type,
        rules_jsonb={
            "version": 1,
            "anchors": ["BIRTH CERTIFICATE"],
            "rules": [
                {"field": f"field_{index}", "anchor": f"Label {index}", "direction": "after"}
                for index in range(rules)
            ],
        },
        hit_count=hit_count,
        is_active=is_active,
    )
    session.add(template)
    await session.flush()
    return template


async def an_outsider(session: AsyncSession) -> User:
    """An administrator of a different office, to push against the tenancy boundary."""
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


class TestListing:
    async def test_a_workspace_that_has_learned_nothing_gets_an_empty_list(
        self, api_client: AsyncClient, viewer_user: User
    ) -> None:
        """Not a 404. The screen tells a reviewer "nothing learned yet", which is only
        honest if an empty register and a broken route look different."""
        await authenticate(api_client, viewer_user)
        response = await api_client.get(TEMPLATES)
        assert response.status_code == 200, response.text
        assert response.json()["items"] == []

    async def test_a_learned_form_is_listed_with_its_rule_count(
        self,
        api_client: AsyncClient,
        viewer_user: User,
        db_session: AsyncSession,
        workspace: Workspace,
    ) -> None:
        await a_template(db_session, workspace_id=workspace.id, rules=12, hit_count=47)
        await authenticate(api_client, viewer_user)

        body = (await api_client.get(TEMPLATES)).json()
        assert len(body["items"]) == 1
        listed = body["items"][0]
        assert listed["name"] == "Union Council 42 birth form"
        assert listed["certificate_type"] == "BIRTH"
        assert listed["rule_count"] == 12
        assert listed["hit_count"] == 47
        assert listed["is_active"] is True

    async def test_a_rule_set_that_is_empty_counts_zero_rather_than_failing(
        self,
        api_client: AsyncClient,
        viewer_user: User,
        db_session: AsyncSession,
        workspace: Workspace,
    ) -> None:
        """An older row, or one written before the rules were filled in, still lists."""
        template = await a_template(db_session, workspace_id=workspace.id)
        template.rules_jsonb = {}
        await db_session.flush()

        await authenticate(api_client, viewer_user)
        body = (await api_client.get(TEMPLATES)).json()
        assert body["items"][0]["rule_count"] == 0

    async def test_newest_is_first(
        self,
        api_client: AsyncClient,
        viewer_user: User,
        db_session: AsyncSession,
        workspace: Workspace,
    ) -> None:
        await a_template(db_session, workspace_id=workspace.id, name="Learned first")
        await a_template(db_session, workspace_id=workspace.id, name="Learned second")
        await authenticate(api_client, viewer_user)

        names = [item["name"] for item in (await api_client.get(TEMPLATES)).json()["items"]]
        assert names[0] == "Learned second"

    async def test_filtering_by_certificate_type(
        self,
        api_client: AsyncClient,
        viewer_user: User,
        db_session: AsyncSession,
        workspace: Workspace,
    ) -> None:
        await a_template(db_session, workspace_id=workspace.id, name="A birth form")
        await a_template(
            db_session,
            workspace_id=workspace.id,
            name="A death form",
            certificate_type=CertificateType.DEATH,
        )
        await authenticate(api_client, viewer_user)

        body = (await api_client.get(f"{TEMPLATES}?certificate_type=DEATH")).json()
        assert [item["name"] for item in body["items"]] == ["A death form"]

    async def test_switched_off_templates_can_be_left_out(
        self,
        api_client: AsyncClient,
        viewer_user: User,
        db_session: AsyncSession,
        workspace: Workspace,
    ) -> None:
        await a_template(db_session, workspace_id=workspace.id, name="Still used")
        await a_template(db_session, workspace_id=workspace.id, name="Retired", is_active=False)
        await authenticate(api_client, viewer_user)

        everything = (await api_client.get(TEMPLATES)).json()
        active = (await api_client.get(f"{TEMPLATES}?active_only=true")).json()
        assert len(everything["items"]) == 2
        assert [item["name"] for item in active["items"]] == ["Still used"]

    async def test_a_total_is_counted_only_when_asked_for(
        self,
        api_client: AsyncClient,
        viewer_user: User,
        db_session: AsyncSession,
        workspace: Workspace,
    ) -> None:
        await a_template(db_session, workspace_id=workspace.id)
        await authenticate(api_client, viewer_user)

        assert (await api_client.get(TEMPLATES)).json()["meta"]["total"] is None
        counted = (await api_client.get(f"{TEMPLATES}?include_total=true")).json()
        assert counted["meta"]["total"] == 1

    async def test_paging_walks_every_row_once(
        self,
        api_client: AsyncClient,
        viewer_user: User,
        db_session: AsyncSession,
        workspace: Workspace,
    ) -> None:
        """Templates written in one transaction share a timestamp, which is exactly the
        case a keyset over the timestamp alone would drop rows at."""
        for index in range(5):
            await a_template(db_session, workspace_id=workspace.id, name=f"Form {index}")
        await authenticate(api_client, viewer_user)

        seen: list[str] = []
        cursor: str | None = None
        for _ in range(5):
            query = f"{TEMPLATES}?limit=2" + (f"&cursor={cursor}" if cursor else "")
            page = (await api_client.get(query)).json()
            seen.extend(item["id"] for item in page["items"])
            cursor = page["meta"]["next_cursor"]
            if cursor is None:
                break
        assert len(seen) == 5
        assert len(set(seen)) == 5


class TestTenancy:
    async def test_another_office_cannot_see_these_templates(
        self, api_client: AsyncClient, db_session: AsyncSession, workspace: Workspace
    ) -> None:
        await a_template(db_session, workspace_id=workspace.id)
        await authenticate(api_client, await an_outsider(db_session))

        assert (await api_client.get(TEMPLATES)).json()["items"] == []

    async def test_one_template_cannot_be_read_across_the_boundary(
        self, api_client: AsyncClient, db_session: AsyncSession, workspace: Workspace
    ) -> None:
        template = await a_template(db_session, workspace_id=workspace.id)
        await authenticate(api_client, await an_outsider(db_session))

        assert (await api_client.get(f"{TEMPLATES}/{template.id}")).status_code == 404


class TestReadingOne:
    async def test_one_learned_form(
        self,
        api_client: AsyncClient,
        viewer_user: User,
        db_session: AsyncSession,
        workspace: Workspace,
    ) -> None:
        template = await a_template(db_session, workspace_id=workspace.id, rules=7)
        await authenticate(api_client, viewer_user)

        response = await api_client.get(f"{TEMPLATES}/{template.id}")
        assert response.status_code == 200, response.text
        assert response.json()["rule_count"] == 7

    async def test_an_unknown_id_is_not_found(
        self, api_client: AsyncClient, viewer_user: User
    ) -> None:
        await authenticate(api_client, viewer_user)
        assert (await api_client.get(f"{TEMPLATES}/{uuid.uuid4()}")).status_code == 404
