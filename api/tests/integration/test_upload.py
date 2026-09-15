"""Upload and ingest, end to end against a real database and real object storage.

Every document here is a genuinely generated file, so the assertions are about
what the pipeline does with real bytes: the digest it computes, the type it
detects from content, the duplicate it recognises, and the specific error it
reports for a file it cannot read.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from certex.db.models import Batch, Document
from certex.enums import BatchStatus, DocumentStatus
from tests.conftest import authenticate
from tests.fixtures.builders import (
    build_corrupt_pdf,
    build_docx,
    build_encrypted_pdf,
    build_image,
    build_multi_certificate_pdf,
    build_text_pdf,
    build_zero_page_pdf,
    build_zip,
)

pytestmark = [pytest.mark.integration]


def files_payload(*paths: Path) -> list[tuple[str, tuple[str, bytes, str]]]:
    """Build a multipart payload. Content type is deliberately generic.

    The server must not believe a declared content type, so the tests send
    ``application/octet-stream`` for everything and let detection do the work.
    """
    return [("files", (path.name, path.read_bytes(), "application/octet-stream")) for path in paths]


async def create_batch(client: AsyncClient, name: str = "Records 2019") -> str:
    response = await client.post("/api/v1/batches", json={"name": name, "settings": {}})
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


class TestUploadHappyPath:
    async def test_single_pdf_is_stored_and_hashed(
        self,
        api_client: AsyncClient,
        db_session,
        operator_user,
        object_storage,
        fixture_dir: Path,
    ) -> None:
        await authenticate(api_client, operator_user)
        batch_id = await create_batch(api_client)
        pdf = build_text_pdf(fixture_dir / "birth.pdf")

        response = await api_client.post(
            f"/api/v1/batches/{batch_id}/files", files=files_payload(pdf)
        )
        assert response.status_code == 201, response.text

        [result] = response.json()
        assert result["status"] == DocumentStatus.QUEUED.value
        assert result["mime_type"] == "application/pdf"
        assert result["byte_size"] == pdf.stat().st_size
        assert result["sha256"] == hashlib.sha256(pdf.read_bytes()).hexdigest()
        assert result["is_duplicate"] is False

        document = await db_session.get(Document, result["document_id"])
        assert document is not None
        assert document.page_count == 1
        # The storage key must be UUID-derived, never the filename.
        assert "birth" not in document.storage_key
        assert str(document.id) in document.storage_key
        assert object_storage.object_exists(document.storage_key)

    async def test_page_count_recorded_for_multi_page_pdf(
        self, api_client: AsyncClient, db_session, operator_user, object_storage, fixture_dir: Path
    ) -> None:
        await authenticate(api_client, operator_user)
        batch_id = await create_batch(api_client)
        pdf = build_multi_certificate_pdf(fixture_dir / "many.pdf", copies=25)

        response = await api_client.post(
            f"/api/v1/batches/{batch_id}/files", files=files_payload(pdf)
        )
        document = await db_session.get(Document, response.json()[0]["document_id"])
        assert document.page_count == 25

    async def test_mixed_batch_of_types(
        self, api_client: AsyncClient, operator_user, object_storage, fixture_dir: Path
    ) -> None:
        await authenticate(api_client, operator_user)
        batch_id = await create_batch(api_client)

        paths = [
            build_text_pdf(fixture_dir / "a.pdf"),
            build_docx(fixture_dir / "b.docx"),
            build_image(fixture_dir / "c.png"),
        ]
        response = await api_client.post(
            f"/api/v1/batches/{batch_id}/files", files=files_payload(*paths)
        )
        assert response.status_code == 201
        mimes = {item["mime_type"] for item in response.json()}
        assert "application/pdf" in mimes
        assert "image/png" in mimes
        assert any("wordprocessingml" in mime for mime in mimes)

    async def test_batch_counters_update(
        self, api_client: AsyncClient, db_session, operator_user, object_storage, fixture_dir: Path
    ) -> None:
        await authenticate(api_client, operator_user)
        batch_id = await create_batch(api_client)
        paths = [
            build_text_pdf(fixture_dir / "a.pdf"),
            build_multi_certificate_pdf(fixture_dir / "b.pdf", copies=3),
        ]
        await api_client.post(f"/api/v1/batches/{batch_id}/files", files=files_payload(*paths))

        batch = await db_session.get(Batch, batch_id)
        await db_session.refresh(batch)
        assert batch.file_count == 2
        assert batch.total_bytes == sum(p.stat().st_size for p in paths)
        assert batch.status is BatchStatus.UPLOADING

    async def test_filename_is_sanitised_on_the_way_in(
        self, api_client: AsyncClient, operator_user, object_storage, fixture_dir: Path
    ) -> None:
        await authenticate(api_client, operator_user)
        batch_id = await create_batch(api_client)
        pdf = build_text_pdf(fixture_dir / "a.pdf")

        response = await api_client.post(
            f"/api/v1/batches/{batch_id}/files",
            files=[("files", ("../../etc/passwd.pdf", pdf.read_bytes(), "application/pdf"))],
        )
        name = response.json()[0]["original_filename"]
        assert name == "passwd.pdf"
        assert "/" not in name


class TestDeduplication:
    async def test_identical_bytes_are_deduplicated(
        self, api_client: AsyncClient, db_session, operator_user, object_storage, fixture_dir: Path
    ) -> None:
        await authenticate(api_client, operator_user)
        batch_id = await create_batch(api_client)
        pdf = build_text_pdf(fixture_dir / "a.pdf")

        first = await api_client.post(f"/api/v1/batches/{batch_id}/files", files=files_payload(pdf))
        second = await api_client.post(
            f"/api/v1/batches/{batch_id}/files", files=files_payload(pdf)
        )

        original = first.json()[0]
        duplicate = second.json()[0]

        assert duplicate["is_duplicate"] is True
        assert duplicate["duplicate_of"] == original["document_id"]
        assert duplicate["status"] == DocumentStatus.DUPLICATE.value
        assert duplicate["sha256"] == original["sha256"]

        # The duplicate reuses the original object rather than writing a second copy.
        rows = (
            await db_session.scalars(select(Document).where(Document.batch_id == batch_id))
        ).all()
        assert len({row.storage_key for row in rows}) == 1

    async def test_duplicate_across_batches_in_the_same_workspace(
        self, api_client: AsyncClient, operator_user, object_storage, fixture_dir: Path
    ) -> None:
        """A records office re-uploads the same folder into a new batch constantly."""
        await authenticate(api_client, operator_user)
        pdf = build_text_pdf(fixture_dir / "a.pdf")

        first_batch = await create_batch(api_client, "January")
        await api_client.post(f"/api/v1/batches/{first_batch}/files", files=files_payload(pdf))

        second_batch = await create_batch(api_client, "February")
        response = await api_client.post(
            f"/api/v1/batches/{second_batch}/files", files=files_payload(pdf)
        )
        assert response.json()[0]["is_duplicate"] is True

    async def test_a_different_file_is_not_a_duplicate(
        self, api_client: AsyncClient, operator_user, object_storage, fixture_dir: Path
    ) -> None:
        await authenticate(api_client, operator_user)
        batch_id = await create_batch(api_client)

        first = build_text_pdf(fixture_dir / "a.pdf", "birth_lahore")
        second = build_text_pdf(fixture_dir / "b.pdf", "death_karachi")

        response = await api_client.post(
            f"/api/v1/batches/{batch_id}/files", files=files_payload(first, second)
        )
        assert [item["is_duplicate"] for item in response.json()] == [False, False]

    async def test_duplicate_count_is_tracked(
        self, api_client: AsyncClient, db_session, operator_user, object_storage, fixture_dir: Path
    ) -> None:
        await authenticate(api_client, operator_user)
        batch_id = await create_batch(api_client)
        pdf = build_text_pdf(fixture_dir / "a.pdf")

        await api_client.post(f"/api/v1/batches/{batch_id}/files", files=files_payload(pdf, pdf))

        batch = await db_session.get(Batch, batch_id)
        await db_session.refresh(batch)
        assert batch.duplicate_count == 1
        assert batch.file_count == 2


class TestRejectedFiles:
    """A bad file fails alone; the rest of the upload still succeeds."""

    async def test_unsupported_type_is_recorded_not_raised(
        self, api_client: AsyncClient, operator_user, object_storage, fixture_dir: Path
    ) -> None:
        await authenticate(api_client, operator_user)
        batch_id = await create_batch(api_client)

        executable = fixture_dir / "tool.pdf"
        executable.write_bytes(b"MZ\x90\x00" + b"\x00" * 2048)

        response = await api_client.post(
            f"/api/v1/batches/{batch_id}/files", files=files_payload(executable)
        )
        assert response.status_code == 201
        [result] = response.json()
        assert result["status"] == DocumentStatus.FAILED.value

    async def test_encrypted_pdf_reports_its_own_code(
        self, api_client: AsyncClient, db_session, operator_user, object_storage, fixture_dir: Path
    ) -> None:
        await authenticate(api_client, operator_user)
        batch_id = await create_batch(api_client)
        encrypted = build_encrypted_pdf(fixture_dir / "locked.pdf", password="letmein")

        response = await api_client.post(
            f"/api/v1/batches/{batch_id}/files", files=files_payload(encrypted)
        )
        document = await db_session.get(Document, response.json()[0]["document_id"])
        assert document.status is DocumentStatus.FAILED
        assert document.error_code == "document_encrypted"
        assert "password" in (document.error_message or "").lower()

    async def test_corrupt_pdf_reports_its_own_code(
        self, api_client: AsyncClient, db_session, operator_user, object_storage, fixture_dir: Path
    ) -> None:
        await authenticate(api_client, operator_user)
        batch_id = await create_batch(api_client)
        corrupt = build_corrupt_pdf(fixture_dir / "broken.pdf")

        response = await api_client.post(
            f"/api/v1/batches/{batch_id}/files", files=files_payload(corrupt)
        )
        document = await db_session.get(Document, response.json()[0]["document_id"])
        assert document.status is DocumentStatus.FAILED
        assert document.error_code == "document_corrupt"

    async def test_zero_page_pdf_is_rejected(
        self, api_client: AsyncClient, db_session, operator_user, object_storage, fixture_dir: Path
    ) -> None:
        await authenticate(api_client, operator_user)
        batch_id = await create_batch(api_client)
        empty = build_zero_page_pdf(fixture_dir / "nopages.pdf")

        response = await api_client.post(
            f"/api/v1/batches/{batch_id}/files", files=files_payload(empty)
        )
        document = await db_session.get(Document, response.json()[0]["document_id"])
        assert document.status is DocumentStatus.FAILED

    async def test_empty_file_is_rejected(
        self, api_client: AsyncClient, operator_user, object_storage, fixture_dir: Path
    ) -> None:
        await authenticate(api_client, operator_user)
        batch_id = await create_batch(api_client)
        empty = fixture_dir / "empty.pdf"
        empty.write_bytes(b"")

        response = await api_client.post(
            f"/api/v1/batches/{batch_id}/files", files=files_payload(empty)
        )
        assert response.json()[0]["status"] == DocumentStatus.FAILED.value

    async def test_one_bad_file_does_not_fail_the_others(
        self, api_client: AsyncClient, operator_user, object_storage, fixture_dir: Path
    ) -> None:
        """The acceptance criterion: a corrupt file fails alone."""
        await authenticate(api_client, operator_user)
        batch_id = await create_batch(api_client)

        good_one = build_text_pdf(fixture_dir / "good1.pdf", "birth_lahore")
        broken = build_corrupt_pdf(fixture_dir / "bad.pdf")
        good_two = build_text_pdf(fixture_dir / "good2.pdf", "death_karachi")

        response = await api_client.post(
            f"/api/v1/batches/{batch_id}/files",
            files=files_payload(good_one, broken, good_two),
        )
        assert response.status_code == 201
        statuses = [item["status"] for item in response.json()]
        assert statuses == [
            DocumentStatus.QUEUED.value,
            DocumentStatus.FAILED.value,
            DocumentStatus.QUEUED.value,
        ]


class TestArchives:
    async def test_zip_members_become_documents(
        self, api_client: AsyncClient, db_session, operator_user, object_storage, fixture_dir: Path
    ) -> None:
        await authenticate(api_client, operator_user)
        batch_id = await create_batch(api_client)

        pdf = build_text_pdf(fixture_dir / "a.pdf", "birth_lahore")
        other = build_text_pdf(fixture_dir / "b.pdf", "death_karachi")
        docx = build_docx(fixture_dir / "c.docx")
        archive = build_zip(
            fixture_dir / "batch.zip",
            {
                "2019/birth.pdf": pdf.read_bytes(),
                "2019/death.pdf": other.read_bytes(),
                "2019/marriage.docx": docx.read_bytes(),
            },
        )

        response = await api_client.post(
            f"/api/v1/batches/{batch_id}/files", files=files_payload(archive)
        )
        [result] = response.json()

        # The archive itself is a container, not something to extract fields from.
        assert result["status"] == DocumentStatus.SKIPPED.value
        assert len(result["children"]) == 3
        assert all(child["extracted_from_archive"] for child in result["children"])

        documents = (
            await db_session.scalars(select(Document).where(Document.batch_id == batch_id))
        ).all()
        assert len(documents) == 4
        children = [doc for doc in documents if doc.parent_document_id is not None]
        assert len(children) == 3
        assert all(child.archive_member_path for child in children)

    async def test_traversal_member_is_not_written(
        self, api_client: AsyncClient, db_session, operator_user, object_storage, fixture_dir: Path
    ) -> None:
        await authenticate(api_client, operator_user)
        batch_id = await create_batch(api_client)

        pdf = build_text_pdf(fixture_dir / "a.pdf")
        archive = build_zip(
            fixture_dir / "evil.zip",
            {"../../etc/passwd": b"root:x:0:0", "safe.pdf": pdf.read_bytes()},
        )

        response = await api_client.post(
            f"/api/v1/batches/{batch_id}/files", files=files_payload(archive)
        )
        names = {child["original_filename"] for child in response.json()[0]["children"]}
        assert "passwd" not in names
        assert "safe.pdf" in names

    async def test_zip_bomb_is_rejected(
        self, api_client: AsyncClient, db_session, operator_user, object_storage, fixture_dir: Path
    ) -> None:
        await authenticate(api_client, operator_user)
        batch_id = await create_batch(api_client)

        from tests.fixtures.builders import build_zip_bomb

        bomb = build_zip_bomb(fixture_dir / "bomb.zip", uncompressed_mb=80)

        response = await api_client.post(
            f"/api/v1/batches/{batch_id}/files", files=files_payload(bomb)
        )
        assert response.status_code == 201
        [result] = response.json()
        assert result["children"] == []

        document = await db_session.get(Document, result["document_id"])
        assert document.status in {DocumentStatus.FAILED, DocumentStatus.SKIPPED}


class TestAuthorisation:
    async def test_viewer_cannot_upload(
        self, api_client: AsyncClient, viewer_user, operator_user, object_storage, fixture_dir: Path
    ) -> None:
        await authenticate(api_client, operator_user)
        batch_id = await create_batch(api_client)

        api_client.cookies.clear()
        await authenticate(api_client, viewer_user)

        pdf = build_text_pdf(fixture_dir / "a.pdf")
        response = await api_client.post(
            f"/api/v1/batches/{batch_id}/files", files=files_payload(pdf)
        )
        assert response.status_code == 403
        assert response.json()["code"] == "insufficient_role"

    async def test_upload_requires_authentication(
        self, api_client: AsyncClient, fixture_dir: Path
    ) -> None:
        pdf = build_text_pdf(fixture_dir / "a.pdf")
        response = await api_client.post(
            "/api/v1/batches/00000000-0000-4000-8000-000000000000/files",
            files=files_payload(pdf),
        )
        assert response.status_code == 401
