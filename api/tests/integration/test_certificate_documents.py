"""Serving the scan behind an entry.

These are identity documents. The bucket is private and there is no URL that works
without a session, so every one of these requests has to prove three things before a
byte is served: there is a session, the entry belongs to the caller's office, and the
document is actually attached to that entry. A guessed id must not be enough.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from certex.core.security import hash_password
from certex.db.models import AuditLog, Batch, Document, User, Workspace
from certex.enums import AuditAction, BatchStatus, DocumentStatus, UserRole
from certex.storage.s3 import ObjectStorage, StorageKeys
from tests.conftest import TEST_PASSWORD, authenticate

pytestmark = [pytest.mark.integration]

TYPES = "/api/v1/certificate-types"
CERTIFICATES = "/api/v1/certificates"

PDF_BYTES = b"%PDF-1.7\n% a scanned certificate\n"


async def birth_type_id(client: AsyncClient) -> str:
    response = await client.get(TYPES)
    assert response.status_code == 200, response.text
    return next(item["id"] for item in response.json() if item["key"] == "BIRTH")


async def record(client: AsyncClient, *, type_id: str, number: str) -> dict[str, Any]:
    response = await client.post(
        CERTIFICATES,
        json={
            "certificate_type_id": type_id,
            "values": {"certificate_number": number, "child_full_name": "Ayesha Noor"},
        },
    )
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


async def a_stored_document(
    session: AsyncSession,
    storage: ObjectStorage,
    *,
    workspace_id: uuid.UUID,
    payload: bytes = PDF_BYTES,
) -> Document:
    """A document row whose bytes really are in object storage."""
    batch = Batch(
        workspace_id=workspace_id,
        name=f"Batch {uuid.uuid4().hex[:6]}",
        status=BatchStatus.COMPLETED,
        settings_json={},
    )
    session.add(batch)
    await session.flush()

    document_id = uuid.uuid4()
    key = StorageKeys.document(workspace_id, document_id, year=2026, month=9)
    storage.upload_bytes(key, payload, content_type="application/pdf")

    document = Document(
        id=document_id,
        batch_id=batch.id,
        workspace_id=workspace_id,
        original_filename="../../etc/passwd.pdf",
        mime_type="application/pdf",
        byte_size=len(payload),
        sha256=uuid.uuid4().hex * 2,
        storage_key=key,
        status=DocumentStatus.COMPLETED,
    )
    session.add(document)
    await session.flush()
    return document


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


@pytest.fixture
async def attached(
    api_client: AsyncClient,
    operator_user: User,
    db_session: AsyncSession,
    object_storage: ObjectStorage,
) -> tuple[str, str]:
    """An entry with a real scan attached. Returns (certificate id, link id)."""
    await authenticate(api_client, operator_user)
    entry = await record(
        api_client, type_id=await birth_type_id(api_client), number="BC/LHR/2019/1001"
    )
    document = await a_stored_document(
        db_session, object_storage, workspace_id=operator_user.workspace_id
    )
    response = await api_client.post(
        f"{CERTIFICATES}/{entry['id']}/documents",
        json={"document_id": str(document.id), "kind": "PRIMARY"},
    )
    assert response.status_code == 201, response.text
    return entry["id"], response.json()["id"]


def content_url(certificate_id: str, link_id: str) -> str:
    return f"{CERTIFICATES}/{certificate_id}/documents/{link_id}/content"


class TestServingTheScan:
    async def test_the_bytes_come_back(
        self, api_client: AsyncClient, attached: tuple[str, str]
    ) -> None:
        certificate_id, link_id = attached
        response = await api_client.get(content_url(certificate_id, link_id))
        assert response.status_code == 200
        assert response.content == PDF_BYTES
        assert response.headers["content-type"] == "application/pdf"

    async def test_it_opens_in_the_viewer_rather_than_downloading(
        self, api_client: AsyncClient, attached: tuple[str, str]
    ) -> None:
        """A clerk comparing a scan to a typed value wants it beside the form."""
        certificate_id, link_id = attached
        response = await api_client.get(content_url(certificate_id, link_id))
        assert response.headers["content-disposition"].startswith("inline")

    async def test_the_download_is_named_after_the_certificate_not_the_upload(
        self, api_client: AsyncClient, attached: tuple[str, str]
    ) -> None:
        """The uploaded name was "../../etc/passwd.pdf" and must not be echoed back."""
        certificate_id, link_id = attached
        disposition = (await api_client.get(content_url(certificate_id, link_id))).headers[
            "content-disposition"
        ]
        assert "passwd" not in disposition
        assert ".." not in disposition
        assert "BC-LHR-2019-1001.pdf" in disposition

    async def test_it_is_not_cached_by_anything_in_between(
        self, api_client: AsyncClient, attached: tuple[str, str]
    ) -> None:
        certificate_id, link_id = attached
        headers = (await api_client.get(content_url(certificate_id, link_id))).headers
        assert "private" in headers["cache-control"]
        assert "no-store" in headers["cache-control"]
        assert headers["x-content-type-options"] == "nosniff"

    async def test_a_viewer_can_see_it(
        self, api_client: AsyncClient, attached: tuple[str, str], viewer_user: User
    ) -> None:
        certificate_id, link_id = attached
        await authenticate(api_client, viewer_user)
        assert (await api_client.get(content_url(certificate_id, link_id))).status_code == 200

    async def test_looking_at_it_is_audited(
        self, api_client: AsyncClient, attached: tuple[str, str], db_session: AsyncSession
    ) -> None:
        certificate_id, link_id = attached
        await api_client.get(content_url(certificate_id, link_id))

        actions = list(
            (
                await db_session.scalars(
                    select(AuditLog.action).where(
                        AuditLog.action == AuditAction.CERTIFICATE_DOCUMENT_VIEWED
                    )
                )
            ).all()
        )
        assert actions == [AuditAction.CERTIFICATE_DOCUMENT_VIEWED]


class TestRefusals:
    async def test_signing_in_is_required(
        self, api_client: AsyncClient, attached: tuple[str, str]
    ) -> None:
        certificate_id, link_id = attached
        api_client.cookies.clear()
        assert (await api_client.get(content_url(certificate_id, link_id))).status_code == 401

    async def test_a_link_from_another_entry_is_not_found(
        self,
        api_client: AsyncClient,
        attached: tuple[str, str],
        operator_user: User,
    ) -> None:
        """The link id is real; it just does not belong to this certificate."""
        _certificate_id, link_id = attached
        other = await record(
            api_client, type_id=await birth_type_id(api_client), number="BC/LHR/2019/1002"
        )
        assert (await api_client.get(content_url(other["id"], link_id))).status_code == 404

    async def test_a_guessed_link_id_is_not_found(
        self, api_client: AsyncClient, attached: tuple[str, str]
    ) -> None:
        certificate_id, _link_id = attached
        response = await api_client.get(content_url(certificate_id, str(uuid.uuid4())))
        assert response.status_code == 404

    async def test_another_office_cannot_read_it(
        self,
        api_client: AsyncClient,
        attached: tuple[str, str],
        db_session: AsyncSession,
    ) -> None:
        certificate_id, link_id = attached
        outsider = await a_second_office(db_session)
        await authenticate(api_client, outsider)
        assert (await api_client.get(content_url(certificate_id, link_id))).status_code == 404

    async def test_an_unknown_certificate_is_not_found(self, api_client: AsyncClient) -> None:
        response = await api_client.get(content_url(str(uuid.uuid4()), str(uuid.uuid4())))
        assert response.status_code in (401, 404)
