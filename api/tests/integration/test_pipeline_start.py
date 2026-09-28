"""Starting a batch.

Starting is the handover from upload to pipeline, and the rules that matter are about
not losing work: a half-uploaded batch cannot start, a started batch cannot start
twice, and the tasks are enqueued only after the transaction commits - otherwise a
worker could pick up a document before the batch was marked as processing.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from certex.api.v1 import batches as batches_api
from certex.db.base import utcnow
from certex.db.models import AuditLog, Batch, Document, UploadSession, User
from certex.enums import AuditAction, BatchStatus, DocumentStatus
from certex.pipeline.dispatch import TASK_TEXT_DOCUMENT, TaskArgument
from tests.conftest import authenticate

pytestmark = pytest.mark.integration


@pytest.fixture
def enqueued(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict[str, TaskArgument]]]:
    """Capture what the endpoint hands to Celery, instead of running a broker."""
    calls: list[tuple[str, dict[str, TaskArgument]]] = []

    def record(task_name: str, kwargs: dict[str, TaskArgument]) -> None:
        calls.append((task_name, kwargs))

    monkeypatch.setattr(batches_api, "enqueue", record)
    return calls


async def add_document(
    session: AsyncSession,
    batch: Batch,
    *,
    name: str = "birth.pdf",
    status: DocumentStatus = DocumentStatus.QUEUED,
) -> Document:
    document = Document(
        batch_id=batch.id,
        workspace_id=batch.workspace_id,
        original_filename=name,
        mime_type="application/pdf",
        byte_size=1024,
        sha256=uuid.uuid4().hex * 2,
        storage_key=f"documents/{batch.workspace_id}/2026/09/{uuid.uuid4()}",
        status=status,
    )
    session.add(document)
    await session.flush()
    return document


async def start(client: AsyncClient, batch: Batch) -> Any:
    return await client.post(f"/api/v1/batches/{batch.id}/start")


class TestStartingABatch:
    async def test_the_batch_moves_to_processing(
        self,
        api_client: AsyncClient,
        db_session: AsyncSession,
        operator_user: User,
        batch: Batch,
        enqueued: list[tuple[str, dict[str, TaskArgument]]],
    ) -> None:
        await add_document(db_session, batch)
        await authenticate(api_client, operator_user)

        response = await start(api_client, batch)

        assert response.status_code == 200, response.text
        assert response.json()["status"] == BatchStatus.PROCESSING.value
        await db_session.refresh(batch)
        assert batch.status is BatchStatus.PROCESSING
        assert batch.started_at is not None

    async def test_every_queued_document_is_enqueued_once(
        self,
        api_client: AsyncClient,
        db_session: AsyncSession,
        operator_user: User,
        batch: Batch,
        enqueued: list[tuple[str, dict[str, TaskArgument]]],
    ) -> None:
        first = await add_document(db_session, batch, name="a.pdf")
        second = await add_document(db_session, batch, name="b.pdf")
        await authenticate(api_client, operator_user)

        await start(api_client, batch)

        assert enqueued == [
            (TASK_TEXT_DOCUMENT, {"document_id": str(first.id)}),
            (TASK_TEXT_DOCUMENT, {"document_id": str(second.id)}),
        ]

    async def test_documents_that_need_no_processing_are_skipped(
        self,
        api_client: AsyncClient,
        db_session: AsyncSession,
        operator_user: User,
        batch: Batch,
        enqueued: list[tuple[str, dict[str, TaskArgument]]],
    ) -> None:
        queued = await add_document(db_session, batch, name="good.pdf")
        await add_document(db_session, batch, name="dupe.pdf", status=DocumentStatus.DUPLICATE)
        await add_document(db_session, batch, name="bad.pdf", status=DocumentStatus.FAILED)
        await authenticate(api_client, operator_user)

        await start(api_client, batch)

        assert enqueued == [(TASK_TEXT_DOCUMENT, {"document_id": str(queued.id)})]

    async def test_starting_is_recorded_in_the_audit_log(
        self,
        api_client: AsyncClient,
        db_session: AsyncSession,
        operator_user: User,
        batch: Batch,
        enqueued: list[tuple[str, dict[str, TaskArgument]]],
    ) -> None:
        await add_document(db_session, batch)
        await authenticate(api_client, operator_user)

        await start(api_client, batch)

        entry = await db_session.scalar(
            select(AuditLog).where(
                AuditLog.action == AuditAction.BATCH_STARTED, AuditLog.entity_id == batch.id
            )
        )
        assert entry is not None
        assert entry.user_id == operator_user.id
        assert entry.metadata_jsonb == {"count": 1}


class TestBatchesThatCannotStart:
    async def test_a_batch_with_no_files_cannot_start(
        self,
        api_client: AsyncClient,
        operator_user: User,
        batch: Batch,
        enqueued: list[tuple[str, dict[str, TaskArgument]]],
    ) -> None:
        await authenticate(api_client, operator_user)

        response = await start(api_client, batch)

        assert response.status_code == 409
        assert response.json()["code"] == "batch_not_ready"
        assert enqueued == []

    async def test_an_unfinished_upload_holds_the_batch_back(
        self,
        api_client: AsyncClient,
        db_session: AsyncSession,
        operator_user: User,
        batch: Batch,
        enqueued: list[tuple[str, dict[str, TaskArgument]]],
    ) -> None:
        await add_document(db_session, batch)
        db_session.add(
            UploadSession(
                batch_id=batch.id,
                workspace_id=batch.workspace_id,
                original_filename="half.pdf",
                declared_mime_type="application/pdf",
                declared_size=4096,
                received_bytes=1024,
                storage_key=f"uploads/{batch.workspace_id}/{uuid.uuid4()}",
                is_complete=False,
                client_file_id="half-1",
                expires_at=utcnow() + dt.timedelta(hours=1),
            )
        )
        await db_session.flush()
        await authenticate(api_client, operator_user)

        response = await start(api_client, batch)

        assert response.status_code == 409
        body = response.json()
        assert body["code"] == "batch_not_ready"
        assert "upload" in body["detail"].lower()
        assert enqueued == []

    async def test_a_batch_cannot_start_twice(
        self,
        api_client: AsyncClient,
        db_session: AsyncSession,
        operator_user: User,
        batch: Batch,
        enqueued: list[tuple[str, dict[str, TaskArgument]]],
    ) -> None:
        await add_document(db_session, batch)
        await authenticate(api_client, operator_user)

        first = await start(api_client, batch)
        second = await start(api_client, batch)

        assert first.status_code == 200
        assert second.status_code == 409
        assert second.json()["code"] == "conflict"
        assert len(enqueued) == 1, "the second call must not enqueue the work again"

    async def test_another_workspaces_batch_is_not_found(
        self,
        api_client: AsyncClient,
        db_session: AsyncSession,
        operator_user: User,
        batch: Batch,
        enqueued: list[tuple[str, dict[str, TaskArgument]]],
    ) -> None:
        from certex.db.models import Workspace

        other = Workspace(name="Another Office", settings_json={})
        db_session.add(other)
        await db_session.flush()
        stranger = Batch(
            workspace_id=other.id,
            name="Not yours",
            status=BatchStatus.CREATED,
            settings_json={},
        )
        db_session.add(stranger)
        await db_session.flush()
        await authenticate(api_client, operator_user)

        response = await start(api_client, stranger)

        assert response.status_code == 404
        assert enqueued == []
        del batch

    async def test_a_batch_of_nothing_but_failures_completes_at_once(
        self,
        api_client: AsyncClient,
        db_session: AsyncSession,
        operator_user: User,
        batch: Batch,
        enqueued: list[tuple[str, dict[str, TaskArgument]]],
    ) -> None:
        # Every file failed at upload: the batch must finish rather than sit
        # "processing" with nothing to process.
        batch.failed_count = 1
        await add_document(db_session, batch, name="bad.pdf", status=DocumentStatus.FAILED)
        await authenticate(api_client, operator_user)

        response = await start(api_client, batch)

        assert response.status_code == 200
        assert response.json()["status"] == BatchStatus.COMPLETED_WITH_ERRORS.value
        await db_session.refresh(batch)
        assert batch.completed_at is not None
        assert enqueued == []

    async def test_a_batch_of_nothing_but_duplicates_completes_at_once(
        self,
        api_client: AsyncClient,
        db_session: AsyncSession,
        operator_user: User,
        batch: Batch,
        enqueued: list[tuple[str, dict[str, TaskArgument]]],
    ) -> None:
        await add_document(db_session, batch, name="dupe.pdf", status=DocumentStatus.DUPLICATE)
        await authenticate(api_client, operator_user)

        response = await start(api_client, batch)

        assert response.status_code == 200
        assert response.json()["status"] == BatchStatus.COMPLETED.value
        assert enqueued == []


class TestPermissions:
    async def test_a_viewer_cannot_start_a_batch(
        self,
        api_client: AsyncClient,
        db_session: AsyncSession,
        viewer_user: User,
        batch: Batch,
        enqueued: list[tuple[str, dict[str, TaskArgument]]],
    ) -> None:
        await add_document(db_session, batch)
        await authenticate(api_client, viewer_user)

        response = await start(api_client, batch)

        assert response.status_code == 403
        assert response.json()["code"] == "insufficient_role"
        assert enqueued == []

    async def test_signing_in_is_required(
        self,
        api_client: AsyncClient,
        batch: Batch,
        enqueued: list[tuple[str, dict[str, TaskArgument]]],
    ) -> None:
        response = await start(api_client, batch)

        assert response.status_code == 401
        assert enqueued == []
