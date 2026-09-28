"""Batch lifecycle: create, list, read, delete, and the tenancy boundary."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select

from certex.db.models import AuditLog, Batch, Document, User, Workspace
from certex.enums import AuditAction, BatchStatus, UserRole
from tests.conftest import TEST_PASSWORD, authenticate
from tests.fixtures.builders import build_text_pdf

pytestmark = [pytest.mark.integration]

BATCHES = "/api/v1/batches"


class TestCreate:
    async def test_operator_can_create(self, api_client: AsyncClient, operator_user: User) -> None:
        await authenticate(api_client, operator_user)
        response = await api_client.post(
            BATCHES, json={"name": "  Records   2019  ", "settings": {}}
        )
        assert response.status_code == 201

        body = response.json()
        assert body["name"] == "Records 2019", "whitespace should be normalised"
        assert body["status"] == BatchStatus.CREATED.value
        assert body["file_count"] == 0

    async def test_settings_are_persisted(
        self, api_client: AsyncClient, operator_user: User
    ) -> None:
        await authenticate(api_client, operator_user)
        response = await api_client.post(
            BATCHES,
            json={
                "name": "Urdu scans",
                "settings": {
                    "ocr_languages": "eng+urd",
                    "expected_types": ["BIRTH", "DEATH"],
                    "confidence_auto_approve": 0.95,
                },
            },
        )
        settings = response.json()["settings"]
        assert settings["ocr_languages"] == "eng+urd"
        assert settings["expected_types"] == ["BIRTH", "DEATH"]
        assert settings["confidence_auto_approve"] == 0.95

    async def test_passwords_are_not_a_batch_setting(
        self, api_client: AsyncClient, operator_user: User
    ) -> None:
        """Passwords travel with the file they open, never on the batch."""
        await authenticate(api_client, operator_user)
        response = await api_client.post(
            BATCHES,
            json={"name": "Locked scans", "settings": {"password": "super-secret-value"}},
        )
        assert response.status_code == 422
        assert "super-secret-value" not in response.text

    async def test_retired_settings_keys_are_tolerated(
        self, api_client: AsyncClient, db_session, operator_user: User
    ) -> None:
        """Rows written before the LLM switches were retired still load their thresholds."""
        await authenticate(api_client, operator_user)
        created = await api_client.post(
            BATCHES, json={"name": "Legacy", "settings": {"confidence_auto_approve": 0.93}}
        )
        batch = await db_session.get(Batch, created.json()["id"])
        batch.settings_json = {
            "confidence_auto_approve": 0.93,
            "llm_enabled": True,
            "allow_vision": False,
        }
        await db_session.flush()

        response = await api_client.get(f"{BATCHES}/{batch.id}")
        assert response.status_code == 200
        assert response.json()["settings"]["confidence_auto_approve"] == 0.93

    async def test_viewer_cannot_create(self, api_client: AsyncClient, viewer_user: User) -> None:
        await authenticate(api_client, viewer_user)
        response = await api_client.post(BATCHES, json={"name": "Nope", "settings": {}})
        assert response.status_code == 403
        assert response.json()["code"] == "insufficient_role"

    async def test_rejects_empty_name(self, api_client: AsyncClient, operator_user: User) -> None:
        await authenticate(api_client, operator_user)
        response = await api_client.post(BATCHES, json={"name": "   ", "settings": {}})
        assert response.status_code == 422

    async def test_rejects_incoherent_thresholds(
        self, api_client: AsyncClient, operator_user: User
    ) -> None:
        await authenticate(api_client, operator_user)
        response = await api_client.post(
            BATCHES,
            json={
                "name": "Bad thresholds",
                "settings": {"confidence_review_floor": 0.95, "confidence_auto_approve": 0.5},
            },
        )
        assert response.status_code == 422

    async def test_rejects_bad_language_code(
        self, api_client: AsyncClient, operator_user: User
    ) -> None:
        await authenticate(api_client, operator_user)
        response = await api_client.post(
            BATCHES, json={"name": "Bad lang", "settings": {"ocr_languages": "eng+12"}}
        )
        assert response.status_code == 422

    async def test_creation_is_audited(
        self, api_client: AsyncClient, db_session, operator_user: User
    ) -> None:
        await authenticate(api_client, operator_user)
        await api_client.post(BATCHES, json={"name": "Audited", "settings": {}})

        count = await db_session.scalar(
            select(func.count())
            .select_from(AuditLog)
            .where(AuditLog.action == AuditAction.BATCH_CREATED)
        )
        assert count == 1


class TestList:
    async def test_newest_first(self, api_client: AsyncClient, operator_user: User) -> None:
        await authenticate(api_client, operator_user)
        for name in ("first", "second", "third"):
            await api_client.post(BATCHES, json={"name": name, "settings": {}})

        body = (await api_client.get(BATCHES)).json()
        assert [item["name"] for item in body["items"]] == ["third", "second", "first"]

    async def test_cursor_pagination_covers_every_row_once(
        self, api_client: AsyncClient, operator_user: User
    ) -> None:
        await authenticate(api_client, operator_user)
        for index in range(12):
            await api_client.post(BATCHES, json={"name": f"batch-{index:02d}", "settings": {}})

        seen: list[str] = []
        cursor: str | None = None
        for _ in range(10):
            url = f"{BATCHES}?limit=5" + (f"&cursor={cursor}" if cursor else "")
            body = (await api_client.get(url)).json()
            seen.extend(item["name"] for item in body["items"])
            cursor = body["meta"]["next_cursor"]
            if not cursor:
                break

        assert len(seen) == 12
        assert len(set(seen)) == 12, "a page boundary duplicated or dropped a row"

    async def test_filter_by_status(self, api_client: AsyncClient, operator_user: User) -> None:
        await authenticate(api_client, operator_user)
        await api_client.post(BATCHES, json={"name": "one", "settings": {}})

        body = (await api_client.get(f"{BATCHES}?filter[status]=CREATED")).json()
        assert len(body["items"]) == 1
        body = (await api_client.get(f"{BATCHES}?filter[status]=COMPLETED")).json()
        assert body["items"] == []

    async def test_search_by_name(self, api_client: AsyncClient, operator_user: User) -> None:
        await authenticate(api_client, operator_user)
        await api_client.post(BATCHES, json={"name": "Lahore 2019", "settings": {}})
        await api_client.post(BATCHES, json={"name": "Karachi 2020", "settings": {}})

        body = (await api_client.get(f"{BATCHES}?search=lahore")).json()
        assert [item["name"] for item in body["items"]] == ["Lahore 2019"]

    async def test_total_is_optional(self, api_client: AsyncClient, operator_user: User) -> None:
        await authenticate(api_client, operator_user)
        await api_client.post(BATCHES, json={"name": "x", "settings": {}})

        assert (await api_client.get(BATCHES)).json()["meta"]["total"] is None
        assert (await api_client.get(f"{BATCHES}?include_total=true")).json()["meta"]["total"] == 1

    async def test_bad_cursor_is_rejected_clearly(
        self, api_client: AsyncClient, operator_user: User
    ) -> None:
        await authenticate(api_client, operator_user)
        response = await api_client.get(f"{BATCHES}?cursor=not-a-real-cursor")
        assert response.status_code == 400
        assert "cursor" in response.json()["remediation"].lower()

    async def test_viewer_may_read(self, api_client: AsyncClient, viewer_user: User) -> None:
        await authenticate(api_client, viewer_user)
        assert (await api_client.get(BATCHES)).status_code == 200


class TestWorkspaceIsolation:
    async def test_another_workspaces_batch_is_not_found(
        self, api_client: AsyncClient, db_session, operator_user: User
    ) -> None:
        """404, not 403: a 403 would confirm the id exists somewhere."""
        other_workspace = Workspace(name="Other Office", settings_json={})
        db_session.add(other_workspace)
        await db_session.flush()

        foreign = Batch(
            workspace_id=other_workspace.id,
            name="Not yours",
            status=BatchStatus.CREATED,
            settings_json={},
        )
        db_session.add(foreign)
        await db_session.flush()

        await authenticate(api_client, operator_user)
        response = await api_client.get(f"{BATCHES}/{foreign.id}")
        assert response.status_code == 404
        assert response.json()["code"] == "not_found"

    async def test_list_excludes_other_workspaces(
        self, api_client: AsyncClient, db_session, operator_user: User
    ) -> None:
        other_workspace = Workspace(name="Other Office", settings_json={})
        db_session.add(other_workspace)
        await db_session.flush()
        db_session.add(
            Batch(
                workspace_id=other_workspace.id,
                name="Invisible",
                status=BatchStatus.CREATED,
                settings_json={},
            )
        )
        await db_session.flush()

        await authenticate(api_client, operator_user)
        await api_client.post(BATCHES, json={"name": "Mine", "settings": {}})

        body = (await api_client.get(BATCHES)).json()
        assert [item["name"] for item in body["items"]] == ["Mine"]

    async def test_cannot_upload_into_another_workspaces_batch(
        self, api_client: AsyncClient, db_session, operator_user: User, fixture_dir: Path
    ) -> None:
        other_workspace = Workspace(name="Other Office", settings_json={})
        db_session.add(other_workspace)
        await db_session.flush()
        foreign = Batch(
            workspace_id=other_workspace.id,
            name="Not yours",
            status=BatchStatus.CREATED,
            settings_json={},
        )
        db_session.add(foreign)
        await db_session.flush()

        await authenticate(api_client, operator_user)
        pdf = build_text_pdf(fixture_dir / "a.pdf")
        response = await api_client.post(
            f"{BATCHES}/{foreign.id}/files",
            files=[("files", ("a.pdf", pdf.read_bytes(), "application/pdf"))],
        )
        assert response.status_code == 404


class TestDocumentsListing:
    async def test_upload_order_is_preserved(
        self, api_client: AsyncClient, operator_user: User, object_storage, fixture_dir: Path
    ) -> None:
        await authenticate(api_client, operator_user)
        batch_id = (
            await api_client.post(BATCHES, json={"name": "Ordered", "settings": {}})
        ).json()["id"]

        names = ["first.pdf", "second.pdf", "third.pdf"]
        payload = [
            (
                "files",
                (name, build_text_pdf(fixture_dir / name, key).read_bytes(), "application/pdf"),
            )
            for name, key in zip(
                names, ["birth_lahore", "death_karachi", "marriage_islamabad"], strict=True
            )
        ]
        await api_client.post(f"{BATCHES}/{batch_id}/files", files=payload)

        body = (await api_client.get(f"{BATCHES}/{batch_id}/documents")).json()
        assert [item["original_filename"] for item in body["items"]] == names


class TestDelete:
    async def test_admin_deletes_batch_documents_and_blobs(
        self,
        api_client: AsyncClient,
        db_session,
        admin_user: User,
        object_storage,
        fixture_dir: Path,
    ) -> None:
        await authenticate(api_client, admin_user)
        batch_id = (await api_client.post(BATCHES, json={"name": "Doomed", "settings": {}})).json()[
            "id"
        ]

        pdf = build_text_pdf(fixture_dir / "a.pdf")
        upload = await api_client.post(
            f"{BATCHES}/{batch_id}/files",
            files=[("files", ("a.pdf", pdf.read_bytes(), "application/pdf"))],
        )
        document = await db_session.get(Document, upload.json()[0]["document_id"])
        key = document.storage_key
        assert object_storage.object_exists(key)

        response = await api_client.delete(f"{BATCHES}/{batch_id}")
        assert response.status_code == 204

        assert await db_session.get(Batch, batch_id) is None
        remaining = await db_session.scalar(
            select(func.count()).select_from(Document).where(Document.batch_id == batch_id)
        )
        assert remaining == 0
        assert not object_storage.object_exists(key), "the blob outlived its document"

    async def test_operator_cannot_delete(
        self, api_client: AsyncClient, operator_user: User
    ) -> None:
        await authenticate(api_client, operator_user)
        batch_id = (await api_client.post(BATCHES, json={"name": "Safe", "settings": {}})).json()[
            "id"
        ]

        response = await api_client.delete(f"{BATCHES}/{batch_id}")
        assert response.status_code == 403

    async def test_deleting_a_missing_batch_is_a_404(
        self, api_client: AsyncClient, admin_user: User
    ) -> None:
        await authenticate(api_client, admin_user)
        response = await api_client.delete(f"{BATCHES}/{uuid.uuid4()}")
        assert response.status_code == 404

    async def test_deletion_is_audited(
        self, api_client: AsyncClient, db_session, admin_user: User
    ) -> None:
        await authenticate(api_client, admin_user)
        batch_id = (await api_client.post(BATCHES, json={"name": "Doomed", "settings": {}})).json()[
            "id"
        ]
        await api_client.delete(f"{BATCHES}/{batch_id}")

        count = await db_session.scalar(
            select(func.count())
            .select_from(AuditLog)
            .where(AuditLog.action == AuditAction.BATCH_DELETED)
        )
        assert count == 1


class TestRoleMatrix:
    @pytest.mark.parametrize(
        ("role", "can_create", "can_delete"),
        [
            (UserRole.VIEWER, False, False),
            (UserRole.OPERATOR, True, False),
            (UserRole.ADMIN, True, True),
        ],
    )
    async def test_role_permissions(
        self,
        api_client: AsyncClient,
        db_session,
        workspace: Workspace,
        role: UserRole,
        can_create: bool,
        can_delete: bool,
    ) -> None:
        from certex.core.security import hash_password

        user = User(
            workspace_id=workspace.id,
            email=f"{role.value.lower()}-matrix@example.com",
            password_hash=hash_password(TEST_PASSWORD),
            role=role,
            is_active=True,
        )
        db_session.add(user)
        await db_session.flush()

        await authenticate(api_client, user)
        created = await api_client.post(BATCHES, json={"name": "Matrix", "settings": {}})
        assert (created.status_code == 201) is can_create

        if not can_create:
            return

        deleted = await api_client.delete(f"{BATCHES}/{created.json()['id']}")
        assert (deleted.status_code == 204) is can_delete
