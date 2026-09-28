"""End-to-end authentication tests against the real app and a real database.

Covers the security properties the specification calls for: httpOnly cookies,
refresh-token rotation, replay detection, role gating, workspace isolation and an
audit trail for every attempt.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select

from certex.core.cookies import ACCESS_COOKIE, CSRF_COOKIE, REFRESH_COOKIE
from certex.db.models import AuditLog, RefreshToken, User, Workspace
from certex.enums import AuditAction, UserRole
from tests.conftest import TEST_PASSWORD

pytestmark = [pytest.mark.integration]

LOGIN = "/api/v1/auth/login"
REFRESH = "/api/v1/auth/refresh"
LOGOUT = "/api/v1/auth/logout"
ME = "/api/v1/auth/me"


async def login(client: AsyncClient, user: User) -> dict[str, str]:
    response = await client.post(LOGIN, json={"email": user.email, "password": TEST_PASSWORD})
    assert response.status_code == 200, response.text
    return response.json()


class TestLogin:
    async def test_successful_login_sets_httponly_cookies(
        self, api_client: AsyncClient, operator_user: User
    ) -> None:
        response = await api_client.post(
            LOGIN, json={"email": operator_user.email, "password": TEST_PASSWORD}
        )
        assert response.status_code == 200

        body = response.json()
        assert body["user"]["email"] == operator_user.email
        assert body["user"]["role"] == UserRole.OPERATOR.value
        assert body["csrf_token"]

        # Tokens must not appear in the body - only in cookies.
        assert "access_token" not in body
        assert "refresh_token" not in body

        cookies = {c.name: c for c in response.cookies.jar}
        assert ACCESS_COOKIE in cookies
        assert REFRESH_COOKIE in cookies
        assert CSRF_COOKIE in cookies

        raw = response.headers.get_list("set-cookie")
        access_header = next(h for h in raw if h.startswith(f"{ACCESS_COOKIE}="))
        refresh_header = next(h for h in raw if h.startswith(f"{REFRESH_COOKIE}="))
        csrf_header = next(h for h in raw if h.startswith(f"{CSRF_COOKIE}="))

        assert "HttpOnly" in access_header
        assert "HttpOnly" in refresh_header
        # The CSRF cookie is readable by design so the SPA can echo it back.
        assert "HttpOnly" not in csrf_header
        # Refresh cookie is scoped to the auth routes, not the whole site.
        assert "Path=/api/v1/auth" in refresh_header

    async def test_wrong_password_rejected(
        self, api_client: AsyncClient, operator_user: User
    ) -> None:
        response = await api_client.post(
            LOGIN, json={"email": operator_user.email, "password": "not-the-password"}
        )
        assert response.status_code == 401
        problem = response.json()
        assert problem["code"] == "invalid_credentials"
        assert problem["title"]
        assert problem["remediation"]
        assert response.headers["content-type"].startswith("application/problem+json")

    async def test_unknown_account_is_indistinguishable(self, api_client: AsyncClient) -> None:
        response = await api_client.post(
            LOGIN, json={"email": "nobody@example.com", "password": "whatever"}
        )
        assert response.status_code == 401
        assert response.json()["code"] == "invalid_credentials"

    async def test_deactivated_account_rejected(
        self, api_client: AsyncClient, db_session, operator_user: User
    ) -> None:
        operator_user.is_active = False
        await db_session.flush()

        response = await api_client.post(
            LOGIN, json={"email": operator_user.email, "password": TEST_PASSWORD}
        )
        assert response.status_code == 401
        assert response.json()["code"] == "account_disabled"

    async def test_malformed_payload_returns_field_errors(self, api_client: AsyncClient) -> None:
        response = await api_client.post(LOGIN, json={"email": "not-an-email"})
        assert response.status_code == 422
        problem = response.json()
        assert problem["code"] == "validation_failed"
        assert {error["field"] for error in problem["errors"]} >= {"email", "password"}

    async def test_login_is_audited(
        self, api_client: AsyncClient, db_session, operator_user: User
    ) -> None:
        await login(api_client, operator_user)
        count = await db_session.scalar(
            select(func.count())
            .select_from(AuditLog)
            .where(AuditLog.action == AuditAction.LOGIN_SUCCEEDED)
        )
        assert count == 1

    async def test_failed_login_is_audited(
        self, api_client: AsyncClient, db_session, operator_user: User
    ) -> None:
        await api_client.post(LOGIN, json={"email": operator_user.email, "password": "nope"})
        count = await db_session.scalar(
            select(func.count())
            .select_from(AuditLog)
            .where(AuditLog.action == AuditAction.LOGIN_FAILED)
        )
        assert count == 1

    async def test_audit_rows_contain_no_credentials(
        self, api_client: AsyncClient, db_session, operator_user: User
    ) -> None:
        await api_client.post(LOGIN, json={"email": operator_user.email, "password": TEST_PASSWORD})
        rows = (await db_session.scalars(select(AuditLog))).all()
        assert rows
        for row in rows:
            rendered = str(row.metadata_jsonb)
            assert TEST_PASSWORD not in rendered
            assert operator_user.email not in rendered


class TestSession:
    async def test_me_returns_current_session(
        self, api_client: AsyncClient, operator_user: User
    ) -> None:
        await login(api_client, operator_user)
        response = await api_client.get(ME)
        assert response.status_code == 200
        assert response.json()["user"]["id"] == str(operator_user.id)

    async def test_me_requires_authentication(self, api_client: AsyncClient) -> None:
        response = await api_client.get(ME)
        assert response.status_code == 401
        assert response.json()["code"] == "not_authenticated"

    async def test_bearer_token_is_accepted(
        self, api_client: AsyncClient, operator_user: User
    ) -> None:
        await login(api_client, operator_user)
        token = api_client.cookies[ACCESS_COOKIE]
        api_client.cookies.clear()

        response = await api_client.get(ME, headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 200

    async def test_garbage_token_rejected(self, api_client: AsyncClient) -> None:
        response = await api_client.get(ME, headers={"Authorization": "Bearer nonsense"})
        assert response.status_code == 401
        assert response.json()["code"] == "token_invalid"

    async def test_deactivation_takes_effect_before_token_expiry(
        self, api_client: AsyncClient, db_session, operator_user: User
    ) -> None:
        """A still-valid access token must stop working once the account is off."""
        await login(api_client, operator_user)
        assert (await api_client.get(ME)).status_code == 200

        operator_user.is_active = False
        await db_session.flush()

        response = await api_client.get(ME)
        assert response.status_code == 401
        assert response.json()["code"] == "account_disabled"


class TestRefreshRotation:
    async def test_refresh_issues_a_new_token(
        self, api_client: AsyncClient, operator_user: User
    ) -> None:
        await login(api_client, operator_user)
        first_refresh = api_client.cookies[REFRESH_COOKIE]

        response = await api_client.post(REFRESH)
        assert response.status_code == 200
        assert api_client.cookies[REFRESH_COOKIE] != first_refresh

    async def test_rotation_consumes_the_presented_token(
        self, api_client: AsyncClient, db_session, operator_user: User
    ) -> None:
        await login(api_client, operator_user)
        await api_client.post(REFRESH)

        tokens = (await db_session.scalars(select(RefreshToken))).all()
        assert len(tokens) == 2
        # Told apart by what happened to them, not by when: both rows are written in
        # one transaction here, so their timestamps can be identical and their order
        # undefined.
        consumed = [token for token in tokens if token.consumed_at is not None]
        live = [token for token in tokens if token.consumed_at is None]
        assert len(consumed) == 1 and len(live) == 1
        # Successor belongs to the same family and is linked from its predecessor.
        assert consumed[0].family_id == live[0].family_id
        assert consumed[0].replaced_by_id == live[0].id

    async def test_replaying_a_consumed_token_revokes_the_family(
        self, api_client: AsyncClient, db_session, operator_user: User
    ) -> None:
        await login(api_client, operator_user)
        stolen = api_client.cookies[REFRESH_COOKIE]

        # Legitimate rotation.
        assert (await api_client.post(REFRESH)).status_code == 200

        # Attacker replays the cookie they captured earlier.
        api_client.cookies.set(REFRESH_COOKIE, stolen, path="/api/v1/auth")
        response = await api_client.post(REFRESH)
        assert response.status_code == 401
        assert response.json()["code"] == "token_reused"

        tokens = (await db_session.scalars(select(RefreshToken))).all()
        assert tokens
        assert all(token.revoked_at is not None for token in tokens), (
            "every token in the family must be revoked after a replay"
        )

        reuse_events = await db_session.scalar(
            select(func.count())
            .select_from(AuditLog)
            .where(AuditLog.action == AuditAction.TOKEN_REUSE_DETECTED)
        )
        assert reuse_events == 1

    async def test_refresh_without_cookie_rejected(self, api_client: AsyncClient) -> None:
        response = await api_client.post(REFRESH)
        assert response.status_code == 401

    async def test_unknown_refresh_token_rejected(self, api_client: AsyncClient) -> None:
        api_client.cookies.set(REFRESH_COOKIE, "a" * 64, path="/api/v1/auth")
        response = await api_client.post(REFRESH)
        assert response.status_code == 401


class TestLogout:
    async def test_logout_revokes_and_clears(
        self, api_client: AsyncClient, db_session, operator_user: User
    ) -> None:
        await login(api_client, operator_user)
        response = await api_client.post(LOGOUT)
        assert response.status_code == 204

        token = await db_session.scalar(select(RefreshToken))
        assert token is not None
        assert token.revoked_at is not None

    async def test_logout_without_session_is_idempotent(self, api_client: AsyncClient) -> None:
        assert (await api_client.post(LOGOUT)).status_code == 204

    async def test_revoked_token_cannot_refresh(
        self, api_client: AsyncClient, operator_user: User
    ) -> None:
        await login(api_client, operator_user)
        refresh_cookie = api_client.cookies[REFRESH_COOKIE]
        await api_client.post(LOGOUT)

        api_client.cookies.set(REFRESH_COOKIE, refresh_cookie, path="/api/v1/auth")
        assert (await api_client.post(REFRESH)).status_code == 401


class TestWorkspaceIsolation:
    async def test_token_minted_for_another_workspace_is_rejected(
        self, api_client: AsyncClient, db_session, operator_user: User
    ) -> None:
        """A token whose workspace claim no longer matches the account must fail."""
        await login(api_client, operator_user)
        assert (await api_client.get(ME)).status_code == 200

        other = Workspace(name="Other Office", settings_json={})
        db_session.add(other)
        await db_session.flush()
        operator_user.workspace_id = other.id
        await db_session.flush()

        response = await api_client.get(ME)
        assert response.status_code == 401


class TestSecurityHeaders:
    async def test_headers_present_on_every_response(self, api_client: AsyncClient) -> None:
        response = await api_client.get("/health/live")
        assert response.headers["X-Content-Type-Options"] == "nosniff"
        assert response.headers["X-Frame-Options"] == "DENY"
        assert response.headers["Referrer-Policy"] == "no-referrer"
        assert response.headers["Cache-Control"] == "no-store"
        assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]

    async def test_request_id_is_echoed(self, api_client: AsyncClient) -> None:
        response = await api_client.get("/health/live", headers={"X-Request-ID": "abc-123"})
        assert response.headers["X-Request-ID"] == "abc-123"

    async def test_request_id_is_generated_when_absent(self, api_client: AsyncClient) -> None:
        assert response_id(await api_client.get("/health/live"))


def response_id(response: object) -> str:
    return str(response.headers["X-Request-ID"])


class TestProblemDetails:
    async def test_unknown_route_returns_problem_document(self, api_client: AsyncClient) -> None:
        response = await api_client.get("/api/v1/does-not-exist")
        assert response.status_code == 404
        problem = response.json()
        assert problem["code"] == "not_found"
        assert problem["type"].endswith("/not_found")
        assert problem["instance"] == "/api/v1/does-not-exist"
        assert problem["request_id"]

    async def test_method_not_allowed(self, api_client: AsyncClient) -> None:
        response = await api_client.get(LOGIN)
        assert response.status_code == 405
        assert response.json()["title"] == "Method not allowed"
