"""Security of the register surface, tested as a matrix rather than a story.

The earlier files test each route's behaviour. This one asks the same three questions
of *every* route the register added, because a gap in authorisation is almost never in
the route somebody was thinking about when they wrote the test:

* **Tenancy.** Given a real id from another office, does the route refuse - and refuse
  by saying "not found", so the id itself is not confirmed?
* **Authentication.** Without a session, does anything answer at all?
* **Role.** Does a viewer get refused every route that writes, and an operator every
  route reserved to an administrator?

Plus the two things a records system gets attacked through: cross-site requests from
another origin, and what an uploaded file is allowed to be.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

import pytest
import structlog
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from certex.api.v1 import certificates as certificates_api
from certex.api.v1 import imports as imports_api
from certex.core.cookies import CSRF_COOKIE
from certex.core.middleware import CSRF_HEADER
from certex.core.ratelimit import RateLimitDecision
from certex.core.security import hash_password
from certex.db.models import AuditLog, User, Workspace
from certex.enums import UserRole
from tests.conftest import TEST_PASSWORD, authenticate

pytestmark = [pytest.mark.integration]

TYPES = "/api/v1/certificate-types"
CERTIFICATES = "/api/v1/certificates"
IMPORTS = "/api/v1/imports"
SCHEMAS = "/api/v1/schemas"
SETTINGS = "/api/v1/workspace/settings"

ROW: dict[str, str] = {
    "certificate_number": "BC/LHR/2019/1001",
    "child_full_name": "Ayesha Noor Malik",
    "date_of_birth": "2019-04-03",
}


@dataclass(frozen=True, slots=True)
class Office:
    """One workspace with its own ids, so a test can borrow another's."""

    user: User
    type_id: str
    certificate_id: str
    schema_id: str
    import_id: str


async def make_user(session: AsyncSession, workspace: Workspace, role: UserRole) -> User:
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


async def build_office(client: AsyncClient, session: AsyncSession) -> Office:
    """A workspace holding one of everything the register can own."""
    workspace = Workspace(name=f"Office {uuid.uuid4().hex[:8]}", settings_json={})
    session.add(workspace)
    await session.flush()
    admin = await make_user(session, workspace, UserRole.ADMIN)
    await authenticate(client, admin)

    types = await client.get(TYPES)
    type_id = next(item["id"] for item in types.json() if item["key"] == "BIRTH")

    certificate = await client.post(
        CERTIFICATES, json={"certificate_type_id": type_id, "values": ROW}
    )
    assert certificate.status_code == 201, certificate.text

    schemas = await client.get(SCHEMAS, params={"certificate_type_id": type_id})
    schema_id = schemas.json()[0]["id"]

    upload = await client.post(
        IMPORTS,
        data={"certificate_type_id": type_id},
        files={
            "file": (
                "register.csv",
                b"certificate_number,child_full_name\r\nBC/1,Someone\r\n",
                "text/csv",
            )
        },
    )
    assert upload.status_code == 202, upload.text

    return Office(
        user=admin,
        type_id=type_id,
        certificate_id=certificate.json()["id"],
        schema_id=schema_id,
        import_id=upload.json()["id"],
    )


@pytest.fixture(autouse=True)
def no_broker(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the queue out of it.

    These tests are about who may call what, and an import route that reaches for a
    broker would make every one of them depend on Redis being up.
    """
    monkeypatch.setattr(imports_api, "enqueue", lambda _name, _kwargs: None)


@pytest.fixture
async def theirs(api_client: AsyncClient, db_session: AsyncSession) -> Office:
    return await build_office(api_client, db_session)


@pytest.fixture
async def mine(api_client: AsyncClient, db_session: AsyncSession) -> Office:
    return await build_office(api_client, db_session)


class TestTenancy:
    """Every id below is real. Only the office asking is wrong."""

    async def test_no_register_route_answers_for_another_office(
        self, api_client: AsyncClient, mine: Office, theirs: Office
    ) -> None:
        await authenticate(api_client, mine.user)

        reads = [
            f"{CERTIFICATES}/{theirs.certificate_id}",
            f"{CERTIFICATES}/{theirs.certificate_id}/duplicates",
            f"{CERTIFICATES}/{theirs.certificate_id}/history",
            f"{CERTIFICATES}/{theirs.certificate_id}/documents/{uuid.uuid4()}/content",
            f"{TYPES}/{theirs.type_id}/import-template",
            f"{SCHEMAS}/{theirs.schema_id}/versions",
            f"{IMPORTS}/{theirs.import_id}",
            f"{IMPORTS}/{theirs.import_id}/errors",
        ]
        refused = {path: (await api_client.get(path)).status_code for path in reads}
        assert all(status == 404 for status in refused.values()), refused

    async def test_a_borrowed_type_cannot_be_exported(
        self, api_client: AsyncClient, mine: Office, theirs: Office
    ) -> None:
        await authenticate(api_client, mine.user)
        response = await api_client.get(
            f"{CERTIFICATES}/export", params={"certificate_type_id": theirs.type_id}
        )
        assert response.status_code == 404

    async def test_no_register_route_writes_to_another_office(
        self, api_client: AsyncClient, mine: Office, theirs: Office
    ) -> None:
        await authenticate(api_client, mine.user)

        writes: list[tuple[str, str, dict[str, Any]]] = [
            (
                "PATCH",
                f"{CERTIFICATES}/{theirs.certificate_id}",
                {"values": {"child_full_name": "Changed"}, "note": "Because."},
            ),
            ("POST", f"{CERTIFICATES}/{theirs.certificate_id}/approve", {}),
            ("POST", f"{CERTIFICATES}/{theirs.certificate_id}/void", {"note": "Because."}),
            (
                "POST",
                f"{CERTIFICATES}/{theirs.certificate_id}/resolve-duplicate",
                {"other_id": mine.certificate_id, "same_certificate": True},
            ),
            (
                "POST",
                f"{CERTIFICATES}/{theirs.certificate_id}/documents",
                {"document_id": str(uuid.uuid4())},
            ),
            ("POST", f"{IMPORTS}/{theirs.import_id}/cancel", {}),
            (
                "POST",
                f"{SCHEMAS}/{theirs.schema_id}/versions",
                {
                    "fields": [
                        {
                            "name": "certificate_number",
                            "label": "No",
                            "kind": "reference",
                            "role": "identifier",
                        }
                    ]
                },
            ),
        ]
        refused: dict[str, int] = {}
        for method, path, body in writes:
            response = await api_client.request(method, path, json=body)
            refused[f"{method} {path}"] = response.status_code
        assert all(status == 404 for status in refused.values()), refused

    async def test_a_schema_cannot_be_created_against_a_borrowed_type(
        self, api_client: AsyncClient, mine: Office, theirs: Office
    ) -> None:
        await authenticate(api_client, mine.user)
        response = await api_client.post(
            SCHEMAS, json={"certificate_type_id": theirs.type_id, "name": "Borrowed"}
        )
        assert response.status_code == 404

    async def test_an_import_cannot_be_created_against_a_borrowed_type(
        self, api_client: AsyncClient, mine: Office, theirs: Office
    ) -> None:
        await authenticate(api_client, mine.user)
        response = await api_client.post(
            IMPORTS,
            data={"certificate_type_id": theirs.type_id},
            files={"file": ("x.csv", b"certificate_number\r\nBC/1\r\n", "text/csv")},
        )
        assert response.status_code == 404

    async def test_refusal_is_not_found_rather_than_forbidden(
        self, api_client: AsyncClient, mine: Office, theirs: Office
    ) -> None:
        """403 would confirm the id exists, which is itself a leak: it tells an
        attacker their guess was right and that another office holds that record."""
        await authenticate(api_client, mine.user)
        real = await api_client.get(f"{CERTIFICATES}/{theirs.certificate_id}")
        invented = await api_client.get(f"{CERTIFICATES}/{uuid.uuid4()}")
        assert real.status_code == invented.status_code == 404
        assert real.json()["code"] == invented.json()["code"]


class TestAuthentication:
    async def test_nothing_answers_without_a_session(
        self, api_client: AsyncClient, mine: Office
    ) -> None:
        api_client.cookies.clear()
        api_client.headers.pop(CSRF_HEADER, None)

        paths = [
            CERTIFICATES,
            f"{CERTIFICATES}/search?q=BC",
            f"{CERTIFICATES}/review-summary",
            f"{CERTIFICATES}/{mine.certificate_id}",
            f"{CERTIFICATES}/{mine.certificate_id}/history",
            f"{CERTIFICATES}/export?certificate_type_id={mine.type_id}",
            TYPES,
            SCHEMAS,
            IMPORTS,
            SETTINGS,
        ]
        answered = {path: (await api_client.get(path)).status_code for path in paths}
        assert all(status == 401 for status in answered.values()), answered


class TestRoles:
    async def test_a_viewer_cannot_write_anything(
        self, api_client: AsyncClient, mine: Office, db_session: AsyncSession
    ) -> None:
        workspace = await db_session.get(Workspace, mine.user.workspace_id)
        assert workspace is not None
        viewer = await make_user(db_session, workspace, UserRole.VIEWER)
        await authenticate(api_client, viewer)

        writes: list[tuple[str, str, dict[str, Any]]] = [
            ("POST", CERTIFICATES, {"certificate_type_id": mine.type_id, "values": ROW}),
            (
                "PATCH",
                f"{CERTIFICATES}/{mine.certificate_id}",
                {"values": {"child_full_name": "Changed"}, "note": "Because."},
            ),
            ("POST", f"{CERTIFICATES}/{mine.certificate_id}/approve", {}),
            ("POST", f"{CERTIFICATES}/{mine.certificate_id}/void", {"note": "Because."}),
            (
                "POST",
                f"{CERTIFICATES}/{mine.certificate_id}/resolve-duplicate",
                {"other_id": str(uuid.uuid4()), "same_certificate": True},
            ),
            ("POST", f"{IMPORTS}/{mine.import_id}/cancel", {}),
            ("POST", TYPES, {"key": "domicile", "name": "Domicile"}),
            ("POST", SCHEMAS, {"certificate_type_id": mine.type_id, "name": "New"}),
            ("PUT", SETTINGS, {"review": {}}),
        ]
        refused: dict[str, int] = {}
        for method, path, body in writes:
            response = await api_client.request(method, path, json=body)
            refused[f"{method} {path}"] = response.status_code
        assert all(status == 403 for status in refused.values()), refused

    async def test_a_viewer_can_still_read_the_register(
        self, api_client: AsyncClient, mine: Office, db_session: AsyncSession
    ) -> None:
        """Refusing writes must not refuse the work a viewer exists to do."""
        workspace = await db_session.get(Workspace, mine.user.workspace_id)
        assert workspace is not None
        viewer = await make_user(db_session, workspace, UserRole.VIEWER)
        await authenticate(api_client, viewer)

        allowed = {
            path: (await api_client.get(path)).status_code
            for path in (
                CERTIFICATES,
                f"{CERTIFICATES}/search?q=BC/LHR/2019/1001",
                f"{CERTIFICATES}/{mine.certificate_id}",
                f"{CERTIFICATES}/{mine.certificate_id}/history",
                TYPES,
            )
        }
        assert all(status == 200 for status in allowed.values()), allowed

    async def test_an_operator_cannot_do_an_administrator_s_work(
        self, api_client: AsyncClient, mine: Office, db_session: AsyncSession
    ) -> None:
        workspace = await db_session.get(Workspace, mine.user.workspace_id)
        assert workspace is not None
        operator = await make_user(db_session, workspace, UserRole.OPERATOR)
        await authenticate(api_client, operator)

        refused: dict[str, int] = {}
        for method, path, body in (
            ("POST", TYPES, {"key": "domicile", "name": "Domicile"}),
            ("POST", SCHEMAS, {"certificate_type_id": mine.type_id, "name": "New"}),
            ("PUT", SETTINGS, {"review": {}}),
            ("POST", f"{CERTIFICATES}/{mine.certificate_id}/void", {"note": "Because."}),
        ):
            response = await api_client.request(method, path, json=body)
            refused[f"{method} {path}"] = response.status_code
        assert all(status == 403 for status in refused.values()), refused


class TestCrossSiteRequests:
    async def test_a_write_without_the_csrf_header_is_refused(
        self, api_client: AsyncClient, mine: Office
    ) -> None:
        """The cookie is sent by the browser automatically; the header is not, and a
        page on another origin cannot read the cookie to echo it."""
        await authenticate(api_client, mine.user)
        api_client.headers.pop(CSRF_HEADER, None)

        response = await api_client.post(
            CERTIFICATES, json={"certificate_type_id": mine.type_id, "values": ROW}
        )
        assert response.status_code == 403

    async def test_a_write_with_the_wrong_csrf_value_is_refused(
        self, api_client: AsyncClient, mine: Office
    ) -> None:
        await authenticate(api_client, mine.user)
        api_client.headers[CSRF_HEADER] = str(uuid.uuid4())

        response = await api_client.patch(
            f"{CERTIFICATES}/{mine.certificate_id}",
            json={"values": {"child_full_name": "Changed"}, "note": "Because."},
        )
        assert response.status_code == 403

    async def test_reads_do_not_need_the_header(
        self, api_client: AsyncClient, mine: Office
    ) -> None:
        await authenticate(api_client, mine.user)
        api_client.headers.pop(CSRF_HEADER, None)
        assert (await api_client.get(f"{CERTIFICATES}/{mine.certificate_id}")).status_code == 200

    async def test_the_csrf_cookie_alone_is_not_enough(
        self, api_client: AsyncClient, mine: Office
    ) -> None:
        """A cross-origin form post carries the cookie and cannot carry the header.

        The session cookie and the readable CSRF cookie both stay on the client here;
        only the header is dropped, which is exactly what another origin can manage.
        """
        await authenticate(api_client, mine.user)
        # Read from the jar rather than by name: signing in twice in one test leaves
        # two cookies of the same name on different paths, which httpx refuses to
        # resolve by name alone.
        assert any(cookie.name == CSRF_COOKIE for cookie in api_client.cookies.jar), (
            "the readable CSRF cookie should have been set at sign-in"
        )
        api_client.headers.pop(CSRF_HEADER, None)

        response = await api_client.post(f"{CERTIFICATES}/{mine.certificate_id}/approve", json={})
        assert response.status_code == 403


class TestUploads:
    async def test_the_uploaded_name_never_reaches_the_storage_key(
        self, api_client: AsyncClient, mine: Office, db_session: AsyncSession
    ) -> None:
        from certex.db.models import CertificateImport

        await authenticate(api_client, mine.user)
        response = await api_client.post(
            IMPORTS,
            data={"certificate_type_id": mine.type_id},
            files={
                "file": (
                    "../../../etc/passwd\x00.csv",
                    b"certificate_number\r\nBC/9\r\n",
                    "text/csv",
                )
            },
        )
        assert response.status_code == 202, response.text
        stored = await db_session.get(CertificateImport, uuid.UUID(response.json()["id"]))
        assert stored is not None
        assert stored.storage_key == f"imports/{mine.user.workspace_id}/{stored.id}.csv"

    async def test_an_empty_upload_is_refused(self, api_client: AsyncClient, mine: Office) -> None:
        await authenticate(api_client, mine.user)
        response = await api_client.post(
            IMPORTS,
            data={"certificate_type_id": mine.type_id},
            files={"file": ("empty.csv", b"", "text/csv")},
        )
        assert response.status_code == 400

    async def test_a_claimed_content_type_decides_nothing(
        self, api_client: AsyncClient, mine: Office
    ) -> None:
        """The importer reads the bytes. A CSV claiming to be a PDF still imports, and
        a PDF claiming to be a CSV fails on its columns rather than on its label."""
        await authenticate(api_client, mine.user)
        response = await api_client.post(
            IMPORTS,
            data={"certificate_type_id": mine.type_id},
            files={
                "file": (
                    "register.csv",
                    b"certificate_number,child_full_name\r\nBC/8,Someone\r\n",
                    "application/pdf",
                )
            },
        )
        assert response.status_code == 202


class TestAuditTrail:
    async def test_every_decision_about_an_entry_is_recorded(
        self, api_client: AsyncClient, mine: Office, db_session: AsyncSession
    ) -> None:
        await authenticate(api_client, mine.user)
        await api_client.patch(
            f"{CERTIFICATES}/{mine.certificate_id}",
            json={"values": {"child_full_name": "Ayesha Noor"}, "note": "Matches the scan."},
        )
        await api_client.post(
            f"{CERTIFICATES}/{mine.certificate_id}/void", json={"note": "Withdrawn."}
        )

        actions = {
            action.value
            for action in (
                await db_session.scalars(
                    select(AuditLog.action).where(AuditLog.workspace_id == mine.user.workspace_id)
                )
            ).all()
        }
        assert {"certificate.created", "certificate.updated", "certificate.voided"} <= actions

    async def test_the_audit_trail_holds_no_field_values(
        self, api_client: AsyncClient, mine: Office, db_session: AsyncSession
    ) -> None:
        """An audit log is read by more people than the register is, so it records
        which fields changed and never what they now say."""
        await authenticate(api_client, mine.user)
        await api_client.patch(
            f"{CERTIFICATES}/{mine.certificate_id}",
            json={
                "values": {"child_full_name": "Zainab Fatima Khan"},
                "note": "Matches the scan.",
            },
        )

        rows = list(
            (
                await db_session.scalars(
                    select(AuditLog).where(AuditLog.workspace_id == mine.user.workspace_id)
                )
            ).all()
        )
        blob = repr([row.metadata_jsonb for row in rows])
        assert "Zainab" not in blob
        assert "child_full_name" in blob, "which field changed is worth recording"


class TestRateLimits:
    """The register's two ways to read or queue a great deal in one request.

    Both are bounded per user rather than per IP: an office shares one address, and
    keying on it would let one clerk's script lock out the counter.
    """

    async def test_searching_is_bounded(
        self, api_client: AsyncClient, mine: Office, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(certificates_api, "get_rate_limiter", lambda: _AlwaysDenied("searches"))
        await authenticate(api_client, mine.user)
        response = await api_client.get(f"{CERTIFICATES}/search", params={"q": "BC"})
        assert response.status_code == 429
        assert response.headers.get("retry-after")

    async def test_exporting_is_bounded(
        self, api_client: AsyncClient, mine: Office, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(certificates_api, "get_rate_limiter", lambda: _AlwaysDenied("exports"))
        await authenticate(api_client, mine.user)
        response = await api_client.get(
            f"{CERTIFICATES}/export", params={"certificate_type_id": mine.type_id}
        )
        assert response.status_code == 429

    async def test_importing_is_bounded(
        self, api_client: AsyncClient, mine: Office, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(imports_api, "get_rate_limiter", lambda: _AlwaysDenied("imports"))
        await authenticate(api_client, mine.user)
        response = await api_client.post(
            IMPORTS,
            data={"certificate_type_id": mine.type_id},
            files={"file": ("x.csv", b"certificate_number\r\nBC/7\r\n", "text/csv")},
        )
        assert response.status_code == 429

    async def test_the_limit_is_keyed_to_the_user_not_the_office(
        self, api_client: AsyncClient, mine: Office
    ) -> None:
        """An office shares one address; keying on it would let one clerk's script
        lock out the counter."""
        recorded: list[str] = []

        class Recording:
            async def check(
                self, bucket: str, identity: str, *, limit: int, window_seconds: int = 60
            ) -> RateLimitDecision:
                recorded.append(identity)
                del bucket, limit, window_seconds
                return RateLimitDecision(True, 10, 10, 0)

        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(certificates_api, "get_rate_limiter", Recording)
            await authenticate(api_client, mine.user)
            await api_client.get(f"{CERTIFICATES}/search", params={"q": "BC"})

        assert recorded == [str(mine.user.id)]


class _AlwaysDenied:
    """A limiter with nothing left, so the route's refusal path is what runs."""

    def __init__(self, what: str) -> None:
        self._what = what

    async def check(
        self, bucket: str, identity: str, *, limit: int, window_seconds: int = 60
    ) -> RateLimitDecision:
        del bucket, identity, limit, window_seconds
        return RateLimitDecision(allowed=False, limit=1, remaining=0, retry_after_seconds=30)


class TestLogsCarryNoPersonalData:
    """Logs leave the machine; the register does not.

    The redaction policy is tested on its own in the unit suite. What this asks is
    something the policy cannot check for itself: whether the register's own call sites
    pass a value at all.

    That distinction matters. Redaction scrubs keys it recognises and patterns it can
    match, and a person's name has no pattern - so a name passed under a key nobody
    allowlisted would be scrubbed only by luck. The invariant worth holding is therefore
    the stricter one: no call site passes a field value in the first place.

    Captured through structlog rather than through ``caplog``, because what reaches the
    stdlib handlers depends on how logging happens to be configured at the time, and
    this has to hold regardless of that.
    """

    async def test_recording_and_correcting_pass_no_values_to_the_logger(
        self, api_client: AsyncClient, mine: Office
    ) -> None:
        await authenticate(api_client, mine.user)

        with structlog.testing.capture_logs() as captured:
            created = await api_client.post(
                CERTIFICATES,
                json={
                    "certificate_type_id": mine.type_id,
                    "values": {
                        "certificate_number": "BC/LHR/2019/7777",
                        "child_full_name": "Zainab Fatima Khan",
                        "father_full_name": "Abdul Rehman Khan",
                    },
                },
            )
            assert created.status_code == 201, created.text
            patched = await api_client.patch(
                f"{CERTIFICATES}/{created.json()['id']}",
                json={
                    "values": {"child_full_name": "Zainab F Khan"},
                    "note": "Matches the scan.",
                },
            )
            assert patched.status_code == 200, patched.text

        assert captured, "the register should have logged something"
        passed = repr(captured)
        for value in ("Zainab", "Fatima", "Abdul Rehman", "Matches the scan"):
            assert value not in passed, f"{value!r} was passed to the logger"

        # Which field a reviewer changed is worth recording, and is not a value.
        events = {str(record.get("event")) for record in captured}
        assert "certificate.corrected" in events
        corrected = next(
            record for record in captured if record.get("event") == "certificate.corrected"
        )
        assert corrected["field_names"] == ["child_full_name"]
