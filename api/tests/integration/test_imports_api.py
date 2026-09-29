"""Bulk import and register export over HTTP.

What the request itself must get right: refuse what it cannot use, keep the file out of
memory, queue the work rather than doing it, and never let one office see another's
import. The reading of the file is the worker's, and is tested against Postgres in
test_import_stage.py.
"""

from __future__ import annotations

import csv
import io
import uuid
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from certex.api.v1 import imports as imports_api
from certex.core.security import hash_password
from certex.db.models import CertificateImport, User, Workspace
from certex.enums import ImportDuplicatePolicy, ImportStatus, UserRole
from certex.pipeline.dispatch import TASK_IMPORT_CSV, TaskArgument
from tests.conftest import TEST_PASSWORD, authenticate

pytestmark = [pytest.mark.integration]

TYPES = "/api/v1/certificate-types"
IMPORTS = "/api/v1/imports"
CERTIFICATES = "/api/v1/certificates"

HEADER = "certificate_number,child_full_name,date_of_birth"
ROW = "BC/LHR/2019/1001,Ayesha Noor Malik,2019-04-03"


@pytest.fixture
def enqueued(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict[str, TaskArgument]]]:
    """Capture what the route would have queued, instead of queueing it."""
    calls: list[tuple[str, dict[str, TaskArgument]]] = []

    def record(task_name: str, kwargs: dict[str, TaskArgument]) -> None:
        calls.append((task_name, kwargs))

    monkeypatch.setattr(imports_api, "enqueue", record)
    return calls


async def birth_type_id(client: AsyncClient) -> str:
    response = await client.get(TYPES)
    assert response.status_code == 200, response.text
    return next(item["id"] for item in response.json() if item["key"] == "BIRTH")


async def upload(
    client: AsyncClient,
    *,
    type_id: str,
    text: str = f"{HEADER}\r\n{ROW}\r\n",
    filename: str = "register.csv",
    policy: str | None = None,
    delimiter: str | None = None,
    expect: int = 202,
) -> dict[str, Any]:
    data: dict[str, str] = {"certificate_type_id": type_id}
    if policy is not None:
        data["duplicate_policy"] = policy
    if delimiter is not None:
        data["delimiter"] = delimiter
    response = await client.post(
        IMPORTS,
        data=data,
        files={"file": (filename, text.encode("utf-8"), "text/csv")},
    )
    assert response.status_code == expect, response.text
    body: dict[str, Any] = response.json()
    return body


async def a_second_office(session: AsyncSession) -> User:
    workspace = Workspace(name=f"Other Office {uuid.uuid4().hex[:8]}", settings_json={})
    session.add(workspace)
    await session.flush()
    user = User(
        workspace_id=workspace.id,
        email=f"outsider-{uuid.uuid4().hex[:8]}@example.com",
        password_hash=hash_password(TEST_PASSWORD),
        role=UserRole.OPERATOR,
        is_active=True,
    )
    session.add(user)
    await session.flush()
    return user


class TestTemplate:
    async def test_it_is_generated_from_the_schema(
        self, api_client: AsyncClient, operator_user: User
    ) -> None:
        await authenticate(api_client, operator_user)
        type_id = await birth_type_id(api_client)

        response = await api_client.get(f"{TYPES}/{type_id}/import-template")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/csv")

        text = response.content.decode("utf-8-sig")
        headers = next(csv.reader(io.StringIO(text)))
        assert "certificate_number" in headers
        assert "child_full_name" in headers

    async def test_it_is_named_after_the_type(
        self, api_client: AsyncClient, operator_user: User
    ) -> None:
        await authenticate(api_client, operator_user)
        type_id = await birth_type_id(api_client)
        response = await api_client.get(f"{TYPES}/{type_id}/import-template")
        assert "birth-import-template.csv" in response.headers["content-disposition"]

    async def test_a_viewer_can_download_it(
        self, api_client: AsyncClient, admin_user: User, viewer_user: User
    ) -> None:
        """Reading what the register expects is not a privileged act."""
        await authenticate(api_client, admin_user)
        type_id = await birth_type_id(api_client)

        await authenticate(api_client, viewer_user)
        assert (await api_client.get(f"{TYPES}/{type_id}/import-template")).status_code == 200

    async def test_a_delimiter_this_system_does_not_write_is_refused(
        self, api_client: AsyncClient, operator_user: User
    ) -> None:
        await authenticate(api_client, operator_user)
        type_id = await birth_type_id(api_client)
        response = await api_client.get(
            f"{TYPES}/{type_id}/import-template", params={"delimiter": "regex"}
        )
        assert response.status_code == 400

    async def test_another_office_s_type_is_not_found(
        self, api_client: AsyncClient, operator_user: User, db_session: AsyncSession
    ) -> None:
        outsider = await a_second_office(db_session)
        await authenticate(api_client, outsider)
        theirs = await birth_type_id(api_client)

        await authenticate(api_client, operator_user)
        assert (await api_client.get(f"{TYPES}/{theirs}/import-template")).status_code == 404


class TestUploading:
    async def test_the_file_is_accepted_and_queued(
        self,
        api_client: AsyncClient,
        operator_user: User,
        enqueued: list[tuple[str, dict[str, TaskArgument]]],
    ) -> None:
        await authenticate(api_client, operator_user)
        record = await upload(api_client, type_id=await birth_type_id(api_client))

        assert record["status"] == ImportStatus.PENDING
        assert record["total_rows"] == 0, "nothing has been read yet"
        assert enqueued == [(TASK_IMPORT_CSV, {"import_id": record["id"]})]

    async def test_the_response_is_the_thing_to_poll(
        self,
        api_client: AsyncClient,
        operator_user: User,
        enqueued: list[tuple[str, dict[str, TaskArgument]]],
    ) -> None:
        """202, not 201: the work has not happened yet and the counts are not a result."""
        await authenticate(api_client, operator_user)
        record = await upload(api_client, type_id=await birth_type_id(api_client))
        assert (await api_client.get(f"{IMPORTS}/{record['id']}")).json()["id"] == record["id"]

    async def test_the_uploaded_name_is_kept_for_display_only(
        self,
        api_client: AsyncClient,
        operator_user: User,
        db_session: AsyncSession,
        enqueued: list[tuple[str, dict[str, TaskArgument]]],
    ) -> None:
        """A hostile filename must not reach the storage key."""
        await authenticate(api_client, operator_user)
        record = await upload(
            api_client,
            type_id=await birth_type_id(api_client),
            filename="../../etc/passwd\x00.csv",
        )

        stored = await db_session.get(CertificateImport, uuid.UUID(record["id"]))
        assert stored is not None
        assert stored.storage_key == f"imports/{operator_user.workspace_id}/{stored.id}.csv"
        assert ".." not in stored.storage_key

    async def test_the_file_is_hashed_and_sized(
        self,
        api_client: AsyncClient,
        operator_user: User,
        db_session: AsyncSession,
        enqueued: list[tuple[str, dict[str, TaskArgument]]],
    ) -> None:
        await authenticate(api_client, operator_user)
        text = f"{HEADER}\r\n{ROW}\r\n"
        record = await upload(api_client, type_id=await birth_type_id(api_client), text=text)

        stored = await db_session.get(CertificateImport, uuid.UUID(record["id"]))
        assert stored is not None
        assert stored.byte_size == len(text.encode())
        assert len(stored.sha256) == 64

    async def test_an_empty_file_is_refused(
        self,
        api_client: AsyncClient,
        operator_user: User,
        enqueued: list[tuple[str, dict[str, TaskArgument]]],
    ) -> None:
        await authenticate(api_client, operator_user)
        await upload(api_client, type_id=await birth_type_id(api_client), text="", expect=400)
        assert enqueued == [], "nothing should have been queued"

    async def test_a_delimiter_this_system_does_not_read_is_refused(
        self,
        api_client: AsyncClient,
        operator_user: User,
        enqueued: list[tuple[str, dict[str, TaskArgument]]],
    ) -> None:
        await authenticate(api_client, operator_user)
        await upload(
            api_client,
            type_id=await birth_type_id(api_client),
            delimiter="regex",
            expect=400,
        )

    async def test_the_duplicate_policy_is_recorded(
        self,
        api_client: AsyncClient,
        operator_user: User,
        enqueued: list[tuple[str, dict[str, TaskArgument]]],
    ) -> None:
        await authenticate(api_client, operator_user)
        record = await upload(
            api_client,
            type_id=await birth_type_id(api_client),
            policy=ImportDuplicatePolicy.RECORD_AS_DUPLICATE.value,
        )
        assert record["duplicate_policy"] == ImportDuplicatePolicy.RECORD_AS_DUPLICATE

    async def test_there_is_no_policy_that_overwrites(
        self,
        api_client: AsyncClient,
        operator_user: User,
        enqueued: list[tuple[str, dict[str, TaskArgument]]],
    ) -> None:
        """The only two options keep the existing entry. Anything else is refused."""
        await authenticate(api_client, operator_user)
        await upload(
            api_client,
            type_id=await birth_type_id(api_client),
            policy="OVERWRITE",
            expect=422,
        )

    async def test_a_viewer_cannot_import(
        self,
        api_client: AsyncClient,
        admin_user: User,
        viewer_user: User,
        enqueued: list[tuple[str, dict[str, TaskArgument]]],
    ) -> None:
        await authenticate(api_client, admin_user)
        type_id = await birth_type_id(api_client)

        await authenticate(api_client, viewer_user)
        await upload(api_client, type_id=type_id, expect=403)

    async def test_an_unknown_type_is_not_found(
        self,
        api_client: AsyncClient,
        operator_user: User,
        enqueued: list[tuple[str, dict[str, TaskArgument]]],
    ) -> None:
        await authenticate(api_client, operator_user)
        await upload(api_client, type_id=str(uuid.uuid4()), expect=404)


class TestWatching:
    async def test_imports_are_listed_newest_first(
        self,
        api_client: AsyncClient,
        operator_user: User,
        enqueued: list[tuple[str, dict[str, TaskArgument]]],
    ) -> None:
        await authenticate(api_client, operator_user)
        type_id = await birth_type_id(api_client)
        first = await upload(api_client, type_id=type_id, filename="one.csv")
        second = await upload(api_client, type_id=type_id, filename="two.csv")

        listed = (await api_client.get(IMPORTS)).json()["items"]
        assert [item["id"] for item in listed] == [second["id"], first["id"]]

    async def test_the_errors_of_a_finished_import_are_paged(
        self,
        api_client: AsyncClient,
        operator_user: User,
        db_session: AsyncSession,
        enqueued: list[tuple[str, dict[str, TaskArgument]]],
    ) -> None:
        from certex.db.models import ImportRowError

        await authenticate(api_client, operator_user)
        record = await upload(api_client, type_id=await birth_type_id(api_client))
        import_id = uuid.UUID(record["id"])
        for line in (3, 4, 5):
            db_session.add(
                ImportRowError(
                    import_id=import_id,
                    workspace_id=operator_user.workspace_id,
                    row_number=line,
                    code="missing_identifier",
                    message="certificate_number is empty.",
                    field_name="certificate_number",
                )
            )
        await db_session.flush()

        page = (await api_client.get(f"{IMPORTS}/{import_id}/errors", params={"limit": 2})).json()
        assert page["total"] == 3
        assert [item["row_number"] for item in page["items"]] == [3, 4]
        assert page["truncated"] is False

    async def test_a_pending_import_can_be_cancelled(
        self,
        api_client: AsyncClient,
        operator_user: User,
        enqueued: list[tuple[str, dict[str, TaskArgument]]],
    ) -> None:
        await authenticate(api_client, operator_user)
        record = await upload(api_client, type_id=await birth_type_id(api_client))
        response = await api_client.post(f"{IMPORTS}/{record['id']}/cancel")
        assert response.status_code == 200
        assert response.json()["status"] == ImportStatus.CANCELLED

    async def test_a_running_import_cannot_be_cancelled(
        self,
        api_client: AsyncClient,
        operator_user: User,
        db_session: AsyncSession,
        enqueued: list[tuple[str, dict[str, TaskArgument]]],
    ) -> None:
        """Rows are already in the register; pretending otherwise would be worse."""
        await authenticate(api_client, operator_user)
        record = await upload(api_client, type_id=await birth_type_id(api_client))
        stored = await db_session.get(CertificateImport, uuid.UUID(record["id"]))
        assert stored is not None
        stored.status = ImportStatus.RUNNING
        await db_session.flush()

        response = await api_client.post(f"{IMPORTS}/{record['id']}/cancel")
        assert response.status_code == 409
        assert response.json()["remediation"]

    async def test_another_office_s_import_is_not_visible(
        self,
        api_client: AsyncClient,
        operator_user: User,
        db_session: AsyncSession,
        enqueued: list[tuple[str, dict[str, TaskArgument]]],
    ) -> None:
        outsider = await a_second_office(db_session)
        await authenticate(api_client, outsider)
        theirs = await upload(api_client, type_id=await birth_type_id(api_client))

        await authenticate(api_client, operator_user)
        assert (await api_client.get(f"{IMPORTS}/{theirs['id']}")).status_code == 404
        assert (await api_client.get(f"{IMPORTS}/{theirs['id']}/errors")).status_code == 404
        listed = (await api_client.get(IMPORTS)).json()["items"]
        assert theirs["id"] not in {item["id"] for item in listed}


class TestRegisterExport:
    async def test_the_columns_are_the_schema_s_own(
        self, api_client: AsyncClient, operator_user: User
    ) -> None:
        await authenticate(api_client, operator_user)
        type_id = await birth_type_id(api_client)

        response = await api_client.get(
            CERTIFICATES + "/export", params={"certificate_type_id": type_id}
        )
        assert response.status_code == 200
        headers = next(csv.reader(io.StringIO(response.content.decode("utf-8-sig"))))
        assert "Certificate number" in headers

    async def test_it_holds_the_entries(self, api_client: AsyncClient, operator_user: User) -> None:
        await authenticate(api_client, operator_user)
        type_id = await birth_type_id(api_client)
        await api_client.post(
            CERTIFICATES,
            json={
                "certificate_type_id": type_id,
                "values": {
                    "certificate_number": "BC/LHR/2019/9001",
                    "child_full_name": "Ayesha Noor Malik",
                    "date_of_birth": "2019-04-03",
                },
            },
        )

        response = await api_client.get(
            CERTIFICATES + "/export", params={"certificate_type_id": type_id}
        )
        rows = list(csv.DictReader(io.StringIO(response.content.decode("utf-8-sig"))))
        assert [row["Certificate number"] for row in rows] == ["BC/LHR/2019/9001"]
        assert rows[0]["Name of child"] == "Ayesha Noor Malik"

    async def test_an_empty_register_still_gives_a_usable_file(
        self, api_client: AsyncClient, operator_user: User
    ) -> None:
        """A header and no rows, which is what a spreadsheet expects."""
        await authenticate(api_client, operator_user)
        type_id = await birth_type_id(api_client)
        response = await api_client.get(
            CERTIFICATES + "/export", params={"certificate_type_id": type_id}
        )
        text = response.content.decode("utf-8-sig")
        assert text.strip()
        assert list(csv.DictReader(io.StringIO(text))) == []

    async def test_the_byte_order_mark_can_be_turned_off(
        self, api_client: AsyncClient, operator_user: User
    ) -> None:
        await authenticate(api_client, operator_user)
        type_id = await birth_type_id(api_client)
        response = await api_client.get(
            CERTIFICATES + "/export",
            params={"certificate_type_id": type_id, "include_bom": "false"},
        )
        assert not response.content.startswith(b"\xef\xbb\xbf")

    async def test_a_viewer_can_export(
        self, api_client: AsyncClient, admin_user: User, viewer_user: User
    ) -> None:
        await authenticate(api_client, admin_user)
        type_id = await birth_type_id(api_client)

        await authenticate(api_client, viewer_user)
        response = await api_client.get(
            CERTIFICATES + "/export", params={"certificate_type_id": type_id}
        )
        assert response.status_code == 200

    async def test_another_office_s_register_cannot_be_exported(
        self, api_client: AsyncClient, operator_user: User, db_session: AsyncSession
    ) -> None:
        outsider = await a_second_office(db_session)
        await authenticate(api_client, outsider)
        theirs = await birth_type_id(api_client)

        await authenticate(api_client, operator_user)
        response = await api_client.get(
            CERTIFICATES + "/export", params={"certificate_type_id": theirs}
        )
        assert response.status_code == 404

    async def test_the_export_is_audited(
        self, api_client: AsyncClient, operator_user: User, db_session: AsyncSession
    ) -> None:
        from certex.db.models import AuditLog
        from certex.enums import AuditAction

        await authenticate(api_client, operator_user)
        type_id = await birth_type_id(api_client)
        await api_client.get(CERTIFICATES + "/export", params={"certificate_type_id": type_id})

        actions = list(
            (
                await db_session.scalars(
                    select(AuditLog.action).where(AuditLog.action == AuditAction.EXPORT_REQUESTED)
                )
            ).all()
        )
        assert actions == [AuditAction.EXPORT_REQUESTED]
