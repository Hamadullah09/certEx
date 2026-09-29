"""Acting on a register entry: approving, correcting, voiding, settling duplicates.

Every one of these is a person's decision about a historical record, so the tests are
mostly about what survives the decision. A corrected entry has to keep what it used to
say; two entries confirmed to be one certificate both have to remain findable; and a
cancelled certificate has to stay in the register, because whoever is holding it needs
to be told it was cancelled.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from certex.core.security import hash_password
from certex.db.models import Certificate, CertificateName, CertificateRevision, User, Workspace
from certex.enums import CertificateStatus, DuplicateStatus, RevisionAction, UserRole
from tests.conftest import TEST_PASSWORD, authenticate

pytestmark = [pytest.mark.integration]

TYPES = "/api/v1/certificate-types"
CERTIFICATES = "/api/v1/certificates"
SETTINGS = "/api/v1/workspace/settings"

ROW: dict[str, str] = {
    "certificate_number": "BC/LHR/2019/1001",
    "child_full_name": "Ayesha Noor Malik",
    "date_of_birth": "2019-04-03",
    "father_full_name": "Tariq Mahmood Malik",
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
    payload: dict[str, Any] = {
        "certificate_type_id": type_id,
        "values": values or ROW,
        "allow_duplicate": allow_duplicate,
    }
    response = await client.post(CERTIFICATES, json=payload)
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


async def read(client: AsyncClient, certificate_id: str) -> dict[str, Any]:
    response = await client.get(f"{CERTIFICATES}/{certificate_id}")
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


async def history(client: AsyncClient, certificate_id: str) -> list[dict[str, Any]]:
    response = await client.get(f"{CERTIFICATES}/{certificate_id}/history")
    assert response.status_code == 200, response.text
    items: list[dict[str, Any]] = response.json()
    return items


async def a_second_office(session: AsyncSession, role: UserRole = UserRole.OPERATOR) -> User:
    workspace = Workspace(name=f"Other Office {uuid.uuid4().hex[:8]}", settings_json={})
    session.add(workspace)
    await session.flush()
    user = User(
        workspace_id=workspace.id,
        email=f"outsider-{uuid.uuid4().hex[:8]}@example.com",
        password_hash=hash_password(TEST_PASSWORD),
        role=role,
        is_active=True,
    )
    session.add(user)
    await session.flush()
    return user


class TestHistory:
    async def test_an_entry_starts_with_a_creation_revision(
        self, api_client: AsyncClient, operator_user: User
    ) -> None:
        """Nothing in the record should be unexplained, including its existence."""
        await authenticate(api_client, operator_user)
        entry = await record(api_client, type_id=await birth_type_id(api_client))

        items = await history(api_client, entry["id"])
        assert [item["action"] for item in items] == [RevisionAction.CREATED]
        assert items[0]["record_version"] == 1

    async def test_the_history_is_oldest_first(
        self, api_client: AsyncClient, operator_user: User
    ) -> None:
        await authenticate(api_client, operator_user)
        entry = await record(api_client, type_id=await birth_type_id(api_client))
        await api_client.patch(
            f"{CERTIFICATES}/{entry['id']}",
            json={"values": {"child_full_name": "Ayesha Noor"}, "note": "Matches the scan."},
        )
        items = await history(api_client, entry["id"])
        assert [item["record_version"] for item in items] == [1, 2]

    async def test_another_office_cannot_read_a_history(
        self, api_client: AsyncClient, operator_user: User, db_session: AsyncSession
    ) -> None:
        await authenticate(api_client, operator_user)
        entry = await record(api_client, type_id=await birth_type_id(api_client))

        outsider = await a_second_office(db_session)
        await authenticate(api_client, outsider)
        response = await api_client.get(f"{CERTIFICATES}/{entry['id']}/history")
        assert response.status_code == 404


class TestCorrecting:
    async def test_a_value_changes_and_the_old_one_is_kept(
        self, api_client: AsyncClient, operator_user: User
    ) -> None:
        await authenticate(api_client, operator_user)
        entry = await record(api_client, type_id=await birth_type_id(api_client))

        response = await api_client.patch(
            f"{CERTIFICATES}/{entry['id']}",
            json={
                "values": {"child_full_name": "Ayesha Noor Malick"},
                "note": "The scan reads Malick with a c.",
            },
        )
        assert response.status_code == 200, response.text
        assert response.json()["values"]["child_full_name"] == "Ayesha Noor Malick"

        items = await history(api_client, entry["id"])
        assert items[-1]["action"] == RevisionAction.CORRECTED
        assert items[-1]["changed_fields"] == ["child_full_name"]
        assert items[-1]["note"] == "The scan reads Malick with a c."

    async def test_a_correction_bumps_the_version(
        self, api_client: AsyncClient, operator_user: User
    ) -> None:
        await authenticate(api_client, operator_user)
        entry = await record(api_client, type_id=await birth_type_id(api_client))
        assert entry["record_version"] == 1

        response = await api_client.patch(
            f"{CERTIFICATES}/{entry['id']}",
            json={"values": {"child_full_name": "Ayesha N Malik"}, "note": "Shortened."},
        )
        assert response.json()["record_version"] == 2

    async def test_only_the_named_fields_change(
        self, api_client: AsyncClient, operator_user: User
    ) -> None:
        """A screen submitting the whole record would blank what another tab just filled."""
        await authenticate(api_client, operator_user)
        entry = await record(api_client, type_id=await birth_type_id(api_client))

        response = await api_client.patch(
            f"{CERTIFICATES}/{entry['id']}",
            json={"values": {"child_full_name": "Ayesha N Malik"}, "note": "Shortened."},
        )
        values = response.json()["values"]
        assert values["father_full_name"] == "Tariq Mahmood Malik"
        assert values["date_of_birth"] == "2019-04-03"

    async def test_an_empty_value_clears_the_field(
        self, api_client: AsyncClient, operator_user: User
    ) -> None:
        await authenticate(api_client, operator_user)
        entry = await record(api_client, type_id=await birth_type_id(api_client))

        response = await api_client.patch(
            f"{CERTIFICATES}/{entry['id']}",
            json={
                "values": {"father_full_name": ""},
                "note": "The field is blank on the certificate.",
            },
        )
        assert "father_full_name" not in response.json()["values"]

    async def test_a_corrected_name_is_findable_by_its_new_spelling(
        self, api_client: AsyncClient, operator_user: User, db_session: AsyncSession
    ) -> None:
        """A correction that left the keys alone would leave the entry findable only
        by what it used to say."""
        await authenticate(api_client, operator_user)
        entry = await record(api_client, type_id=await birth_type_id(api_client))
        await api_client.patch(
            f"{CERTIFICATES}/{entry['id']}",
            json={"values": {"child_full_name": "Fatima Zahra"}, "note": "Wrong child."},
        )

        found = await api_client.get(f"{CERTIFICATES}/search", params={"q": "Fatima Zahra"})
        assert [hit["certificate"]["id"] for hit in found.json()["items"]] == [entry["id"]]

        keys = list(
            (
                await db_session.scalars(
                    select(CertificateName.value_key).where(
                        CertificateName.certificate_id == uuid.UUID(entry["id"])
                    )
                )
            ).all()
        )
        assert "ayesha noor malik" not in keys, "the stale key must not survive"

    async def test_a_correction_needs_a_reason(
        self, api_client: AsyncClient, operator_user: User
    ) -> None:
        await authenticate(api_client, operator_user)
        entry = await record(api_client, type_id=await birth_type_id(api_client))
        response = await api_client.patch(
            f"{CERTIFICATES}/{entry['id']}",
            json={"values": {"child_full_name": "Someone"}, "note": "   "},
        )
        assert response.status_code == 422

    async def test_a_field_the_schema_does_not_define_is_refused(
        self, api_client: AsyncClient, operator_user: User
    ) -> None:
        await authenticate(api_client, operator_user)
        entry = await record(api_client, type_id=await birth_type_id(api_client))
        response = await api_client.patch(
            f"{CERTIFICATES}/{entry['id']}",
            json={"values": {"favourite_colour": "green"}, "note": "Why not."},
        )
        assert response.status_code == 422

    async def test_changing_nothing_adds_no_history(
        self, api_client: AsyncClient, operator_user: User
    ) -> None:
        await authenticate(api_client, operator_user)
        entry = await record(api_client, type_id=await birth_type_id(api_client))
        await api_client.patch(
            f"{CERTIFICATES}/{entry['id']}",
            json={"values": {"child_full_name": ROW["child_full_name"]}, "note": "No change."},
        )
        assert len(await history(api_client, entry["id"])) == 1

    async def test_a_viewer_cannot_correct(
        self, api_client: AsyncClient, operator_user: User, viewer_user: User
    ) -> None:
        await authenticate(api_client, operator_user)
        entry = await record(api_client, type_id=await birth_type_id(api_client))

        await authenticate(api_client, viewer_user)
        response = await api_client.patch(
            f"{CERTIFICATES}/{entry['id']}",
            json={"values": {"child_full_name": "Someone Else"}, "note": "Because."},
        )
        assert response.status_code == 403

    async def test_a_renumbered_entry_is_no_longer_linked_to_the_other(
        self, api_client: AsyncClient, operator_user: User
    ) -> None:
        """Only a matching number is strong enough to file one entry against another."""
        await authenticate(api_client, operator_user)
        type_id = await birth_type_id(api_client)
        await record(api_client, type_id=type_id)
        second = await record(api_client, type_id=type_id, allow_duplicate=True)
        assert second["duplicate_status"] == DuplicateStatus.SUSPECTED

        response = await api_client.patch(
            f"{CERTIFICATES}/{second['id']}",
            json={
                "values": {"certificate_number": "BC/LHR/2019/1009"},
                "note": "Transposed digits.",
            },
        )
        assert response.json()["duplicate_of_id"] is None

    async def test_but_the_same_person_on_the_same_day_is_still_raised(
        self, api_client: AsyncClient, operator_user: User
    ) -> None:
        """Two entries for one child born on one day are worth asking about however
        their numbers differ - that is the mistyped-number case, in reverse."""
        await authenticate(api_client, operator_user)
        type_id = await birth_type_id(api_client)
        await record(api_client, type_id=type_id)
        second = await record(api_client, type_id=type_id, allow_duplicate=True)

        response = await api_client.patch(
            f"{CERTIFICATES}/{second['id']}",
            json={
                "values": {"certificate_number": "BC/LHR/2019/1009"},
                "note": "Transposed digits.",
            },
        )
        assert response.json()["duplicate_status"] == DuplicateStatus.SUSPECTED

    async def test_a_different_person_entirely_clears_the_question(
        self, api_client: AsyncClient, operator_user: User
    ) -> None:
        await authenticate(api_client, operator_user)
        type_id = await birth_type_id(api_client)
        await record(api_client, type_id=type_id)
        second = await record(api_client, type_id=type_id, allow_duplicate=True)

        response = await api_client.patch(
            f"{CERTIFICATES}/{second['id']}",
            json={
                "values": {
                    "certificate_number": "BC/LHR/2019/1009",
                    "child_full_name": "Bilal Hussain",
                    "date_of_birth": "2018-01-05",
                },
                "note": "This row belonged to a different certificate entirely.",
            },
        )
        assert response.json()["duplicate_status"] == DuplicateStatus.NONE
        assert response.json()["duplicate_of_id"] is None


class TestApproving:
    async def test_approving_clears_the_queue_flag(
        self, api_client: AsyncClient, operator_user: User, db_session: AsyncSession
    ) -> None:
        await authenticate(api_client, operator_user)
        entry = await record(api_client, type_id=await birth_type_id(api_client))
        stored = await db_session.get(Certificate, uuid.UUID(entry["id"]))
        assert stored is not None
        stored.needs_review = True
        await db_session.flush()

        response = await api_client.post(f"{CERTIFICATES}/{entry['id']}/approve", json={})
        assert response.status_code == 200
        assert response.json()["needs_review"] is False

    async def test_approving_is_recorded_as_approval_not_correction(
        self, api_client: AsyncClient, operator_user: User, db_session: AsyncSession
    ) -> None:
        """The two mean different things and must not look the same in the history."""
        await authenticate(api_client, operator_user)
        entry = await record(api_client, type_id=await birth_type_id(api_client))
        stored = await db_session.get(Certificate, uuid.UUID(entry["id"]))
        assert stored is not None
        stored.needs_review = True
        await db_session.flush()

        await api_client.post(f"{CERTIFICATES}/{entry['id']}/approve", json={})
        items = await history(api_client, entry["id"])
        assert items[-1]["action"] == RevisionAction.APPROVED
        assert items[-1]["changed_fields"] == []

    async def test_approving_twice_adds_one_revision(
        self, api_client: AsyncClient, operator_user: User, db_session: AsyncSession
    ) -> None:
        await authenticate(api_client, operator_user)
        entry = await record(api_client, type_id=await birth_type_id(api_client))
        stored = await db_session.get(Certificate, uuid.UUID(entry["id"]))
        assert stored is not None
        stored.needs_review = True
        await db_session.flush()

        await api_client.post(f"{CERTIFICATES}/{entry['id']}/approve", json={})
        await api_client.post(f"{CERTIFICATES}/{entry['id']}/approve", json={})
        actions = [item["action"] for item in await history(api_client, entry["id"])]
        assert actions.count(RevisionAction.APPROVED) == 1

    async def test_an_unsettled_duplicate_has_to_be_dealt_with_first(
        self, api_client: AsyncClient, operator_user: User
    ) -> None:
        await authenticate(api_client, operator_user)
        type_id = await birth_type_id(api_client)
        await record(api_client, type_id=type_id)
        second = await record(api_client, type_id=type_id, allow_duplicate=True)

        response = await api_client.post(f"{CERTIFICATES}/{second['id']}/approve", json={})
        assert response.status_code == 409
        assert response.json()["remediation"]

    async def test_correcting_can_approve_in_one_action(
        self, api_client: AsyncClient, operator_user: User, db_session: AsyncSession
    ) -> None:
        await authenticate(api_client, operator_user)
        entry = await record(api_client, type_id=await birth_type_id(api_client))
        stored = await db_session.get(Certificate, uuid.UUID(entry["id"]))
        assert stored is not None
        stored.needs_review = True
        await db_session.flush()

        response = await api_client.patch(
            f"{CERTIFICATES}/{entry['id']}",
            json={
                "values": {"child_full_name": "Ayesha Noor"},
                "note": "Matches the scan.",
                "approve": True,
            },
        )
        assert response.json()["needs_review"] is False


class TestDuplicates:
    async def test_confirming_supersedes_the_later_entry(
        self, api_client: AsyncClient, operator_user: User
    ) -> None:
        await authenticate(api_client, operator_user)
        type_id = await birth_type_id(api_client)
        first = await record(api_client, type_id=type_id)
        second = await record(api_client, type_id=type_id, allow_duplicate=True)

        response = await api_client.post(
            f"{CERTIFICATES}/{second['id']}/resolve-duplicate",
            json={"other_id": first["id"], "same_certificate": True, "note": "Same child."},
        )
        assert response.status_code == 200, response.text
        earlier, later = response.json()
        assert earlier["id"] == first["id"]
        assert earlier["status"] == CertificateStatus.ACTIVE
        assert later["status"] == CertificateStatus.SUPERSEDED
        assert later["duplicate_status"] == DuplicateStatus.CONFIRMED

    async def test_neither_entry_is_deleted(
        self, api_client: AsyncClient, operator_user: User, db_session: AsyncSession
    ) -> None:
        """The office may have to explain either one to whoever holds a copy."""
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

    async def test_no_values_are_combined(
        self, api_client: AsyncClient, operator_user: User
    ) -> None:
        await authenticate(api_client, operator_user)
        type_id = await birth_type_id(api_client)
        first = await record(api_client, type_id=type_id)
        second = await record(
            api_client,
            type_id=type_id,
            values={**ROW, "child_full_name": "Ayesha Noor"},
            allow_duplicate=True,
        )
        await api_client.post(
            f"{CERTIFICATES}/{second['id']}/resolve-duplicate",
            json={"other_id": first["id"], "same_certificate": True},
        )

        assert (await read(api_client, first["id"]))["values"]["child_full_name"] == (
            "Ayesha Noor Malik"
        )
        assert (await read(api_client, second["id"]))["values"]["child_full_name"] == (
            "Ayesha Noor"
        )

    async def test_calling_them_distinct_stops_the_question_recurring(
        self, api_client: AsyncClient, operator_user: User
    ) -> None:
        await authenticate(api_client, operator_user)
        type_id = await birth_type_id(api_client)
        first = await record(api_client, type_id=type_id)
        second = await record(api_client, type_id=type_id, allow_duplicate=True)

        await api_client.post(
            f"{CERTIFICATES}/{second['id']}/resolve-duplicate",
            json={
                "other_id": first["id"],
                "same_certificate": False,
                "note": "Two offices reused the number.",
            },
        )
        reread = await read(api_client, second["id"])
        assert reread["duplicate_status"] == DuplicateStatus.DISTINCT
        assert reread["status"] == CertificateStatus.ACTIVE
        assert reread["needs_review"] is False

    async def test_a_settled_pair_is_not_reopened_by_a_correction(
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

        response = await api_client.patch(
            f"{CERTIFICATES}/{second['id']}",
            json={"values": {"father_full_name": "Tariq Mahmood"}, "note": "Shortened."},
        )
        assert response.json()["duplicate_status"] == DuplicateStatus.DISTINCT

    async def test_an_entry_cannot_duplicate_itself(
        self, api_client: AsyncClient, operator_user: User
    ) -> None:
        await authenticate(api_client, operator_user)
        entry = await record(api_client, type_id=await birth_type_id(api_client))
        response = await api_client.post(
            f"{CERTIFICATES}/{entry['id']}/resolve-duplicate",
            json={"other_id": entry["id"], "same_certificate": True},
        )
        assert response.status_code == 400

    async def test_another_office_s_entry_cannot_be_named(
        self, api_client: AsyncClient, operator_user: User, db_session: AsyncSession
    ) -> None:
        outsider = await a_second_office(db_session)
        await authenticate(api_client, outsider)
        theirs = await record(api_client, type_id=await birth_type_id(api_client))

        await authenticate(api_client, operator_user)
        mine = await record(api_client, type_id=await birth_type_id(api_client))
        response = await api_client.post(
            f"{CERTIFICATES}/{mine['id']}/resolve-duplicate",
            json={"other_id": theirs["id"], "same_certificate": True},
        )
        assert response.status_code == 404


class TestVoiding:
    async def test_an_administrator_can_void_an_entry(
        self, api_client: AsyncClient, admin_user: User
    ) -> None:
        await authenticate(api_client, admin_user)
        entry = await record(api_client, type_id=await birth_type_id(api_client))

        response = await api_client.post(
            f"{CERTIFICATES}/{entry['id']}/void",
            json={"note": "Issued in error; the applicant withdrew."},
        )
        assert response.status_code == 200, response.text
        assert response.json()["status"] == CertificateStatus.VOID

    async def test_a_void_entry_is_still_readable(
        self, api_client: AsyncClient, admin_user: User
    ) -> None:
        """Whoever holds the certificate has to be told it was cancelled."""
        await authenticate(api_client, admin_user)
        entry = await record(api_client, type_id=await birth_type_id(api_client))
        await api_client.post(f"{CERTIFICATES}/{entry['id']}/void", json={"note": "Withdrawn."})

        reread = await read(api_client, entry["id"])
        assert reread["status"] == CertificateStatus.VOID
        assert reread["values"]["child_full_name"] == "Ayesha Noor Malik"

    async def test_voiding_needs_a_reason(self, api_client: AsyncClient, admin_user: User) -> None:
        await authenticate(api_client, admin_user)
        entry = await record(api_client, type_id=await birth_type_id(api_client))
        response = await api_client.post(f"{CERTIFICATES}/{entry['id']}/void", json={"note": " "})
        assert response.status_code == 422

    async def test_an_operator_cannot_void(
        self, api_client: AsyncClient, admin_user: User, operator_user: User
    ) -> None:
        await authenticate(api_client, admin_user)
        entry = await record(api_client, type_id=await birth_type_id(api_client))

        await authenticate(api_client, operator_user)
        response = await api_client.post(
            f"{CERTIFICATES}/{entry['id']}/void", json={"note": "Because."}
        )
        assert response.status_code == 403

    async def test_a_void_entry_drops_out_of_search(
        self, api_client: AsyncClient, admin_user: User
    ) -> None:
        await authenticate(api_client, admin_user)
        entry = await record(api_client, type_id=await birth_type_id(api_client))
        await api_client.post(f"{CERTIFICATES}/{entry['id']}/void", json={"note": "Withdrawn."})

        found = await api_client.get(
            f"{CERTIFICATES}/search", params={"q": ROW["certificate_number"]}
        )
        assert found.json()["items"] == []


class TestReviewSummary:
    async def test_it_counts_what_is_waiting(
        self, api_client: AsyncClient, operator_user: User
    ) -> None:
        await authenticate(api_client, operator_user)
        type_id = await birth_type_id(api_client)
        await record(api_client, type_id=type_id)
        await record(api_client, type_id=type_id, allow_duplicate=True)

        summary = (await api_client.get(f"{CERTIFICATES}/review-summary")).json()
        assert summary["needs_review"] == 1
        assert summary["suspected_duplicates"] == 1

    async def test_an_empty_register_counts_nothing(
        self, api_client: AsyncClient, operator_user: User
    ) -> None:
        await authenticate(api_client, operator_user)
        summary = (await api_client.get(f"{CERTIFICATES}/review-summary")).json()
        assert summary == {"needs_review": 0, "suspected_duplicates": 0}

    async def test_another_office_s_queue_is_not_counted(
        self, api_client: AsyncClient, operator_user: User, db_session: AsyncSession
    ) -> None:
        outsider = await a_second_office(db_session)
        await authenticate(api_client, outsider)
        type_id = await birth_type_id(api_client)
        await record(api_client, type_id=type_id)
        await record(api_client, type_id=type_id, allow_duplicate=True)

        await authenticate(api_client, operator_user)
        summary = (await api_client.get(f"{CERTIFICATES}/review-summary")).json()
        assert summary["needs_review"] == 0


class TestSettings:
    async def test_the_defaults_review_everything(
        self, api_client: AsyncClient, operator_user: User
    ) -> None:
        """The safe position for an office that has not decided yet."""
        await authenticate(api_client, operator_user)
        settings = (await api_client.get(SETTINGS)).json()
        assert settings["review"]["confidence_auto_approve"] == 1.0
        assert settings["review"]["review_suspected_duplicates"] is True

    async def test_an_administrator_can_change_them(
        self, api_client: AsyncClient, admin_user: User
    ) -> None:
        await authenticate(api_client, admin_user)
        response = await api_client.put(
            SETTINGS,
            json={
                "review": {
                    "confidence_auto_approve": 0.95,
                    "confidence_review_floor": 0.6,
                    "review_imported_records": True,
                    "review_suspected_duplicates": True,
                }
            },
        )
        assert response.status_code == 200, response.text
        assert response.json()["review"]["confidence_auto_approve"] == 0.95
        assert (await api_client.get(SETTINGS)).json()["review"]["confidence_review_floor"] == 0.6

    async def test_contradictory_thresholds_are_refused(
        self, api_client: AsyncClient, admin_user: User
    ) -> None:
        await authenticate(api_client, admin_user)
        response = await api_client.put(
            SETTINGS,
            json={"review": {"confidence_auto_approve": 0.5, "confidence_review_floor": 0.9}},
        )
        assert response.status_code == 422

    async def test_an_operator_cannot_change_them(
        self, api_client: AsyncClient, operator_user: User
    ) -> None:
        await authenticate(api_client, operator_user)
        response = await api_client.put(SETTINGS, json={"review": {}})
        assert response.status_code == 403

    async def test_turning_off_duplicate_review_still_records_the_duplicate(
        self, api_client: AsyncClient, admin_user: User
    ) -> None:
        """Not queueing work is not the same as merging records."""
        await authenticate(api_client, admin_user)
        await api_client.put(SETTINGS, json={"review": {"review_suspected_duplicates": False}})

        type_id = await birth_type_id(api_client)
        first = await record(api_client, type_id=type_id)
        second = await record(api_client, type_id=type_id, allow_duplicate=True)

        assert second["duplicate_status"] == DuplicateStatus.SUSPECTED
        assert second["duplicate_of_id"] == first["id"]
        assert second["needs_review"] is False

    async def test_a_revision_never_records_a_value_in_its_note(
        self, api_client: AsyncClient, operator_user: User, db_session: AsyncSession
    ) -> None:
        """The note is a person's words. The values live on the revision itself,
        which is inside the register's own trust boundary."""
        await authenticate(api_client, operator_user)
        entry = await record(api_client, type_id=await birth_type_id(api_client))
        await api_client.patch(
            f"{CERTIFICATES}/{entry['id']}",
            json={"values": {"child_full_name": "Ayesha Noor"}, "note": "Matches the scan."},
        )

        revisions = list(
            (
                await db_session.scalars(
                    select(CertificateRevision).where(
                        CertificateRevision.certificate_id == uuid.UUID(entry["id"])
                    )
                )
            ).all()
        )
        latest = max(revisions, key=lambda revision: revision.record_version)
        assert latest.values_jsonb["child_full_name"] == "Ayesha Noor"
        assert "Ayesha" not in (latest.note or "")
