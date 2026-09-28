"""The review API: reading rows, correcting them, and the pages behind them.

What a reviewer does all day is: filter to the rows that need attention, look at a value
beside the page it came from, fix it, and move on. These tests are that loop - with
particular attention to the two things that would make reviewing pointless: a correction
that does not survive, and a corrected row that keeps its old verdict.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from certex.api.v1 import rows as rows_api
from certex.db.models import (
    AuditLog,
    Batch,
    CertificateUnit,
    Document,
    Extraction,
    FieldCorrection,
    User,
)
from certex.enums import (
    AuditAction,
    CertificateType,
    DocumentStatus,
    ExtractionMethod,
    ReviewStatus,
    UnitStatus,
    ValidationFlag,
)
from certex.pipeline.dispatch import TASK_EXTRACT_UNIT, TaskArgument
from tests.conftest import authenticate

pytestmark = pytest.mark.integration

BIRTH_FIELDS = {
    "certificate_number": "BC-2019-004471",
    "child_full_name": "Ayesha Noor Malik",
    "date_of_birth": "1987-03-14",
    "father_full_name": "Tarig Mahmood Malik",
    "sex": "F",
}


@pytest.fixture
def enqueued(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict[str, TaskArgument]]]:
    calls: list[tuple[str, dict[str, TaskArgument]]] = []

    def record(task_name: str, kwargs: dict[str, TaskArgument]) -> None:
        calls.append((task_name, kwargs))

    monkeypatch.setattr(rows_api, "enqueue", record)
    return calls


async def make_row(
    session: AsyncSession,
    batch: Batch,
    *,
    fields: dict[str, str] | None = None,
    certificate_type: CertificateType = CertificateType.BIRTH,
    review_status: ReviewStatus = ReviewStatus.NEEDS_REVIEW,
    flags: list[str] | None = None,
    confidence: float = 0.9,
    file_name: str = "birth.pdf",
) -> Extraction:
    document = Document(
        batch_id=batch.id,
        workspace_id=batch.workspace_id,
        original_filename=file_name,
        mime_type="application/pdf",
        byte_size=2048,
        sha256=uuid.uuid4().hex * 2,
        storage_key=f"documents/{batch.workspace_id}/2026/09/{uuid.uuid4()}",
        status=DocumentStatus.COMPLETED,
        page_count=1,
    )
    session.add(document)
    await session.flush()

    unit = CertificateUnit(
        document_id=document.id,
        batch_id=batch.id,
        ordinal=0,
        page_start=1,
        page_end=1,
        certificate_type=certificate_type,
        type_confidence=0.9,
        status=UnitStatus.COMPLETED,
    )
    session.add(unit)
    await session.flush()

    values = BIRTH_FIELDS if fields is None else fields
    row = Extraction(
        unit_id=unit.id,
        batch_id=batch.id,
        workspace_id=batch.workspace_id,
        certificate_type=certificate_type,
        fields_jsonb=dict(values),
        field_confidences_jsonb=dict.fromkeys(values, 0.95),
        field_methods_jsonb=dict.fromkeys(values, ExtractionMethod.RULE.value),
        field_sources_jsonb={
            name: {
                "page_number": 1,
                "snippet": value,
                "bbox": {"x0": 0.1, "y0": 0.2, "x1": 0.5, "y1": 0.24},
                "label": name.replace("_", " ").title(),
            }
            for name, value in values.items()
        },
        flags_jsonb=flags or [],
        field_issues_jsonb=[],
        row_confidence=confidence,
        review_status=review_status,
    )
    session.add(row)
    await session.flush()
    return row


class TestListingRows:
    async def test_the_rows_of_a_batch_come_back(
        self, api_client: AsyncClient, db_session: AsyncSession, operator_user: User, batch: Batch
    ) -> None:
        await make_row(db_session, batch)
        await make_row(db_session, batch, file_name="death.pdf")
        await authenticate(api_client, operator_user)

        response = await api_client.get(f"/api/v1/batches/{batch.id}/rows")

        assert response.status_code == 200, response.text
        items = response.json()["items"]
        assert len(items) == 2
        assert items[0]["fields"]["child_full_name"] == "Ayesha Noor Malik"
        assert items[0]["file_name"] == "birth.pdf"

    async def test_each_row_carries_its_serial_number(
        self, api_client: AsyncClient, db_session: AsyncSession, operator_user: User, batch: Batch
    ) -> None:
        # Column one of the CSV, and how an operator refers to a row on the telephone.
        for _ in range(3):
            await make_row(db_session, batch)
        await authenticate(api_client, operator_user)

        response = await api_client.get(f"/api/v1/batches/{batch.id}/rows")

        assert [item["serial_no"] for item in response.json()["items"]] == [1, 2, 3]

    async def test_rows_can_be_filtered_by_what_needs_attention(
        self, api_client: AsyncClient, db_session: AsyncSession, operator_user: User, batch: Batch
    ) -> None:
        await make_row(db_session, batch, review_status=ReviewStatus.MANUALLY_APPROVED)
        await make_row(db_session, batch, review_status=ReviewStatus.FAILED)
        await authenticate(api_client, operator_user)

        response = await api_client.get(
            f"/api/v1/batches/{batch.id}/rows?filter[status]={ReviewStatus.FAILED.value}"
        )

        items = response.json()["items"]
        assert len(items) == 1
        assert items[0]["review_status"] == ReviewStatus.FAILED.value

    async def test_rows_can_be_filtered_by_flag(
        self, api_client: AsyncClient, db_session: AsyncSession, operator_user: User, batch: Batch
    ) -> None:
        await make_row(db_session, batch, flags=[ValidationFlag.DATE_AMBIGUOUS.value])
        await make_row(db_session, batch, flags=[ValidationFlag.CNIC_INVALID.value])
        await authenticate(api_client, operator_user)

        response = await api_client.get(
            f"/api/v1/batches/{batch.id}/rows?filter[flag]={ValidationFlag.CNIC_INVALID.value}"
        )

        items = response.json()["items"]
        assert len(items) == 1
        assert items[0]["flags"] == [ValidationFlag.CNIC_INVALID.value]

    async def test_rows_can_be_filtered_by_certificate_type(
        self, api_client: AsyncClient, db_session: AsyncSession, operator_user: User, batch: Batch
    ) -> None:
        await make_row(db_session, batch)
        await make_row(
            db_session,
            batch,
            certificate_type=CertificateType.DEATH,
            fields={"certificate_number": "DC-1", "deceased_full_name": "Abdul Rehman"},
        )
        await authenticate(api_client, operator_user)

        response = await api_client.get(f"/api/v1/batches/{batch.id}/rows?filter[type]=DEATH")

        assert len(response.json()["items"]) == 1

    async def test_rows_can_be_searched_by_value(
        self, api_client: AsyncClient, db_session: AsyncSession, operator_user: User, batch: Batch
    ) -> None:
        await make_row(db_session, batch)
        await make_row(
            db_session,
            batch,
            fields={"certificate_number": "BC-2", "child_full_name": "Bilal Ahmed"},
        )
        await authenticate(api_client, operator_user)

        response = await api_client.get(f"/api/v1/batches/{batch.id}/rows?search=Bilal")

        items = response.json()["items"]
        assert len(items) == 1
        assert items[0]["fields"]["child_full_name"] == "Bilal Ahmed"

    async def test_the_total_can_be_asked_for(
        self, api_client: AsyncClient, db_session: AsyncSession, operator_user: User, batch: Batch
    ) -> None:
        for _ in range(3):
            await make_row(db_session, batch)
        await authenticate(api_client, operator_user)

        response = await api_client.get(
            f"/api/v1/batches/{batch.id}/rows?limit=2&include_total=true"
        )

        body = response.json()
        assert body["meta"]["total"] == 3
        assert body["meta"]["has_more"] is True

    async def test_viewing_rows_is_recorded(
        self,
        api_client: AsyncClient,
        db_session: AsyncSession,
        operator_user: User,
        batch: Batch,
    ) -> None:
        # Who looked at certificate details, and when.
        await make_row(db_session, batch)
        await authenticate(api_client, operator_user)

        await api_client.get(f"/api/v1/batches/{batch.id}/rows")

        entry = await db_session.scalar(
            select(AuditLog).where(AuditLog.action == AuditAction.ROWS_VIEWED)
        )
        assert entry is not None
        assert entry.entity_id == batch.id

    async def test_another_workspaces_rows_are_not_listed(
        self, api_client: AsyncClient, db_session: AsyncSession, operator_user: User, batch: Batch
    ) -> None:
        from certex.db.models import Workspace

        other_workspace = Workspace(name="Another Office", settings_json={})
        db_session.add(other_workspace)
        await db_session.flush()
        other_batch = Batch(workspace_id=other_workspace.id, name="Theirs", settings_json={})
        db_session.add(other_batch)
        await db_session.flush()
        await make_row(db_session, other_batch)
        await authenticate(api_client, operator_user)

        response = await api_client.get(f"/api/v1/batches/{other_batch.id}/rows")

        assert response.status_code == 404


class TestOneRow:
    async def test_every_field_of_the_type_is_returned_whether_found_or_not(
        self, api_client: AsyncClient, db_session: AsyncSession, operator_user: User, batch: Batch
    ) -> None:
        # The review grid shows a column per field; a missing value is a blank cell, not
        # a missing column.
        row = await make_row(db_session, batch)
        await authenticate(api_client, operator_user)

        response = await api_client.get(f"/api/v1/rows/{row.id}")

        names = [value["name"] for value in response.json()["values"]]
        assert "mother_full_name" in names, "a field that was not read is still a column"
        assert "child_full_name" in names

    async def test_a_value_says_where_it_came_from(
        self, api_client: AsyncClient, db_session: AsyncSession, operator_user: User, batch: Batch
    ) -> None:
        row = await make_row(db_session, batch)
        await authenticate(api_client, operator_user)

        response = await api_client.get(f"/api/v1/rows/{row.id}")

        value = next(
            item for item in response.json()["values"] if item["name"] == "child_full_name"
        )
        assert value["page_number"] == 1
        assert value["bbox"]["x0"] == 0.1
        assert value["snippet"] == "Ayesha Noor Malik"
        assert value["method"] == ExtractionMethod.RULE.value

    async def test_the_pages_this_row_covers_are_listed(
        self, api_client: AsyncClient, db_session: AsyncSession, operator_user: User, batch: Batch
    ) -> None:
        row = await make_row(db_session, batch)
        await authenticate(api_client, operator_user)

        response = await api_client.get(f"/api/v1/rows/{row.id}")

        assert response.json()["page_numbers"] == [1]

    async def test_a_row_that_does_not_exist(
        self, api_client: AsyncClient, operator_user: User
    ) -> None:
        await authenticate(api_client, operator_user)
        response = await api_client.get(f"/api/v1/rows/{uuid.uuid4()}")
        assert response.status_code == 404


class TestCorrectingARow:
    async def test_a_corrected_value_is_stored(
        self,
        api_client: AsyncClient,
        db_session: AsyncSession,
        operator_user: User,
        batch: Batch,
    ) -> None:
        row = await make_row(db_session, batch)
        await authenticate(api_client, operator_user)

        response = await api_client.patch(
            f"/api/v1/rows/{row.id}",
            json={"fields": {"father_full_name": "Tariq Mahmood Malik"}},
        )

        assert response.status_code == 200, response.text
        assert response.json()["fields"]["father_full_name"] == "Tariq Mahmood Malik"

    async def test_a_corrected_value_is_marked_as_a_persons_word(
        self,
        api_client: AsyncClient,
        db_session: AsyncSession,
        operator_user: User,
        batch: Batch,
    ) -> None:
        row = await make_row(db_session, batch)
        await authenticate(api_client, operator_user)

        response = await api_client.patch(
            f"/api/v1/rows/{row.id}",
            json={"fields": {"father_full_name": "Tariq Mahmood Malik"}},
        )

        value = next(
            item for item in response.json()["values"] if item["name"] == "father_full_name"
        )
        assert value["method"] == ExtractionMethod.MANUAL.value
        assert value["confidence"] == 1.0

    async def test_a_corrected_date_is_stored_the_way_every_date_is(
        self,
        api_client: AsyncClient,
        db_session: AsyncSession,
        operator_user: User,
        batch: Batch,
    ) -> None:
        # A reviewer types what the certificate says; the CSV still gets ISO.
        row = await make_row(db_session, batch)
        await authenticate(api_client, operator_user)

        response = await api_client.patch(
            f"/api/v1/rows/{row.id}", json={"fields": {"date_of_birth": "14-03-1987"}}
        )

        assert response.json()["fields"]["date_of_birth"] == "1987-03-14"

    async def test_every_edit_is_kept(
        self,
        api_client: AsyncClient,
        db_session: AsyncSession,
        operator_user: User,
        batch: Batch,
    ) -> None:
        # The record of what a person changed is what templates are learned from.
        row = await make_row(db_session, batch)
        await authenticate(api_client, operator_user)

        await api_client.patch(
            f"/api/v1/rows/{row.id}",
            json={"fields": {"father_full_name": "Tariq Mahmood Malik"}},
        )

        correction = await db_session.scalar(
            select(FieldCorrection).where(FieldCorrection.extraction_id == row.id)
        )
        assert correction is not None
        assert correction.field_name == "father_full_name"
        assert correction.old_value == "Tarig Mahmood Malik"
        assert correction.new_value == "Tariq Mahmood Malik"
        assert correction.corrected_by == operator_user.id

    async def test_a_correction_is_audited_without_copying_the_value(
        self,
        api_client: AsyncClient,
        db_session: AsyncSession,
        operator_user: User,
        batch: Batch,
    ) -> None:
        row = await make_row(db_session, batch)
        await authenticate(api_client, operator_user)

        await api_client.patch(
            f"/api/v1/rows/{row.id}",
            json={"fields": {"father_full_name": "Tariq Mahmood Malik"}},
        )

        entry = await db_session.scalar(
            select(AuditLog).where(AuditLog.action == AuditAction.EXTRACTION_CORRECTED)
        )
        assert entry is not None
        assert entry.metadata_jsonb == {"field_names": ["father_full_name"], "count": 1}
        assert "Tariq" not in str(entry.metadata_jsonb)

    async def test_clearing_a_field_removes_it(
        self,
        api_client: AsyncClient,
        db_session: AsyncSession,
        operator_user: User,
        batch: Batch,
    ) -> None:
        row = await make_row(db_session, batch)
        await authenticate(api_client, operator_user)

        response = await api_client.patch(f"/api/v1/rows/{row.id}", json={"fields": {"sex": None}})

        assert response.json()["fields"].get("sex") is None

    async def test_an_unknown_field_is_refused(
        self,
        api_client: AsyncClient,
        db_session: AsyncSession,
        operator_user: User,
        batch: Batch,
    ) -> None:
        row = await make_row(db_session, batch)
        await authenticate(api_client, operator_user)

        response = await api_client.patch(
            f"/api/v1/rows/{row.id}", json={"fields": {"favourite_colour": "blue"}}
        )

        assert response.status_code == 400
        assert "favourite_colour" in response.json()["detail"]

    async def test_a_viewer_cannot_correct(
        self, api_client: AsyncClient, db_session: AsyncSession, viewer_user: User, batch: Batch
    ) -> None:
        row = await make_row(db_session, batch)
        await authenticate(api_client, viewer_user)

        response = await api_client.patch(f"/api/v1/rows/{row.id}", json={"fields": {"sex": "M"}})

        assert response.status_code == 403


class TestCorrectionChangesTheVerdict:
    async def test_fixing_the_problem_clears_the_flag(
        self,
        api_client: AsyncClient,
        db_session: AsyncSession,
        operator_user: User,
        batch: Batch,
    ) -> None:
        # A row a reviewer has fixed must stop being flagged, or the queue never empties.
        row = await make_row(
            db_session,
            batch,
            fields={**BIRTH_FIELDS, "father_id_number": "not a number"},
            flags=[ValidationFlag.CNIC_INVALID.value],
        )
        await authenticate(api_client, operator_user)

        response = await api_client.patch(
            f"/api/v1/rows/{row.id}",
            json={"fields": {"father_id_number": "35201-1234567-1"}},
        )

        assert ValidationFlag.CNIC_INVALID.value not in response.json()["flags"]

    async def test_breaking_the_row_raises_a_flag_immediately(
        self,
        api_client: AsyncClient,
        db_session: AsyncSession,
        operator_user: User,
        batch: Batch,
    ) -> None:
        row = await make_row(db_session, batch)
        await authenticate(api_client, operator_user)

        response = await api_client.patch(
            f"/api/v1/rows/{row.id}", json={"fields": {"father_id_number": "12345"}}
        )

        assert ValidationFlag.CNIC_INVALID.value in response.json()["flags"]

    async def test_correcting_re_scores_the_row(
        self,
        api_client: AsyncClient,
        db_session: AsyncSession,
        operator_user: User,
        batch: Batch,
    ) -> None:
        row = await make_row(db_session, batch, confidence=0.2)
        await authenticate(api_client, operator_user)

        response = await api_client.patch(
            f"/api/v1/rows/{row.id}",
            json={"fields": {"father_full_name": "Tariq Mahmood Malik"}},
        )

        assert response.json()["row_confidence"] != 0.2

    async def test_a_reviewer_can_approve_the_row(
        self,
        api_client: AsyncClient,
        db_session: AsyncSession,
        operator_user: User,
        batch: Batch,
    ) -> None:
        # The office reviews every row, so approving is how a row leaves the queue.
        row = await make_row(db_session, batch)
        await authenticate(api_client, operator_user)

        response = await api_client.patch(
            f"/api/v1/rows/{row.id}",
            json={"fields": {"father_full_name": "Tariq Mahmood Malik"}, "approve": True},
        )

        body = response.json()
        assert body["review_status"] == ReviewStatus.MANUALLY_APPROVED.value
        assert body["reviewed_at"] is not None

    async def test_approving_is_audited(
        self,
        api_client: AsyncClient,
        db_session: AsyncSession,
        operator_user: User,
        batch: Batch,
    ) -> None:
        row = await make_row(db_session, batch)
        await authenticate(api_client, operator_user)

        await api_client.patch(f"/api/v1/rows/{row.id}", json={"fields": {}, "approve": True})

        entry = await db_session.scalar(
            select(AuditLog).where(AuditLog.action == AuditAction.EXTRACTION_APPROVED)
        )
        assert entry is not None


class TestReprocessing:
    async def test_a_row_can_be_sent_back_to_be_read_again(
        self,
        api_client: AsyncClient,
        db_session: AsyncSession,
        operator_user: User,
        batch: Batch,
        enqueued: list[tuple[str, dict[str, TaskArgument]]],
    ) -> None:
        row = await make_row(db_session, batch)
        await authenticate(api_client, operator_user)

        response = await api_client.post(f"/api/v1/rows/{row.id}/reprocess")

        assert response.status_code == 202
        assert enqueued == [(TASK_EXTRACT_UNIT, {"unit_id": str(row.unit_id)})]

    async def test_the_unit_goes_back_to_where_extraction_starts(
        self,
        api_client: AsyncClient,
        db_session: AsyncSession,
        operator_user: User,
        batch: Batch,
        enqueued: list[tuple[str, dict[str, TaskArgument]]],
    ) -> None:
        row = await make_row(db_session, batch)
        await authenticate(api_client, operator_user)

        await api_client.post(f"/api/v1/rows/{row.id}/reprocess")

        unit = await db_session.get(CertificateUnit, row.unit_id)
        assert unit is not None
        assert unit.status is UnitStatus.CLASSIFIED

    async def test_reprocessing_is_audited(
        self,
        api_client: AsyncClient,
        db_session: AsyncSession,
        operator_user: User,
        batch: Batch,
        enqueued: list[tuple[str, dict[str, TaskArgument]]],
    ) -> None:
        row = await make_row(db_session, batch)
        await authenticate(api_client, operator_user)

        await api_client.post(f"/api/v1/rows/{row.id}/reprocess")

        entry = await db_session.scalar(
            select(AuditLog).where(AuditLog.action == AuditAction.EXTRACTION_REPROCESSED)
        )
        assert entry is not None

    async def test_a_viewer_cannot_reprocess(
        self,
        api_client: AsyncClient,
        db_session: AsyncSession,
        viewer_user: User,
        batch: Batch,
        enqueued: list[tuple[str, dict[str, TaskArgument]]],
    ) -> None:
        row = await make_row(db_session, batch)
        await authenticate(api_client, viewer_user)

        response = await api_client.post(f"/api/v1/rows/{row.id}/reprocess")

        assert response.status_code == 403
        assert enqueued == []


class TestAuthentication:
    async def test_signing_in_is_required(self, api_client: AsyncClient, batch: Batch) -> None:
        response: Any = await api_client.get(f"/api/v1/batches/{batch.id}/rows")
        assert response.status_code == 401
