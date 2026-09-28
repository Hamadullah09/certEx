"""Per-file PDF passwords, rejection details, and removing files before processing."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from certex.db.models import AuditLog, Batch, Document
from certex.enums import BatchStatus, DocumentStatus
from certex.pipeline.pdfprobe import probe_pdf
from tests.conftest import authenticate
from tests.fixtures.builders import (
    build_corrupt_pdf,
    build_docx,
    build_encrypted_pdf,
    build_text_pdf,
    build_zip,
)
from tests.integration.test_upload import create_batch, files_payload

pytestmark = [pytest.mark.integration]


class TestPerFilePasswords:
    async def test_password_opens_an_encrypted_pdf_and_is_never_stored(
        self, api_client: AsyncClient, db_session, operator_user, object_storage, fixture_dir: Path
    ) -> None:
        await authenticate(api_client, operator_user)
        batch_id = await create_batch(api_client)
        locked = build_encrypted_pdf(fixture_dir / "locked.pdf", password="letmein")

        response = await api_client.post(
            f"/api/v1/batches/{batch_id}/files",
            files=files_payload(locked),
            data={"passwords": json.dumps({"locked.pdf": "letmein"})},
        )
        assert response.status_code == 201, response.text
        [result] = response.json()
        assert result["status"] == DocumentStatus.QUEUED.value
        assert "letmein" not in response.text

        document = await db_session.get(Document, result["document_id"])
        assert document.is_encrypted
        assert document.page_count == 1

        # What the pipeline will read is the unencrypted copy.
        stored = fixture_dir / "stored.pdf"
        object_storage.download_to_path(document.storage_key, stored)
        assert not probe_pdf(stored).is_encrypted

        # And the secret reached no persistent row.
        batch = await db_session.get(Batch, batch_id)
        audit_rows = (await db_session.scalars(select(AuditLog))).all()
        assert "letmein" not in str(batch.settings_json)
        assert all("letmein" not in str(row.metadata_jsonb) for row in audit_rows)

    async def test_passwords_only_apply_to_their_own_file(
        self, api_client: AsyncClient, operator_user, object_storage, fixture_dir: Path
    ) -> None:
        await authenticate(api_client, operator_user)
        batch_id = await create_batch(api_client)
        first = build_encrypted_pdf(fixture_dir / "first.pdf", password="alpha")
        second = build_encrypted_pdf(fixture_dir / "second.pdf", password="bravo")

        response = await api_client.post(
            f"/api/v1/batches/{batch_id}/files",
            files=files_payload(first, second),
            data={"passwords": json.dumps({"first.pdf": "alpha"})},
        )
        statuses = {item["original_filename"]: item["status"] for item in response.json()}
        assert statuses == {"first.pdf": "QUEUED", "second.pdf": "FAILED"}

    async def test_wrong_password_fails_with_guidance(
        self, api_client: AsyncClient, operator_user, object_storage, fixture_dir: Path
    ) -> None:
        await authenticate(api_client, operator_user)
        batch_id = await create_batch(api_client)
        locked = build_encrypted_pdf(fixture_dir / "locked.pdf", password="letmein")

        response = await api_client.post(
            f"/api/v1/batches/{batch_id}/files",
            files=files_payload(locked),
            data={"passwords": json.dumps({"locked.pdf": "guess"})},
        )
        [result] = response.json()
        assert result["status"] == DocumentStatus.FAILED.value
        assert result["error_code"] == "document_encrypted"
        assert result["remediation"]
        assert "guess" not in response.text

    async def test_malformed_passwords_field_is_rejected_without_echo(
        self, api_client: AsyncClient, operator_user, object_storage, fixture_dir: Path
    ) -> None:
        await authenticate(api_client, operator_user)
        batch_id = await create_batch(api_client)
        pdf = build_text_pdf(fixture_dir / "a.pdf")

        response = await api_client.post(
            f"/api/v1/batches/{batch_id}/files",
            files=files_payload(pdf),
            data={"passwords": "{not json secret-value"},
        )
        assert response.status_code == 400
        assert "secret-value" not in response.text


class TestRejectionDetails:
    async def test_rejected_files_say_why_and_what_to_do(
        self, api_client: AsyncClient, operator_user, object_storage, fixture_dir: Path
    ) -> None:
        await authenticate(api_client, operator_user)
        batch_id = await create_batch(api_client)
        corrupt = build_corrupt_pdf(fixture_dir / "broken.pdf")

        response = await api_client.post(
            f"/api/v1/batches/{batch_id}/files", files=files_payload(corrupt)
        )
        [result] = response.json()
        assert result["status"] == DocumentStatus.FAILED.value
        assert result["error_code"] == "document_corrupt"
        assert result["error_message"]
        assert "Re-export" in result["remediation"]

    async def test_document_listing_carries_remediation(
        self, api_client: AsyncClient, operator_user, object_storage, fixture_dir: Path
    ) -> None:
        await authenticate(api_client, operator_user)
        batch_id = await create_batch(api_client)
        corrupt = build_corrupt_pdf(fixture_dir / "broken.pdf")
        await api_client.post(f"/api/v1/batches/{batch_id}/files", files=files_payload(corrupt))

        listing = (await api_client.get(f"/api/v1/batches/{batch_id}/documents")).json()
        [item] = listing["items"]
        assert item["remediation"]


class TestRemoveDocument:
    async def test_failed_upload_can_be_removed_before_retrying(
        self, api_client: AsyncClient, db_session, operator_user, object_storage, fixture_dir: Path
    ) -> None:
        await authenticate(api_client, operator_user)
        batch_id = await create_batch(api_client)
        corrupt = build_corrupt_pdf(fixture_dir / "broken.pdf")
        good = build_text_pdf(fixture_dir / "good.pdf")
        results = (
            await api_client.post(
                f"/api/v1/batches/{batch_id}/files", files=files_payload(corrupt, good)
            )
        ).json()
        failed_id = next(item["document_id"] for item in results if item["status"] == "FAILED")

        response = await api_client.delete(f"/api/v1/batches/{batch_id}/documents/{failed_id}")
        assert response.status_code == 204

        batch = await db_session.get(Batch, batch_id)
        await db_session.refresh(batch)
        assert batch.file_count == 1
        assert batch.failed_count == 0
        assert await db_session.get(Document, failed_id) is None

    async def test_removing_a_stored_file_deletes_its_blob(
        self, api_client: AsyncClient, db_session, operator_user, object_storage, fixture_dir: Path
    ) -> None:
        await authenticate(api_client, operator_user)
        batch_id = await create_batch(api_client)
        pdf = build_text_pdf(fixture_dir / "good.pdf")
        [result] = (
            await api_client.post(f"/api/v1/batches/{batch_id}/files", files=files_payload(pdf))
        ).json()
        document = await db_session.get(Document, result["document_id"])
        key = document.storage_key
        assert object_storage.object_exists(key)

        await api_client.delete(f"/api/v1/batches/{batch_id}/documents/{result['document_id']}")
        assert not object_storage.object_exists(key)

    async def test_removing_an_archive_removes_its_members(
        self, api_client: AsyncClient, db_session, operator_user, object_storage, fixture_dir: Path
    ) -> None:
        await authenticate(api_client, operator_user)
        batch_id = await create_batch(api_client)
        archive = build_zip(
            fixture_dir / "bundle.zip",
            {
                "one.pdf": build_text_pdf(fixture_dir / "one.pdf").read_bytes(),
                "two.docx": build_docx(fixture_dir / "two.docx").read_bytes(),
            },
        )
        [result] = (
            await api_client.post(f"/api/v1/batches/{batch_id}/files", files=files_payload(archive))
        ).json()
        assert len(result["children"]) == 2

        await api_client.delete(f"/api/v1/batches/{batch_id}/documents/{result['document_id']}")
        remaining = (
            await db_session.scalars(select(Document).where(Document.batch_id == batch_id))
        ).all()
        assert remaining == []

    async def test_files_cannot_be_removed_once_processing_started(
        self, api_client: AsyncClient, db_session, operator_user, object_storage, fixture_dir: Path
    ) -> None:
        await authenticate(api_client, operator_user)
        batch_id = await create_batch(api_client)
        pdf = build_text_pdf(fixture_dir / "good.pdf")
        [result] = (
            await api_client.post(f"/api/v1/batches/{batch_id}/files", files=files_payload(pdf))
        ).json()
        batch = await db_session.get(Batch, batch_id)
        batch.status = BatchStatus.PROCESSING
        await db_session.flush()

        response = await api_client.delete(
            f"/api/v1/batches/{batch_id}/documents/{result['document_id']}"
        )
        assert response.status_code == 409

    async def test_viewers_cannot_remove_files(
        self,
        api_client: AsyncClient,
        operator_user,
        viewer_user,
        object_storage,
        fixture_dir: Path,
    ) -> None:
        await authenticate(api_client, operator_user)
        batch_id = await create_batch(api_client)
        pdf = build_text_pdf(fixture_dir / "good.pdf")
        [result] = (
            await api_client.post(f"/api/v1/batches/{batch_id}/files", files=files_payload(pdf))
        ).json()
        await authenticate(api_client, viewer_user)
        response = await api_client.delete(
            f"/api/v1/batches/{batch_id}/documents/{result['document_id']}"
        )
        assert response.status_code == 403
