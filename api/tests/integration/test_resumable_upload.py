"""Resumable chunked uploads, against real Postgres and real S3-compatible storage.

The large fixture is a genuine multi-megabyte PDF, so these tests exercise real
multipart parts - including the 5 MiB minimum part size S3 enforces - rather than
a single tiny request that would pass whether or not chunking works.
"""

from __future__ import annotations

import hashlib
import uuid
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from certex.db.models import Document, UploadSession
from certex.enums import DocumentStatus
from certex.pipeline.pdfprobe import probe_pdf
from tests.conftest import authenticate
from tests.fixtures.builders import build_encrypted_pdf, build_large_pdf, build_text_pdf

pytestmark = [pytest.mark.integration]


@pytest.fixture(scope="session")
def large_pdf(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Built once: two 8 MiB chunks' worth of real PDF."""
    return build_large_pdf(tmp_path_factory.mktemp("large") / "bulky-scan.pdf", megabytes=12)


async def create_batch(client: AsyncClient) -> str:
    response = await client.post("/api/v1/batches", json={"name": "Chunked", "settings": {}})
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


async def open_upload(
    client: AsyncClient, batch_id: str, path: Path, *, client_file_id: str | None = None
) -> dict[str, object]:
    response = await client.post(
        f"/api/v1/batches/{batch_id}/uploads",
        json={
            "client_file_id": client_file_id or f"file-{uuid.uuid4().hex}",
            "filename": path.name,
            "size": path.stat().st_size,
            "content_type": "application/pdf",
        },
    )
    assert response.status_code == 201, response.text
    body: dict[str, object] = response.json()
    return body


async def put_chunk(
    client: AsyncClient, batch_id: str, upload_id: str, payload: bytes, offset: int
) -> object:
    return await client.put(
        f"/api/v1/batches/{batch_id}/uploads/{upload_id}/chunks/{offset}",
        content=payload,
        headers={"Content-Type": "application/octet-stream"},
    )


async def send_all_chunks(
    client: AsyncClient, batch_id: str, state: dict[str, object], data: bytes
) -> dict[str, object]:
    size = int(str(state["chunk_size"]))
    offset = int(str(state["received_bytes"]))
    while offset < len(data):
        response = await put_chunk(
            client, batch_id, str(state["upload_id"]), data[offset : offset + size], offset
        )
        assert response.status_code == 200, response.text  # type: ignore[attr-defined]
        state = response.json()  # type: ignore[attr-defined]
        offset = int(str(state["received_bytes"]))
    return state


class TestChunkedRoundTrip:
    async def test_multi_part_upload_becomes_a_stored_document(
        self,
        api_client: AsyncClient,
        db_session,
        operator_user,
        object_storage,
        large_pdf: Path,
    ) -> None:
        await authenticate(api_client, operator_user)
        batch_id = await create_batch(api_client)
        data = large_pdf.read_bytes()

        state = await open_upload(api_client, batch_id, large_pdf)
        assert state["received_bytes"] == 0
        assert int(str(state["chunk_size"])) >= 5 * 1024 * 1024
        assert len(data) > int(str(state["chunk_size"])), "fixture must need several parts"

        state = await send_all_chunks(api_client, batch_id, state, data)
        assert state["received_bytes"] == len(data)

        response = await api_client.post(
            f"/api/v1/batches/{batch_id}/uploads/{state['upload_id']}/complete", json={}
        )
        assert response.status_code == 201, response.text
        result = response.json()
        assert result["status"] == DocumentStatus.QUEUED.value
        assert result["sha256"] == hashlib.sha256(data).hexdigest()
        assert result["byte_size"] == len(data)
        assert result["mime_type"] == "application/pdf"

        document = await db_session.get(Document, result["document_id"])
        assert document is not None
        assert document.page_count == 2
        assert object_storage.object_exists(document.storage_key)

        # The staging object is gone: only the document copy holds the bytes.
        upload = await db_session.get(UploadSession, uuid.UUID(str(state["upload_id"])))
        assert upload is not None and upload.is_complete
        assert not object_storage.object_exists(upload.storage_key)

    async def test_stored_bytes_match_what_was_sent(
        self, api_client: AsyncClient, db_session, operator_user, object_storage, large_pdf: Path
    ) -> None:
        await authenticate(api_client, operator_user)
        batch_id = await create_batch(api_client)
        data = large_pdf.read_bytes()
        state = await send_all_chunks(
            api_client, batch_id, await open_upload(api_client, batch_id, large_pdf), data
        )
        result = (
            await api_client.post(
                f"/api/v1/batches/{batch_id}/uploads/{state['upload_id']}/complete", json={}
            )
        ).json()

        document = await db_session.get(Document, result["document_id"])
        body = object_storage.download_stream(document.storage_key)
        try:
            digest = hashlib.sha256()
            for block in iter(lambda: body.read(1024 * 1024), b""):
                digest.update(block)
        finally:
            body.close()
        assert digest.hexdigest() == hashlib.sha256(data).hexdigest()


class TestResume:
    async def test_reannouncing_the_same_file_resumes_where_it_stopped(
        self, api_client: AsyncClient, operator_user, object_storage, large_pdf: Path
    ) -> None:
        await authenticate(api_client, operator_user)
        batch_id = await create_batch(api_client)
        data = large_pdf.read_bytes()

        first = await open_upload(api_client, batch_id, large_pdf, client_file_id="scan-001")
        size = int(str(first["chunk_size"]))
        sent = await put_chunk(api_client, batch_id, str(first["upload_id"]), data[:size], 0)
        assert sent.status_code == 200  # type: ignore[attr-defined]

        # The browser "crashes" and comes back.
        resumed = await open_upload(api_client, batch_id, large_pdf, client_file_id="scan-001")
        assert resumed["upload_id"] == first["upload_id"]
        assert resumed["received_bytes"] == size

        state = await send_all_chunks(api_client, batch_id, resumed, data)
        completed = await api_client.post(
            f"/api/v1/batches/{batch_id}/uploads/{state['upload_id']}/complete", json={}
        )
        assert completed.status_code == 201
        assert completed.json()["sha256"] == hashlib.sha256(data).hexdigest()

    async def test_resending_a_received_chunk_is_harmless(
        self, api_client: AsyncClient, operator_user, object_storage, large_pdf: Path
    ) -> None:
        await authenticate(api_client, operator_user)
        batch_id = await create_batch(api_client)
        data = large_pdf.read_bytes()
        state = await open_upload(api_client, batch_id, large_pdf)
        size = int(str(state["chunk_size"]))

        for _ in range(2):
            response = await put_chunk(
                api_client, batch_id, str(state["upload_id"]), data[:size], 0
            )
            assert response.status_code == 200  # type: ignore[attr-defined]
            assert response.json()["received_bytes"] == size  # type: ignore[attr-defined]

    async def test_status_can_be_polled(
        self, api_client: AsyncClient, operator_user, object_storage, large_pdf: Path
    ) -> None:
        await authenticate(api_client, operator_user)
        batch_id = await create_batch(api_client)
        state = await open_upload(api_client, batch_id, large_pdf)
        response = await api_client.get(f"/api/v1/batches/{batch_id}/uploads/{state['upload_id']}")
        assert response.status_code == 200
        assert response.json()["declared_size"] == large_pdf.stat().st_size

    async def test_expired_upload_starts_over(
        self, api_client: AsyncClient, db_session, operator_user, object_storage, large_pdf: Path
    ) -> None:
        import datetime as dt

        await authenticate(api_client, operator_user)
        batch_id = await create_batch(api_client)
        first = await open_upload(api_client, batch_id, large_pdf, client_file_id="stale")

        upload = await db_session.get(UploadSession, uuid.UUID(str(first["upload_id"])))
        upload.expires_at = dt.datetime.now(dt.UTC) - dt.timedelta(minutes=1)
        await db_session.flush()

        again = await open_upload(api_client, batch_id, large_pdf, client_file_id="stale")
        assert again["upload_id"] != first["upload_id"]
        assert again["received_bytes"] == 0


class TestProtocolErrors:
    async def test_out_of_order_chunk_is_refused(
        self, api_client: AsyncClient, operator_user, object_storage, large_pdf: Path
    ) -> None:
        await authenticate(api_client, operator_user)
        batch_id = await create_batch(api_client)
        data = large_pdf.read_bytes()
        state = await open_upload(api_client, batch_id, large_pdf)
        size = int(str(state["chunk_size"]))

        response = await put_chunk(
            api_client, batch_id, str(state["upload_id"]), data[size : 2 * size], size
        )
        assert response.status_code == 409  # type: ignore[attr-defined]
        assert "offset 0" in response.json()["remediation"]  # type: ignore[attr-defined]

    async def test_wrong_chunk_length_is_refused(
        self, api_client: AsyncClient, operator_user, object_storage, large_pdf: Path
    ) -> None:
        await authenticate(api_client, operator_user)
        batch_id = await create_batch(api_client)
        state = await open_upload(api_client, batch_id, large_pdf)
        response = await put_chunk(api_client, batch_id, str(state["upload_id"]), b"x" * 10, 0)
        assert response.status_code == 400  # type: ignore[attr-defined]

    async def test_oversized_chunk_is_refused(
        self, api_client: AsyncClient, operator_user, object_storage, large_pdf: Path
    ) -> None:
        await authenticate(api_client, operator_user)
        batch_id = await create_batch(api_client)
        state = await open_upload(api_client, batch_id, large_pdf)
        size = int(str(state["chunk_size"]))
        response = await put_chunk(
            api_client, batch_id, str(state["upload_id"]), b"x" * (size + 1), 0
        )
        assert response.status_code == 413  # type: ignore[attr-defined]

    async def test_completing_early_reports_what_is_missing(
        self, api_client: AsyncClient, operator_user, object_storage, large_pdf: Path
    ) -> None:
        await authenticate(api_client, operator_user)
        batch_id = await create_batch(api_client)
        state = await open_upload(api_client, batch_id, large_pdf)
        response = await api_client.post(
            f"/api/v1/batches/{batch_id}/uploads/{state['upload_id']}/complete", json={}
        )
        assert response.status_code == 409
        assert response.json()["code"] == "upload_incomplete"

    async def test_completing_twice_returns_the_same_document(
        self, api_client: AsyncClient, operator_user, object_storage, fixture_dir: Path
    ) -> None:
        await authenticate(api_client, operator_user)
        batch_id = await create_batch(api_client)
        pdf = build_text_pdf(fixture_dir / "small.pdf")
        state = await send_all_chunks(
            api_client, batch_id, await open_upload(api_client, batch_id, pdf), pdf.read_bytes()
        )
        url = f"/api/v1/batches/{batch_id}/uploads/{state['upload_id']}/complete"
        first = await api_client.post(url, json={})
        second = await api_client.post(url, json={})
        assert first.status_code == second.status_code == 201
        assert first.json()["document_id"] == second.json()["document_id"]

    async def test_cancelled_upload_is_gone(
        self, api_client: AsyncClient, db_session, operator_user, object_storage, large_pdf: Path
    ) -> None:
        await authenticate(api_client, operator_user)
        batch_id = await create_batch(api_client)
        state = await open_upload(api_client, batch_id, large_pdf)
        url = f"/api/v1/batches/{batch_id}/uploads/{state['upload_id']}"

        assert (await api_client.delete(url)).status_code == 204
        assert (await api_client.get(url)).status_code == 404
        remaining = await db_session.scalar(
            select(UploadSession).where(UploadSession.id == uuid.UUID(str(state["upload_id"])))
        )
        assert remaining is None

    async def test_viewer_cannot_upload(
        self, api_client: AsyncClient, operator_user, viewer_user, object_storage, fixture_dir: Path
    ) -> None:
        await authenticate(api_client, operator_user)
        batch_id = await create_batch(api_client)
        await authenticate(api_client, viewer_user)
        pdf = build_text_pdf(fixture_dir / "small.pdf")
        response = await api_client.post(
            f"/api/v1/batches/{batch_id}/uploads",
            json={"client_file_id": "v-1", "filename": pdf.name, "size": pdf.stat().st_size},
        )
        assert response.status_code == 403


class TestEncryptedViaChunks:
    async def test_password_opens_the_file_and_is_not_kept(
        self, api_client: AsyncClient, db_session, operator_user, object_storage, fixture_dir: Path
    ) -> None:
        await authenticate(api_client, operator_user)
        batch_id = await create_batch(api_client)
        locked = build_encrypted_pdf(fixture_dir / "locked.pdf", password="letmein")
        state = await send_all_chunks(
            api_client,
            batch_id,
            await open_upload(api_client, batch_id, locked),
            locked.read_bytes(),
        )

        response = await api_client.post(
            f"/api/v1/batches/{batch_id}/uploads/{state['upload_id']}/complete",
            json={"password": "letmein"},
        )
        assert response.status_code == 201, response.text
        assert response.json()["status"] == DocumentStatus.QUEUED.value
        assert "letmein" not in response.text

        document = await db_session.get(Document, response.json()["document_id"])
        assert document.is_encrypted, "the original's encryption is still recorded"

        stored = fixture_dir / "stored.pdf"
        object_storage.download_to_path(document.storage_key, stored)
        assert not probe_pdf(stored).is_encrypted

    async def test_wrong_password_records_a_clear_failure(
        self, api_client: AsyncClient, operator_user, object_storage, fixture_dir: Path
    ) -> None:
        await authenticate(api_client, operator_user)
        batch_id = await create_batch(api_client)
        locked = build_encrypted_pdf(fixture_dir / "locked.pdf", password="letmein")
        state = await send_all_chunks(
            api_client,
            batch_id,
            await open_upload(api_client, batch_id, locked),
            locked.read_bytes(),
        )
        response = await api_client.post(
            f"/api/v1/batches/{batch_id}/uploads/{state['upload_id']}/complete",
            json={"password": "not-it"},
        )
        body = response.json()
        assert body["status"] == DocumentStatus.FAILED.value
        assert body["error_code"] == "document_encrypted"
        assert body["remediation"]
