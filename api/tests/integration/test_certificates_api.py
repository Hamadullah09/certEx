"""The register over HTTP.

The rules worth testing here are the ones a careless implementation gets wrong in a
way nobody notices until the register is already wrong: a repeated certificate
number quietly overwriting an entry, a document attached by an id from another
office, or an entry readable across the workspace boundary.
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
    AuditLog,
    Certificate,
    CertificateName,
    Document,
    User,
    Workspace,
)
from certex.enums import (
    AuditAction,
    CertificateSource,
    DuplicateStatus,
    FieldRole,
    UserRole,
)
from tests.conftest import TEST_PASSWORD, authenticate

pytestmark = [pytest.mark.integration]

TYPES = "/api/v1/certificate-types"
CERTIFICATES = "/api/v1/certificates"

BIRTH_ROW: dict[str, str] = {
    "certificate_number": "BC/LHR/2019/1001",
    "child_full_name": "Ayesha Noor Malik",
    "sex": "Female",
    "date_of_birth": "2019-04-03",
    "father_full_name": "Tariq Mahmood Malik",
    "mother_full_name": "Nasreen Akhtar",
    "registration_date": "2019-05-01",
    "issuing_authority": "Union Council 42, Lahore",
}


async def birth_type_id(client: AsyncClient) -> str:
    """The seeded Birth type; the first read of the list is what seeds it."""
    response = await client.get(TYPES)
    assert response.status_code == 200, response.text
    return next(item["id"] for item in response.json() if item["key"] == "BIRTH")


async def record(
    client: AsyncClient,
    *,
    type_id: str,
    values: dict[str, str] | None = None,
    allow_duplicate: bool = False,
    expect: int = 201,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "certificate_type_id": type_id,
        "values": values if values is not None else BIRTH_ROW,
    }
    if allow_duplicate:
        payload["allow_duplicate"] = True
    response = await client.post(CERTIFICATES, json=payload)
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
        role=UserRole.ADMIN,
        is_active=True,
    )
    session.add(user)
    await session.flush()
    return user


class TestRecording:
    async def test_an_operator_can_record_a_certificate(
        self, api_client: AsyncClient, operator_user: User
    ) -> None:
        await authenticate(api_client, operator_user)
        entry = await record(api_client, type_id=await birth_type_id(api_client))

        assert entry["certificate_number"] == "BC/LHR/2019/1001"
        assert entry["primary_name"] == "Ayesha Noor Malik"
        assert entry["event_date"] == "2019-04-03"
        assert entry["event_date_role"] == FieldRole.BIRTH_DATE
        assert entry["source"] == CertificateSource.MANUAL
        assert entry["duplicate_status"] == DuplicateStatus.NONE

    async def test_the_whole_row_is_stored_under_its_field_names(
        self, api_client: AsyncClient, operator_user: User
    ) -> None:
        await authenticate(api_client, operator_user)
        entry = await record(api_client, type_id=await birth_type_id(api_client))
        assert entry["values"]["mother_full_name"] == "Nasreen Akhtar"
        assert entry["values"]["issuing_authority"] == "Union Council 42, Lahore"

    async def test_everyone_named_becomes_searchable(
        self, api_client: AsyncClient, operator_user: User, db_session: AsyncSession
    ) -> None:
        """Not just the child: a clerk looks up a birth by the father's name."""
        await authenticate(api_client, operator_user)
        entry = await record(api_client, type_id=await birth_type_id(api_client))

        rows = list(
            (
                await db_session.scalars(
                    select(CertificateName).where(
                        CertificateName.certificate_id == uuid.UUID(entry["id"])
                    )
                )
            ).all()
        )
        by_role = {row.role: row.value_key for row in rows}
        assert by_role[FieldRole.SUBJECT_NAME] == "ayesha noor malik"
        assert by_role[FieldRole.FATHER_NAME] == "tariq mahmood malik"
        assert by_role[FieldRole.MOTHER_NAME] == "nasreen akhtar"

    async def test_the_dates_are_recorded_under_their_roles(
        self, api_client: AsyncClient, operator_user: User
    ) -> None:
        await authenticate(api_client, operator_user)
        entry = await record(api_client, type_id=await birth_type_id(api_client))
        detail = (await api_client.get(f"{CERTIFICATES}/{entry['id']}")).json()
        roles = {item["role"]: item["value"] for item in detail["dates"]}
        assert roles[FieldRole.BIRTH_DATE] == "2019-04-03"
        assert roles[FieldRole.REGISTRATION_DATE] == "2019-05-01"

    async def test_a_record_with_no_number_is_refused(
        self, api_client: AsyncClient, operator_user: User
    ) -> None:
        """Without a number the entry could never be found again."""
        await authenticate(api_client, operator_user)
        values = {key: value for key, value in BIRTH_ROW.items() if key != "certificate_number"}
        await record(api_client, type_id=await birth_type_id(api_client), values=values, expect=422)

    async def test_a_field_the_schema_does_not_define_is_refused(
        self, api_client: AsyncClient, operator_user: User
    ) -> None:
        await authenticate(api_client, operator_user)
        values = {**BIRTH_ROW, "favourite_colour": "green"}
        response = await api_client.post(
            CERTIFICATES,
            json={"certificate_type_id": await birth_type_id(api_client), "values": values},
        )
        assert response.status_code == 422
        assert "favourite_colour" in response.json()["detail"]

    async def test_a_viewer_cannot_record_one(
        self, api_client: AsyncClient, admin_user: User, viewer_user: User
    ) -> None:
        await authenticate(api_client, admin_user)
        type_id = await birth_type_id(api_client)

        await authenticate(api_client, viewer_user)
        await record(api_client, type_id=type_id, expect=403)

    async def test_recording_is_audited(
        self, api_client: AsyncClient, operator_user: User, db_session: AsyncSession
    ) -> None:
        await authenticate(api_client, operator_user)
        await record(api_client, type_id=await birth_type_id(api_client))

        count = await db_session.scalar(
            select(func.count())
            .select_from(AuditLog)
            .where(AuditLog.action == AuditAction.CERTIFICATE_CREATED)
        )
        assert count == 1


class TestDuplicates:
    async def test_a_repeated_number_is_a_conflict(
        self, api_client: AsyncClient, operator_user: User
    ) -> None:
        """Never an overwrite. The office decides what two entries mean."""
        await authenticate(api_client, operator_user)
        type_id = await birth_type_id(api_client)
        await record(api_client, type_id=type_id)

        response = await api_client.post(
            CERTIFICATES, json={"certificate_type_id": type_id, "values": BIRTH_ROW}
        )
        assert response.status_code == 409
        assert "BC/LHR/2019/1001" in response.json()["detail"]
        assert response.json()["remediation"]

    async def test_the_same_number_written_differently_still_collides(
        self, api_client: AsyncClient, operator_user: User
    ) -> None:
        await authenticate(api_client, operator_user)
        type_id = await birth_type_id(api_client)
        await record(api_client, type_id=type_id)

        restyled = {**BIRTH_ROW, "certificate_number": "bc-lhr-2019-1001"}
        await record(api_client, type_id=type_id, values=restyled, expect=409)

    async def test_the_first_entry_is_left_exactly_as_it_was(
        self, api_client: AsyncClient, operator_user: User
    ) -> None:
        await authenticate(api_client, operator_user)
        type_id = await birth_type_id(api_client)
        first = await record(api_client, type_id=type_id)

        await record(
            api_client,
            type_id=type_id,
            values={**BIRTH_ROW, "child_full_name": "Someone Else"},
            expect=409,
        )
        reread = (await api_client.get(f"{CERTIFICATES}/{first['id']}")).json()
        assert reread["primary_name"] == "Ayesha Noor Malik"
        assert reread["record_version"] == 1

    async def test_it_can_be_recorded_deliberately_as_a_duplicate(
        self, api_client: AsyncClient, operator_user: User
    ) -> None:
        """The same number does recur across offices, so the office can insist."""
        await authenticate(api_client, operator_user)
        type_id = await birth_type_id(api_client)
        first = await record(api_client, type_id=type_id)
        second = await record(api_client, type_id=type_id, allow_duplicate=True)

        assert second["duplicate_status"] == DuplicateStatus.SUSPECTED
        assert second["duplicate_of_id"] == first["id"]
        assert second["needs_review"] is True, "a duplicate is a question for a person"

    async def test_neither_entry_is_merged(
        self, api_client: AsyncClient, operator_user: User, db_session: AsyncSession
    ) -> None:
        await authenticate(api_client, operator_user)
        type_id = await birth_type_id(api_client)
        await record(api_client, type_id=type_id)
        await record(
            api_client,
            type_id=type_id,
            values={**BIRTH_ROW, "child_full_name": "Ayesha Noor"},
            allow_duplicate=True,
        )
        count = await db_session.scalar(select(func.count()).select_from(Certificate))
        assert count == 2

    async def test_the_duplicate_list_explains_why(
        self, api_client: AsyncClient, operator_user: User
    ) -> None:
        await authenticate(api_client, operator_user)
        type_id = await birth_type_id(api_client)
        first = await record(api_client, type_id=type_id)
        second = await record(api_client, type_id=type_id, allow_duplicate=True)

        candidates = (await api_client.get(f"{CERTIFICATES}/{second['id']}/duplicates")).json()
        assert [item["certificate_id"] for item in candidates] == [first["id"]]
        assert candidates[0]["reason"] == "same_number"

    async def test_a_mistyped_number_is_caught_by_name_and_date(
        self, api_client: AsyncClient, operator_user: User
    ) -> None:
        """One certificate entered twice, the number wrong once: everything else agrees."""
        await authenticate(api_client, operator_user)
        type_id = await birth_type_id(api_client)
        first = await record(api_client, type_id=type_id)
        second = await record(
            api_client,
            type_id=type_id,
            values={**BIRTH_ROW, "certificate_number": "BC/LHR/2019/1OO1"},
        )

        assert second["duplicate_status"] == DuplicateStatus.SUSPECTED
        assert second["duplicate_of_id"] is None, "not linked: only a number match is that strong"

        candidates = (await api_client.get(f"{CERTIFICATES}/{second['id']}/duplicates")).json()
        assert candidates[0]["certificate_id"] == first["id"]
        assert candidates[0]["reason"] == "same_name_and_event_date"

    async def test_a_different_person_on_the_same_day_is_not_a_duplicate(
        self, api_client: AsyncClient, operator_user: User
    ) -> None:
        await authenticate(api_client, operator_user)
        type_id = await birth_type_id(api_client)
        await record(api_client, type_id=type_id)
        other = await record(
            api_client,
            type_id=type_id,
            values={
                **BIRTH_ROW,
                "certificate_number": "BC/LHR/2019/1002",
                "child_full_name": "Bilal Hussain",
            },
        )
        assert other["duplicate_status"] == DuplicateStatus.NONE

    async def test_another_office_holding_the_number_is_not_this_office_s_problem(
        self, api_client: AsyncClient, operator_user: User, db_session: AsyncSession
    ) -> None:
        outsider = await a_second_office(db_session)
        await authenticate(api_client, outsider)
        await record(api_client, type_id=await birth_type_id(api_client))

        await authenticate(api_client, operator_user)
        mine = await record(api_client, type_id=await birth_type_id(api_client))
        assert mine["duplicate_status"] == DuplicateStatus.NONE


class TestReading:
    async def test_a_viewer_can_read_an_entry(
        self, api_client: AsyncClient, operator_user: User, viewer_user: User
    ) -> None:
        await authenticate(api_client, operator_user)
        entry = await record(api_client, type_id=await birth_type_id(api_client))

        await authenticate(api_client, viewer_user)
        response = await api_client.get(f"{CERTIFICATES}/{entry['id']}")
        assert response.status_code == 200
        assert response.json()["certificate_number"] == "BC/LHR/2019/1001"

    async def test_reading_an_entry_is_audited(
        self, api_client: AsyncClient, operator_user: User, db_session: AsyncSession
    ) -> None:
        """An entry is personal data, so who looked at it is worth recording."""
        await authenticate(api_client, operator_user)
        entry = await record(api_client, type_id=await birth_type_id(api_client))
        await api_client.get(f"{CERTIFICATES}/{entry['id']}")

        count = await db_session.scalar(
            select(func.count())
            .select_from(AuditLog)
            .where(AuditLog.action == AuditAction.CERTIFICATE_VIEWED)
        )
        assert count == 1

    async def test_an_unknown_id_is_not_found(
        self, api_client: AsyncClient, viewer_user: User
    ) -> None:
        await authenticate(api_client, viewer_user)
        assert (await api_client.get(f"{CERTIFICATES}/{uuid.uuid4()}")).status_code == 404

    async def test_listing_is_paged(self, api_client: AsyncClient, operator_user: User) -> None:
        await authenticate(api_client, operator_user)
        type_id = await birth_type_id(api_client)
        for index in range(3):
            await record(
                api_client,
                type_id=type_id,
                values={**BIRTH_ROW, "certificate_number": f"BC/LHR/2019/{2000 + index}"},
            )

        first = (await api_client.get(CERTIFICATES, params={"limit": 2})).json()
        assert len(first["items"]) == 2
        assert first["meta"]["has_more"] is True

        second = (
            await api_client.get(
                CERTIFICATES, params={"limit": 2, "cursor": first["meta"]["next_cursor"]}
            )
        ).json()
        assert len(second["items"]) == 1
        assert second["meta"]["has_more"] is False

        seen = [item["id"] for item in (*first["items"], *second["items"])]
        assert len(set(seen)) == 3, "no entry appears on two pages"

    async def test_listing_can_be_narrowed_to_what_needs_review(
        self, api_client: AsyncClient, operator_user: User
    ) -> None:
        await authenticate(api_client, operator_user)
        type_id = await birth_type_id(api_client)
        await record(api_client, type_id=type_id)
        flagged = await record(api_client, type_id=type_id, allow_duplicate=True)

        listed = (await api_client.get(CERTIFICATES, params={"needs_review": True})).json()
        assert [item["id"] for item in listed["items"]] == [flagged["id"]]

    async def test_listing_can_be_narrowed_to_duplicates(
        self, api_client: AsyncClient, operator_user: User
    ) -> None:
        await authenticate(api_client, operator_user)
        type_id = await birth_type_id(api_client)
        await record(api_client, type_id=type_id)
        flagged = await record(api_client, type_id=type_id, allow_duplicate=True)

        listed = (await api_client.get(CERTIFICATES, params={"duplicates_only": True})).json()
        assert [item["id"] for item in listed["items"]] == [flagged["id"]]

    async def test_signing_in_is_required(self, api_client: AsyncClient) -> None:
        assert (await api_client.get(CERTIFICATES)).status_code == 401


class TestTenancy:
    async def test_another_office_s_entry_reads_as_absent(
        self, api_client: AsyncClient, operator_user: User, db_session: AsyncSession
    ) -> None:
        outsider = await a_second_office(db_session)
        await authenticate(api_client, outsider)
        theirs = await record(api_client, type_id=await birth_type_id(api_client))

        await authenticate(api_client, operator_user)
        assert (await api_client.get(f"{CERTIFICATES}/{theirs['id']}")).status_code == 404
        assert (
            await api_client.get(f"{CERTIFICATES}/{theirs['id']}/duplicates")
        ).status_code == 404

    async def test_another_office_s_entries_are_not_listed(
        self, api_client: AsyncClient, operator_user: User, db_session: AsyncSession
    ) -> None:
        outsider = await a_second_office(db_session)
        await authenticate(api_client, outsider)
        theirs = await record(api_client, type_id=await birth_type_id(api_client))

        await authenticate(api_client, operator_user)
        listed = (await api_client.get(CERTIFICATES)).json()
        assert theirs["id"] not in {item["id"] for item in listed["items"]}


class TestDocumentLinks:
    async def test_a_document_is_attached_by_identity(
        self,
        api_client: AsyncClient,
        operator_user: User,
        db_session: AsyncSession,
        workspace: Workspace,
    ) -> None:
        await authenticate(api_client, operator_user)
        entry = await record(api_client, type_id=await birth_type_id(api_client))
        document = await a_document(db_session, workspace_id=workspace.id)

        response = await api_client.post(
            f"{CERTIFICATES}/{entry['id']}/documents",
            json={"document_id": str(document.id), "kind": "SUPPORTING", "page_start": 2},
        )
        assert response.status_code == 201, response.text
        assert response.json()["document_id"] == str(document.id)

    async def test_attaching_the_same_document_twice_adds_one_link(
        self,
        api_client: AsyncClient,
        operator_user: User,
        db_session: AsyncSession,
        workspace: Workspace,
    ) -> None:
        await authenticate(api_client, operator_user)
        entry = await record(api_client, type_id=await birth_type_id(api_client))
        document = await a_document(db_session, workspace_id=workspace.id)

        body = {"document_id": str(document.id)}
        first = await api_client.post(f"{CERTIFICATES}/{entry['id']}/documents", json=body)
        second = await api_client.post(f"{CERTIFICATES}/{entry['id']}/documents", json=body)
        assert first.json()["id"] == second.json()["id"]

        detail = (await api_client.get(f"{CERTIFICATES}/{entry['id']}")).json()
        assert len(detail["documents"]) == 1

    async def test_a_document_from_another_office_cannot_be_attached(
        self, api_client: AsyncClient, operator_user: User, db_session: AsyncSession
    ) -> None:
        """The id is guessable; the answer must not depend on it being unguessable."""
        outsider = await a_second_office(db_session)
        theirs = await a_document(db_session, workspace_id=outsider.workspace_id)

        await authenticate(api_client, operator_user)
        entry = await record(api_client, type_id=await birth_type_id(api_client))
        response = await api_client.post(
            f"{CERTIFICATES}/{entry['id']}/documents", json={"document_id": str(theirs.id)}
        )
        assert response.status_code == 404

    async def test_a_viewer_cannot_attach_one(
        self,
        api_client: AsyncClient,
        operator_user: User,
        viewer_user: User,
        db_session: AsyncSession,
        workspace: Workspace,
    ) -> None:
        await authenticate(api_client, operator_user)
        entry = await record(api_client, type_id=await birth_type_id(api_client))
        document = await a_document(db_session, workspace_id=workspace.id)

        await authenticate(api_client, viewer_user)
        response = await api_client.post(
            f"{CERTIFICATES}/{entry['id']}/documents", json={"document_id": str(document.id)}
        )
        assert response.status_code == 403

    async def test_pages_out_of_order_are_refused(
        self,
        api_client: AsyncClient,
        operator_user: User,
        db_session: AsyncSession,
        workspace: Workspace,
    ) -> None:
        await authenticate(api_client, operator_user)
        entry = await record(api_client, type_id=await birth_type_id(api_client))
        document = await a_document(db_session, workspace_id=workspace.id)

        response = await api_client.post(
            f"{CERTIFICATES}/{entry['id']}/documents",
            json={"document_id": str(document.id), "page_start": 5, "page_end": 2},
        )
        assert response.status_code == 422


async def a_document(session: AsyncSession, *, workspace_id: uuid.UUID) -> Document:
    """A stored document row. Its bytes are irrelevant to linking."""
    from certex.db.models import Batch
    from certex.enums import BatchStatus, DocumentStatus

    batch = Batch(
        workspace_id=workspace_id,
        name=f"Batch {uuid.uuid4().hex[:6]}",
        status=BatchStatus.COMPLETED,
        settings_json={},
    )
    session.add(batch)
    await session.flush()

    document = Document(
        batch_id=batch.id,
        workspace_id=workspace_id,
        original_filename="scan.pdf",
        mime_type="application/pdf",
        byte_size=1024,
        sha256=uuid.uuid4().hex * 2,
        storage_key=f"workspaces/{workspace_id}/documents/{uuid.uuid4()}.pdf",
        status=DocumentStatus.COMPLETED,
    )
    session.add(document)
    await session.flush()
    return document
