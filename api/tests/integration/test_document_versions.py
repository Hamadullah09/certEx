"""Replacing a scan, and following a superseded entry to the one that replaced it.

Registers get photographed twice - the first pass is crooked, or the flash washed out
half the page - and the second scan has to become the certificate's scan without
erasing the first. A value in the register was read from the first one, and "why does it
say that" has to stay answerable against the image it was actually read from.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from certex.core.security import hash_password
from certex.db.models import (
    Batch,
    Certificate,
    CertificateDocument,
    Document,
    User,
    Workspace,
)
from certex.enums import (
    BatchStatus,
    CertificateStatus,
    DocumentStatus,
    RevisionAction,
    UserRole,
)
from certex.storage.s3 import ObjectStorage, StorageKeys
from tests.conftest import TEST_PASSWORD, authenticate

pytestmark = [pytest.mark.integration]

TYPES = "/api/v1/certificate-types"
CERTIFICATES = "/api/v1/certificates"

ROW: dict[str, str] = {
    "certificate_number": "BC/LHR/2019/1001",
    "child_full_name": "Ayesha Noor Malik",
    "date_of_birth": "2019-04-03",
}


async def birth_type_id(client: AsyncClient) -> str:
    response = await client.get(TYPES)
    assert response.status_code == 200, response.text
    return next(item["id"] for item in response.json() if item["key"] == "BIRTH")


async def record(
    client: AsyncClient,
    *,
    type_id: str,
    values: dict[str, str] | None = None,
    allow_duplicate: bool = False,
) -> dict[str, Any]:
    response = await client.post(
        CERTIFICATES,
        json={
            "certificate_type_id": type_id,
            "values": values or ROW,
            "allow_duplicate": allow_duplicate,
        },
    )
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


async def a_scan(
    session: AsyncSession,
    storage: ObjectStorage,
    *,
    workspace_id: uuid.UUID,
    payload: bytes,
) -> Document:
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
        original_filename="scan.pdf",
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
async def with_scan(
    api_client: AsyncClient,
    operator_user: User,
    db_session: AsyncSession,
    object_storage: ObjectStorage,
) -> tuple[str, str, Document]:
    """An entry with a crooked first scan attached. (certificate id, link id, document)"""
    await authenticate(api_client, operator_user)
    entry = await record(api_client, type_id=await birth_type_id(api_client))
    first = await a_scan(
        db_session,
        object_storage,
        workspace_id=operator_user.workspace_id,
        payload=b"%PDF-1.7\nfirst, crooked\n",
    )
    response = await api_client.post(
        f"{CERTIFICATES}/{entry['id']}/documents",
        json={"document_id": str(first.id), "kind": "PRIMARY", "page_start": 1, "page_end": 1},
    )
    assert response.status_code == 201, response.text
    return entry["id"], response.json()["id"], first


class TestReplacingAScan:
    async def test_the_new_scan_becomes_the_certificate_s_scan(
        self,
        api_client: AsyncClient,
        with_scan: tuple[str, str, Document],
        db_session: AsyncSession,
        object_storage: ObjectStorage,
        operator_user: User,
    ) -> None:
        certificate_id, link_id, _first = with_scan
        better = await a_scan(
            db_session,
            object_storage,
            workspace_id=operator_user.workspace_id,
            payload=b"%PDF-1.7\nstraight, readable\n",
        )

        response = await api_client.post(
            f"{CERTIFICATES}/{certificate_id}/documents/{link_id}/replace",
            json={"document_id": str(better.id), "note": "The first pass was crooked."},
        )
        assert response.status_code == 200, response.text
        added, replaced = response.json()
        assert added["document_id"] == str(better.id)
        assert added["kind"] == "PRIMARY"
        assert replaced["id"] == link_id
        assert replaced["kind"] == "SUPERSEDED"

    async def test_the_old_scan_is_kept_not_deleted(
        self,
        api_client: AsyncClient,
        with_scan: tuple[str, str, Document],
        db_session: AsyncSession,
        object_storage: ObjectStorage,
        operator_user: User,
    ) -> None:
        """A value was read from it, so it has to stay answerable."""
        certificate_id, link_id, first = with_scan
        better = await a_scan(
            db_session,
            object_storage,
            workspace_id=operator_user.workspace_id,
            payload=b"%PDF-1.7\nstraight\n",
        )
        await api_client.post(
            f"{CERTIFICATES}/{certificate_id}/documents/{link_id}/replace",
            json={"document_id": str(better.id)},
        )

        detail = (await api_client.get(f"{CERTIFICATES}/{certificate_id}")).json()
        assert len(detail["documents"]) == 2

        still_there = await api_client.get(
            f"{CERTIFICATES}/{certificate_id}/documents/{link_id}/content"
        )
        assert still_there.status_code == 200
        assert still_there.content == b"%PDF-1.7\nfirst, crooked\n"
        assert first.id is not None

    async def test_the_page_range_carries_over_when_it_is_not_given(
        self,
        api_client: AsyncClient,
        with_scan: tuple[str, str, Document],
        db_session: AsyncSession,
        object_storage: ObjectStorage,
        operator_user: User,
    ) -> None:
        certificate_id, link_id, _first = with_scan
        better = await a_scan(
            db_session,
            object_storage,
            workspace_id=operator_user.workspace_id,
            payload=b"%PDF-1.7\nstraight\n",
        )
        response = await api_client.post(
            f"{CERTIFICATES}/{certificate_id}/documents/{link_id}/replace",
            json={"document_id": str(better.id)},
        )
        added, _replaced = response.json()
        assert (added["page_start"], added["page_end"]) == (1, 1)

    async def test_the_replacement_is_recorded_in_the_history(
        self,
        api_client: AsyncClient,
        with_scan: tuple[str, str, Document],
        db_session: AsyncSession,
        object_storage: ObjectStorage,
        operator_user: User,
    ) -> None:
        certificate_id, link_id, _first = with_scan
        better = await a_scan(
            db_session,
            object_storage,
            workspace_id=operator_user.workspace_id,
            payload=b"%PDF-1.7\nstraight\n",
        )
        await api_client.post(
            f"{CERTIFICATES}/{certificate_id}/documents/{link_id}/replace",
            json={"document_id": str(better.id), "note": "Crooked and half cut off."},
        )

        history = (await api_client.get(f"{CERTIFICATES}/{certificate_id}/history")).json()
        assert history[-1]["action"] == RevisionAction.DOCUMENT_REPLACED
        assert history[-1]["note"] == "Crooked and half cut off."

    async def test_replacing_a_scan_with_itself_is_refused(
        self, api_client: AsyncClient, with_scan: tuple[str, str, Document]
    ) -> None:
        certificate_id, link_id, first = with_scan
        response = await api_client.post(
            f"{CERTIFICATES}/{certificate_id}/documents/{link_id}/replace",
            json={"document_id": str(first.id)},
        )
        assert response.status_code == 409

    async def test_a_scan_from_another_office_cannot_be_attached(
        self,
        api_client: AsyncClient,
        with_scan: tuple[str, str, Document],
        db_session: AsyncSession,
        object_storage: ObjectStorage,
    ) -> None:
        certificate_id, link_id, _first = with_scan
        outsider = await a_second_office(db_session)
        theirs = await a_scan(
            db_session,
            object_storage,
            workspace_id=outsider.workspace_id,
            payload=b"%PDF-1.7\nsomebody else\n",
        )
        response = await api_client.post(
            f"{CERTIFICATES}/{certificate_id}/documents/{link_id}/replace",
            json={"document_id": str(theirs.id)},
        )
        assert response.status_code == 404

    async def test_a_viewer_cannot_replace_a_scan(
        self,
        api_client: AsyncClient,
        with_scan: tuple[str, str, Document],
        db_session: AsyncSession,
        object_storage: ObjectStorage,
        operator_user: User,
        viewer_user: User,
    ) -> None:
        certificate_id, link_id, _first = with_scan
        better = await a_scan(
            db_session,
            object_storage,
            workspace_id=operator_user.workspace_id,
            payload=b"%PDF-1.7\nstraight\n",
        )
        await authenticate(api_client, viewer_user)
        response = await api_client.post(
            f"{CERTIFICATES}/{certificate_id}/documents/{link_id}/replace",
            json={"document_id": str(better.id)},
        )
        assert response.status_code == 403

    async def test_an_unknown_link_is_not_found(
        self,
        api_client: AsyncClient,
        with_scan: tuple[str, str, Document],
        db_session: AsyncSession,
        object_storage: ObjectStorage,
        operator_user: User,
    ) -> None:
        certificate_id, _link_id, _first = with_scan
        better = await a_scan(
            db_session,
            object_storage,
            workspace_id=operator_user.workspace_id,
            payload=b"%PDF-1.7\nstraight\n",
        )
        response = await api_client.post(
            f"{CERTIFICATES}/{certificate_id}/documents/{uuid.uuid4()}/replace",
            json={"document_id": str(better.id)},
        )
        assert response.status_code == 404


class TestSupersededEntries:
    async def test_a_superseded_entry_points_at_the_one_that_replaced_it(
        self, api_client: AsyncClient, operator_user: User
    ) -> None:
        """Which is a different fact from which entry it might repeat."""
        await authenticate(api_client, operator_user)
        type_id = await birth_type_id(api_client)
        first = await record(api_client, type_id=type_id)
        second = await record(api_client, type_id=type_id, allow_duplicate=True)

        await api_client.post(
            f"{CERTIFICATES}/{second['id']}/resolve-duplicate",
            json={"other_id": first["id"], "same_certificate": True},
        )

        later = (await api_client.get(f"{CERTIFICATES}/{second['id']}")).json()
        assert later["status"] == CertificateStatus.SUPERSEDED
        assert later["superseded_by_id"] == first["id"]
        assert later["duplicate_of_id"] is None, "the suspicion has been answered"

    async def test_the_surviving_entry_points_at_nothing(
        self, api_client: AsyncClient, operator_user: User
    ) -> None:
        await authenticate(api_client, operator_user)
        type_id = await birth_type_id(api_client)
        first = await record(api_client, type_id=type_id)
        second = await record(api_client, type_id=type_id, allow_duplicate=True)
        await api_client.post(
            f"{CERTIFICATES}/{second['id']}/resolve-duplicate",
            json={"other_id": first["id"], "same_certificate": True},
        )

        earlier = (await api_client.get(f"{CERTIFICATES}/{first['id']}")).json()
        assert earlier["status"] == CertificateStatus.ACTIVE
        assert earlier["superseded_by_id"] is None
        assert earlier["duplicate_of_id"] is None

    async def test_entries_called_distinct_supersede_nothing(
        self, api_client: AsyncClient, operator_user: User
    ) -> None:
        await authenticate(api_client, operator_user)
        type_id = await birth_type_id(api_client)
        first = await record(api_client, type_id=type_id)
        second = await record(api_client, type_id=type_id, allow_duplicate=True)
        await api_client.post(
            f"{CERTIFICATES}/{second['id']}/resolve-duplicate",
            json={"other_id": first["id"], "same_certificate": False},
        )

        later = (await api_client.get(f"{CERTIFICATES}/{second['id']}")).json()
        assert later["status"] == CertificateStatus.ACTIVE
        assert later["superseded_by_id"] is None

    async def test_a_superseded_entry_cannot_be_corrected(
        self, api_client: AsyncClient, operator_user: User
    ) -> None:
        """Correcting it would change a record nobody relies on any more."""
        await authenticate(api_client, operator_user)
        type_id = await birth_type_id(api_client)
        first = await record(api_client, type_id=type_id)
        second = await record(api_client, type_id=type_id, allow_duplicate=True)
        await api_client.post(
            f"{CERTIFICATES}/{second['id']}/resolve-duplicate",
            json={"other_id": first["id"], "same_certificate": True},
        )

        response = await api_client.patch(
            f"{CERTIFICATES}/{second['id']}",
            json={"values": {"child_full_name": "Someone"}, "note": "Because."},
        )
        assert response.status_code == 409
        assert "superseded" in response.json()["detail"]

    async def test_both_entries_remain_in_the_register(
        self, api_client: AsyncClient, operator_user: User, db_session: AsyncSession
    ) -> None:
        await authenticate(api_client, operator_user)
        type_id = await birth_type_id(api_client)
        first = await record(api_client, type_id=type_id)
        second = await record(api_client, type_id=type_id, allow_duplicate=True)
        await api_client.post(
            f"{CERTIFICATES}/{second['id']}/resolve-duplicate",
            json={"other_id": first["id"], "same_certificate": True},
        )

        count = await db_session.scalar(select(func.count()).select_from(Certificate))
        assert count == 2
        links = await db_session.scalar(select(func.count()).select_from(CertificateDocument))
        assert links == 0
