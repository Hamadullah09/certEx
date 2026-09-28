"""The page image a reviewer checks a value against.

A scanned page already has one, made when OCR straightened it. A text PDF has never been
drawn at all - and a reviewer still has to see it, or they are asked to trust a value
against nothing. So it is rendered on first request and kept, at the same resolution and
with the same straightening OCR would have used, which is what keeps a stored bounding
box in the right place whatever kind of document it came from.
"""

from __future__ import annotations

import asyncio
import io
import uuid
from pathlib import Path

import pytest
from httpx import AsyncClient
from PIL import Image
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from certex.config import get_settings
from certex.db.models import AuditLog, Batch, Document, User
from certex.db.session import session_scope
from certex.enums import AuditAction, DocumentStatus
from certex.pipeline.filetypes import MIME_DOCX, MIME_PDF
from certex.pipeline.ocr.tesseract import resolve_binary
from certex.pipeline.stages.ocr_stage import run_ocr_page_stage
from certex.pipeline.stages.text_stage import run_text_stage
from certex.storage.s3 import StorageKeys
from tests.conftest import authenticate
from tests.fixtures.builders import build_docx, build_text_pdf
from tests.fixtures.corpus import build_scanned_pdf
from tests.integration.conftest import CommittedBatch, Recorder

pytestmark = pytest.mark.integration


async def upload_document(
    session: AsyncSession,
    batch: Batch,
    path: Path,
    *,
    mime_type: str = MIME_PDF,
) -> Document:
    """A document row plus its bytes in storage, without running the pipeline."""
    import datetime as dt
    import hashlib

    from certex.storage.s3 import get_object_storage

    document_id = uuid.uuid4()
    now = dt.datetime.now(tz=dt.UTC)
    key = StorageKeys.document(batch.workspace_id, document_id, year=now.year, month=now.month)
    payload = path.read_bytes()
    get_object_storage().upload_bytes(key, payload, content_type=mime_type)

    document = Document(
        id=document_id,
        batch_id=batch.id,
        workspace_id=batch.workspace_id,
        original_filename=path.name,
        mime_type=mime_type,
        byte_size=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
        storage_key=key,
        status=DocumentStatus.COMPLETED,
        page_count=1,
    )
    session.add(document)
    await session.flush()
    return document


class TestRenderingOnDemand:
    async def test_a_text_pdf_gets_a_page_image_drawn_for_it(
        self,
        api_client: AsyncClient,
        db_session: AsyncSession,
        operator_user: User,
        batch: Batch,
        object_storage: object,
        tmp_path: Path,
    ) -> None:
        document = await upload_document(db_session, batch, build_text_pdf(tmp_path / "birth.pdf"))
        await authenticate(api_client, operator_user)

        response = await api_client.get(f"/api/v1/documents/{document.id}/pages/1/image")

        assert response.status_code == 200, response.text
        assert response.headers["content-type"] == "image/jpeg"
        with Image.open(io.BytesIO(response.content)) as image:
            assert image.width > 500, "a page a person can actually read"

    async def test_the_image_is_kept_for_the_next_reviewer(
        self,
        api_client: AsyncClient,
        db_session: AsyncSession,
        operator_user: User,
        batch: Batch,
        object_storage: object,
        tmp_path: Path,
    ) -> None:
        document = await upload_document(db_session, batch, build_text_pdf(tmp_path / "birth.pdf"))
        await authenticate(api_client, operator_user)

        first = await api_client.get(f"/api/v1/documents/{document.id}/pages/1/image")
        second = await api_client.get(f"/api/v1/documents/{document.id}/pages/1/image")

        assert first.content == second.content
        key = StorageKeys.page_image(batch.workspace_id, document.id, 1)
        assert object_storage.object_exists(key)
        object_storage.delete(key)

    async def test_the_image_is_not_cached_by_anything_shared(
        self,
        api_client: AsyncClient,
        db_session: AsyncSession,
        operator_user: User,
        batch: Batch,
        object_storage: object,
        tmp_path: Path,
    ) -> None:
        # It is a person's certificate, not a public asset.
        document = await upload_document(db_session, batch, build_text_pdf(tmp_path / "birth.pdf"))
        await authenticate(api_client, operator_user)

        response = await api_client.get(f"/api/v1/documents/{document.id}/pages/1/image")

        assert "private" in response.headers["cache-control"]

    async def test_viewing_a_page_is_recorded(
        self,
        api_client: AsyncClient,
        db_session: AsyncSession,
        operator_user: User,
        batch: Batch,
        object_storage: object,
        tmp_path: Path,
    ) -> None:
        document = await upload_document(db_session, batch, build_text_pdf(tmp_path / "birth.pdf"))
        await authenticate(api_client, operator_user)

        await api_client.get(f"/api/v1/documents/{document.id}/pages/1/image")

        entry = await db_session.scalar(
            select(AuditLog).where(AuditLog.action == AuditAction.DOCUMENT_PAGE_VIEWED)
        )
        assert entry is not None
        assert entry.metadata_jsonb == {"page_number": 1}


class TestWhatCannotBeDrawn:
    async def test_a_word_document_says_so_rather_than_failing_oddly(
        self,
        api_client: AsyncClient,
        db_session: AsyncSession,
        operator_user: User,
        batch: Batch,
        object_storage: object,
        tmp_path: Path,
    ) -> None:
        # This deployment has no renderer for Word; the review screen shows the text.
        document = await upload_document(
            db_session, batch, build_docx(tmp_path / "birth.docx"), mime_type=MIME_DOCX
        )
        await authenticate(api_client, operator_user)

        response = await api_client.get(f"/api/v1/documents/{document.id}/pages/1/image")

        assert response.status_code == 415
        assert response.json()["code"] == "unsupported_media_type"

    async def test_a_page_that_does_not_exist(
        self,
        api_client: AsyncClient,
        db_session: AsyncSession,
        operator_user: User,
        batch: Batch,
        object_storage: object,
        tmp_path: Path,
    ) -> None:
        document = await upload_document(db_session, batch, build_text_pdf(tmp_path / "birth.pdf"))
        await authenticate(api_client, operator_user)

        response = await api_client.get(f"/api/v1/documents/{document.id}/pages/9/image")

        assert response.status_code == 422
        assert response.json()["code"] == "document_corrupt"

    async def test_another_workspaces_document_is_not_served(
        self, api_client: AsyncClient, operator_user: User
    ) -> None:
        await authenticate(api_client, operator_user)
        response = await api_client.get(f"/api/v1/documents/{uuid.uuid4()}/pages/1/image")
        assert response.status_code == 404

    async def test_signing_in_is_required(self, api_client: AsyncClient) -> None:
        response = await api_client.get(f"/api/v1/documents/{uuid.uuid4()}/pages/1/image")
        assert response.status_code == 401


@pytest.mark.ocr
@pytest.mark.skipif(
    resolve_binary(get_settings()) is None,
    reason="the tesseract binary is not installed on this machine",
)
class TestScannedPages:
    async def test_the_image_ocr_read_is_the_one_served(
        self, committed_batch: CommittedBatch, recorder: Recorder, tmp_path: Path
    ) -> None:
        # Boxes are stored against the straightened image OCR saw, so that is the image
        # the review pane must draw them over.
        document_id = committed_batch.add_document(
            build_scanned_pdf(tmp_path / "scan.pdf", skew_degrees=1.5), mime_type=MIME_PDF
        )
        await asyncio.to_thread(run_text_stage, document_id, dispatch=recorder)
        await asyncio.to_thread(run_ocr_page_stage, document_id, 1, dispatch=recorder)

        key = StorageKeys.page_image(committed_batch.workspace_id, document_id, 1)
        committed_batch.storage_keys.append(key)
        stored = committed_batch.storage.get_bytes_if_exists(key)

        assert stored is not None
        with session_scope() as session:
            document = session.get(Document, document_id)
            assert document is not None
        with Image.open(io.BytesIO(stored)) as image:
            assert image.mode == "L"
            assert image.height > image.width, "a portrait certificate, straightened"
