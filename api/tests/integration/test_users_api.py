"""Managing the people in an office.

Most of what is tested here is refusal, because the ways this goes wrong are not
ordinary bugs - they are an office locking itself out of its own register with no
database console to climb back in through, or an endpoint quietly answering the
question "does this person work here" to anybody who asks.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from certex.db.models import RefreshToken, User
from certex.enums import UserRole
from tests.conftest import TEST_PASSWORD, authenticate

pytestmark = [pytest.mark.integration]

USERS = "/api/v1/users"
FORGOT = "/api/v1/auth/forgot-password"

GOOD_PASSWORD = "correct horse battery staple"


async def add_user(
    client: AsyncClient,
    *,
    email: str | None = None,
    role: str = "OPERATOR",
    full_name: str | None = "Ayesha Noor",
    password: str = GOOD_PASSWORD,
    expect: int = 201,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "email": email or f"new-{uuid.uuid4().hex[:8]}@example.com",
        "role": role,
        "password": password,
    }
    if full_name is not None:
        payload["full_name"] = full_name
    response = await client.post(USERS, json=payload)
    assert response.status_code == expect, response.text
    body: dict[str, Any] = response.json()
    return body


class TestListing:
    async def test_any_member_can_see_who_works_here(
        self, api_client: AsyncClient, viewer_user: User
    ) -> None:
        """Not administrator-only: a reviewer looking at "approved by" has to be able
        to put a name to it."""
        await authenticate(api_client, viewer_user)
        response = await api_client.get(USERS)
        assert response.status_code == 200, response.text
        assert any(item["email"] == viewer_user.email for item in response.json())

    async def test_no_password_material_is_returned(
        self, api_client: AsyncClient, admin_user: User
    ) -> None:
        await authenticate(api_client, admin_user)
        body = (await api_client.get(USERS)).text
        assert "password_hash" not in body
        assert "$2b$" not in body, "a bcrypt hash reached the wire"

    async def test_another_office_is_not_listed(
        self, api_client: AsyncClient, admin_user: User, db_session: AsyncSession
    ) -> None:
        from certex.core.security import hash_password
        from certex.db.models import Workspace

        other = Workspace(name=f"Other {uuid.uuid4().hex[:6]}", settings_json={})
        db_session.add(other)
        await db_session.flush()
        db_session.add(
            User(
                workspace_id=other.id,
                email=f"outsider-{uuid.uuid4().hex[:8]}@example.com",
                password_hash=hash_password(TEST_PASSWORD),
                role=UserRole.ADMIN,
                is_active=True,
            )
        )
        await db_session.flush()

        await authenticate(api_client, admin_user)
        listed = (await api_client.get(USERS)).json()
        assert all("outsider" not in item["email"] for item in listed)


class TestAdding:
    async def test_an_administrator_adds_somebody(
        self, api_client: AsyncClient, admin_user: User
    ) -> None:
        await authenticate(api_client, admin_user)
        created = await add_user(api_client, full_name="Ayesha Noor", role="OPERATOR")
        assert created["full_name"] == "Ayesha Noor"
        assert created["role"] == "OPERATOR"
        assert created["is_active"] is True

    async def test_an_operator_cannot(self, api_client: AsyncClient, operator_user: User) -> None:
        await authenticate(api_client, operator_user)
        await add_user(api_client, expect=403)

    async def test_a_repeated_address_is_refused(
        self, api_client: AsyncClient, admin_user: User
    ) -> None:
        await authenticate(api_client, admin_user)
        address = f"twice-{uuid.uuid4().hex[:8]}@example.com"
        await add_user(api_client, email=address)
        await add_user(api_client, email=address, expect=409)

    async def test_a_short_password_is_refused(
        self, api_client: AsyncClient, admin_user: User
    ) -> None:
        await authenticate(api_client, admin_user)
        await add_user(api_client, password="short", expect=422)

    async def test_the_new_account_can_sign_in(
        self, api_client: AsyncClient, admin_user: User
    ) -> None:
        await authenticate(api_client, admin_user)
        address = f"signs-in-{uuid.uuid4().hex[:8]}@example.com"
        await add_user(api_client, email=address)

        response = await api_client.post(
            "/api/v1/auth/login", json={"email": address, "password": GOOD_PASSWORD}
        )
        assert response.status_code == 200, response.text


class TestRolesAndAccess:
    async def test_an_administrator_changes_a_role(
        self, api_client: AsyncClient, admin_user: User
    ) -> None:
        await authenticate(api_client, admin_user)
        person = await add_user(api_client, role="VIEWER")

        response = await api_client.patch(f"{USERS}/{person['id']}", json={"role": "OPERATOR"})
        assert response.status_code == 200, response.text
        assert response.json()["role"] == "OPERATOR"

    async def test_switching_an_account_off_ends_its_sessions(
        self, api_client: AsyncClient, admin_user: User, db_session: AsyncSession
    ) -> None:
        """The point of switching one off. A live refresh token would keep it working."""
        await authenticate(api_client, admin_user)
        address = f"ending-{uuid.uuid4().hex[:8]}@example.com"
        person = await add_user(api_client, email=address)

        async with AsyncClient(
            transport=api_client._transport, base_url=str(api_client.base_url)
        ) as theirs:
            await theirs.post(
                "/api/v1/auth/login", json={"email": address, "password": GOOD_PASSWORD}
            )

        live = await db_session.scalar(
            select(RefreshToken).where(
                RefreshToken.user_id == uuid.UUID(person["id"]),
                RefreshToken.revoked_at.is_(None),
            )
        )
        assert live is not None, "the sign-in should have left a token to revoke"

        await api_client.patch(f"{USERS}/{person['id']}", json={"is_active": False})
        await db_session.refresh(live)
        assert live.revoked_at is not None

    async def test_you_cannot_change_your_own_role(
        self, api_client: AsyncClient, admin_user: User
    ) -> None:
        """Nor undo it afterwards, which is why it is refused rather than warned about."""
        await authenticate(api_client, admin_user)
        response = await api_client.patch(f"{USERS}/{admin_user.id}", json={"role": "VIEWER"})
        assert response.status_code == 400
        assert "your own role" in response.text

    async def test_you_cannot_switch_yourself_off(
        self, api_client: AsyncClient, admin_user: User
    ) -> None:
        await authenticate(api_client, admin_user)
        response = await api_client.patch(f"{USERS}/{admin_user.id}", json={"is_active": False})
        assert response.status_code == 400

    async def test_another_administrator_can_be_demoted(
        self, api_client: AsyncClient, admin_user: User
    ) -> None:
        """What keeps an office from locking itself out is the self-change rules above,
        not a count of administrators: the only person who can demote one is another
        active administrator, who is still there afterwards. So this is allowed, and the
        office still has an administrator - the one who did it."""
        await authenticate(api_client, admin_user)
        second = await add_user(api_client, role="ADMIN")

        response = await api_client.patch(f"{USERS}/{second['id']}", json={"role": "VIEWER"})
        assert response.status_code == 200, response.text
        assert response.json()["role"] == "VIEWER"

        listed = (await api_client.get(USERS)).json()
        admins = [item for item in listed if item["role"] == "ADMIN" and item["is_active"]]
        assert admins, "the office must still have an administrator"

    async def test_a_name_can_be_corrected(self, api_client: AsyncClient, admin_user: User) -> None:
        await authenticate(api_client, admin_user)
        person = await add_user(api_client, full_name="Ayesha Nur")

        response = await api_client.patch(
            f"{USERS}/{person['id']}", json={"full_name": "Ayesha Noor"}
        )
        assert response.json()["full_name"] == "Ayesha Noor"


class TestPasswords:
    async def test_an_administrator_sets_one_and_it_works(
        self, api_client: AsyncClient, admin_user: User
    ) -> None:
        await authenticate(api_client, admin_user)
        address = f"reset-{uuid.uuid4().hex[:8]}@example.com"
        person = await add_user(api_client, email=address)

        replacement = "a different set of words"
        response = await api_client.post(
            f"{USERS}/{person['id']}/password", json={"password": replacement}
        )
        assert response.status_code == 204, response.text

        assert (
            await api_client.post(
                "/api/v1/auth/login", json={"email": address, "password": GOOD_PASSWORD}
            )
        ).status_code == 401
        assert (
            await api_client.post(
                "/api/v1/auth/login", json={"email": address, "password": replacement}
            )
        ).status_code == 200

    async def test_an_operator_cannot_set_somebody_elses(
        self, api_client: AsyncClient, admin_user: User, operator_user: User
    ) -> None:
        await authenticate(api_client, admin_user)
        person = await add_user(api_client)

        await authenticate(api_client, operator_user)
        response = await api_client.post(
            f"{USERS}/{person['id']}/password", json={"password": GOOD_PASSWORD}
        )
        assert response.status_code == 403

    async def test_changing_your_own_needs_the_current_one(
        self, api_client: AsyncClient, operator_user: User
    ) -> None:
        await authenticate(api_client, operator_user)
        response = await api_client.post(
            f"{USERS}/me/password",
            json={"current_password": "not it", "new_password": GOOD_PASSWORD},
        )
        assert response.status_code == 400
        assert "current password" in response.text.lower()

    async def test_changing_your_own_works(
        self, api_client: AsyncClient, operator_user: User
    ) -> None:
        await authenticate(api_client, operator_user)
        response = await api_client.post(
            f"{USERS}/me/password",
            json={"current_password": TEST_PASSWORD, "new_password": GOOD_PASSWORD},
        )
        assert response.status_code == 204, response.text

        assert (
            await api_client.post(
                "/api/v1/auth/login",
                json={"email": operator_user.email, "password": GOOD_PASSWORD},
            )
        ).status_code == 200

    async def test_the_same_password_again_is_refused(
        self, api_client: AsyncClient, operator_user: User
    ) -> None:
        await authenticate(api_client, operator_user)
        response = await api_client.post(
            f"{USERS}/me/password",
            json={"current_password": TEST_PASSWORD, "new_password": TEST_PASSWORD},
        )
        assert response.status_code == 400


class TestForgottenPasswords:
    async def test_a_request_is_recorded_for_the_administrator_to_see(
        self, api_client: AsyncClient, admin_user: User
    ) -> None:
        await authenticate(api_client, admin_user)
        address = f"forgot-{uuid.uuid4().hex[:8]}@example.com"
        person = await add_user(api_client, email=address)

        assert (await api_client.post(FORGOT, json={"email": address})).status_code == 202

        listed = (await api_client.get(USERS)).json()
        asked = next(item for item in listed if item["id"] == person["id"])
        assert asked["password_reset_requested_at"] is not None

    async def test_an_unknown_address_answers_exactly_the_same(
        self, api_client: AsyncClient
    ) -> None:
        """Otherwise this endpoint is a list of who works here, readable by anybody."""
        known = await api_client.post(FORGOT, json={"email": "nobody@example.com"})
        assert known.status_code == 202
        assert known.text in ("", "null")

    async def test_it_needs_no_session(self, api_client: AsyncClient) -> None:
        """Somebody who has forgotten their password cannot sign in to ask."""
        response = await api_client.post(FORGOT, json={"email": "whoever@example.com"})
        assert response.status_code == 202

    async def test_setting_the_password_clears_the_request(
        self, api_client: AsyncClient, admin_user: User
    ) -> None:
        await authenticate(api_client, admin_user)
        address = f"cleared-{uuid.uuid4().hex[:8]}@example.com"
        person = await add_user(api_client, email=address)
        await api_client.post(FORGOT, json={"email": address})

        await api_client.post(
            f"{USERS}/{person['id']}/password", json={"password": "another handful of words"}
        )

        listed = (await api_client.get(USERS)).json()
        done = next(item for item in listed if item["id"] == person["id"])
        assert done["password_reset_requested_at"] is None
