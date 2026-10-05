"""The office itself: what it is called.

A deployment starts out named by whatever the seeding script was given, which unless
somebody set ``CERTEX_SEED_WORKSPACE_NAME`` is "Demo Records Office". That name is on
every screen and in the name of every file exported, so an office with no way to change
it is stuck introducing itself as a demo.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from certex.db.models import User, Workspace
from tests.conftest import authenticate

pytestmark = [pytest.mark.integration]

NAME = "/api/v1/workspace/name"


class TestRenaming:
    async def test_an_administrator_renames_the_office(
        self,
        api_client: AsyncClient,
        admin_user: User,
        db_session: AsyncSession,
        workspace: Workspace,
    ) -> None:
        await authenticate(api_client, admin_user)

        response = await api_client.put(NAME, json={"name": "Union Council 42, Lahore"})
        assert response.status_code == 200, response.text
        assert response.json()["name"] == "Union Council 42, Lahore"

        await db_session.refresh(workspace)
        assert workspace.name == "Union Council 42, Lahore"

    async def test_the_new_name_comes_back_on_the_session(
        self, api_client: AsyncClient, admin_user: User
    ) -> None:
        """The header reads it from there, so it has to follow immediately."""
        await authenticate(api_client, admin_user)
        await api_client.put(NAME, json={"name": "Lahore Records"})

        me = await api_client.get("/api/v1/auth/me")
        assert me.json()["workspace"]["name"] == "Lahore Records"

    async def test_surrounding_space_is_tidied(
        self, api_client: AsyncClient, admin_user: User
    ) -> None:
        await authenticate(api_client, admin_user)
        response = await api_client.put(NAME, json={"name": "  Union   Council  42  "})
        assert response.json()["name"] == "Union Council 42"

    async def test_an_empty_name_is_refused(
        self, api_client: AsyncClient, admin_user: User
    ) -> None:
        """An office with a blank name has a blank header and blank export filenames."""
        await authenticate(api_client, admin_user)
        assert (await api_client.put(NAME, json={"name": "   "})).status_code == 400

    async def test_an_operator_cannot_rename_the_office(
        self, api_client: AsyncClient, operator_user: User
    ) -> None:
        await authenticate(api_client, operator_user)
        assert (await api_client.put(NAME, json={"name": "Mine now"})).status_code == 403

    async def test_signing_in_is_required(self, api_client: AsyncClient) -> None:
        assert (await api_client.put(NAME, json={"name": "Anybody"})).status_code == 401


class TestSeedingAfterARename:
    """Seeding runs on every start, and an office can rename itself.

    The seeder used to find its workspace by name, so the first restart after a rename
    created a second, empty workspace - and every restart after that another one. The
    account is the stable identity; the name is a label somebody is allowed to change.
    """

    async def test_renaming_does_not_make_the_seeder_create_another_workspace(
        self, api_client: AsyncClient, admin_user: User, db_session: AsyncSession
    ) -> None:
        from sqlalchemy import func, select

        from certex.cli import _seed
        from certex.config import get_settings

        await authenticate(api_client, admin_user)
        await api_client.put(NAME, json={"name": "Union Council 42"})
        await db_session.commit()

        settings = get_settings()
        before = await db_session.scalar(select(func.count()).select_from(Workspace))

        # The seeder is synchronous and opens its own session, like the start script.
        import asyncio

        await asyncio.to_thread(_seed, settings)

        after = await db_session.scalar(select(func.count()).select_from(Workspace))
        assert after == before, "seeding after a rename created a second workspace"

    async def test_the_renamed_workspace_keeps_its_name(
        self, api_client: AsyncClient, admin_user: User, db_session: AsyncSession, workspace: Workspace
    ) -> None:
        import asyncio

        from certex.cli import _seed
        from certex.config import get_settings

        await authenticate(api_client, admin_user)
        await api_client.put(NAME, json={"name": "Union Council 42"})
        await db_session.commit()

        await asyncio.to_thread(_seed, get_settings())

        await db_session.refresh(workspace)
        assert workspace.name == "Union Council 42", "seeding renamed the office back"
