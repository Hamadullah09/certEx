"""Seeing where the boundaries fell, and correcting them.

Splitting and merging are how a reviewer fixes the two ways boundary detection goes
wrong. Both have to leave the data consistent: page ranges that still tile the
document, ordinals in page order, no extraction left attached to a range it no longer
describes, and the affected units queued to be read again.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from certex.api.v1 import units as units_api
from certex.db.models import AuditLog, Batch, CertificateUnit, Document, Extraction, User
from certex.enums import (
    AuditAction,
    BoundaryMethod,
    CertificateType,
    ClassificationMethod,
    DocumentStatus,
    ReviewStatus,
    UnitStatus,
)
from certex.pipeline.dispatch import TASK_CLASSIFY_UNIT, TaskArgument
from tests.conftest import authenticate

pytestmark = pytest.mark.integration


@pytest.fixture
def enqueued(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict[str, TaskArgument]]]:
    calls: list[tuple[str, dict[str, TaskArgument]]] = []

    def record(task_name: str, kwargs: dict[str, TaskArgument]) -> None:
        calls.append((task_name, kwargs))

    monkeypatch.setattr(units_api, "enqueue", record)
    return calls


async def make_document(session: AsyncSession, batch: Batch, *, pages: int = 6) -> Document:
    document = Document(
        batch_id=batch.id,
        workspace_id=batch.workspace_id,
        original_filename="register.pdf",
        mime_type="application/pdf",
        byte_size=4096,
        sha256=uuid.uuid4().hex * 2,
        storage_key=f"documents/{batch.workspace_id}/2026/09/{uuid.uuid4()}",
        status=DocumentStatus.CLASSIFYING,
        page_count=pages,
    )
    session.add(document)
    await session.flush()
    return document


async def make_units(
    session: AsyncSession, document: Document, ranges: list[tuple[int, int]]
) -> list[CertificateUnit]:
    units = [
        CertificateUnit(
            document_id=document.id,
            batch_id=document.batch_id,
            ordinal=ordinal,
            page_start=start,
            page_end=end,
            boundary_method=BoundaryMethod.CONTENT_HEADER,
            boundary_confidence=0.85,
            certificate_type=CertificateType.BIRTH,
            type_confidence=0.9,
            classification_method=ClassificationMethod.KEYWORD,
            status=UnitStatus.CLASSIFIED,
        )
        for ordinal, (start, end) in enumerate(ranges)
    ]
    session.add_all(units)
    await session.flush()
    return units


async def make_extraction(
    session: AsyncSession, unit: CertificateUnit, workspace_id: uuid.UUID
) -> Extraction:
    extraction = Extraction(
        unit_id=unit.id,
        batch_id=unit.batch_id,
        workspace_id=workspace_id,
        certificate_type=unit.certificate_type,
        fields_jsonb={"certificate_number": {"value": "BC-2019-004471"}},
        flags_jsonb=[],
        row_confidence=0.9,
        review_status=ReviewStatus.AUTO_APPROVED,
    )
    session.add(extraction)
    await session.flush()
    return extraction


async def read_units(session: AsyncSession, document_id: uuid.UUID) -> list[CertificateUnit]:
    return list(
        (
            await session.scalars(
                select(CertificateUnit)
                .where(CertificateUnit.document_id == document_id)
                .order_by(CertificateUnit.page_start)
            )
        ).all()
    )


class TestListing:
    async def test_units_of_a_batch_are_listed_in_order(
        self,
        api_client: AsyncClient,
        db_session: AsyncSession,
        operator_user: User,
        batch: Batch,
    ) -> None:
        document = await make_document(db_session, batch)
        await make_units(db_session, document, [(1, 2), (3, 4), (5, 6)])
        await authenticate(api_client, operator_user)

        response = await api_client.get(f"/api/v1/units?batch_id={batch.id}")

        assert response.status_code == 200, response.text
        items = response.json()["items"]
        assert [item["page_start"] for item in items] == [1, 3, 5]
        assert items[0]["certificate_type"] == CertificateType.BIRTH.value
        assert items[0]["boundary_method"] == BoundaryMethod.CONTENT_HEADER.value

    async def test_units_can_be_filtered_to_one_file(
        self,
        api_client: AsyncClient,
        db_session: AsyncSession,
        operator_user: User,
        batch: Batch,
    ) -> None:
        first = await make_document(db_session, batch)
        second = await make_document(db_session, batch)
        await make_units(db_session, first, [(1, 1)])
        await make_units(db_session, second, [(1, 1), (2, 2)])
        await authenticate(api_client, operator_user)

        response = await api_client.get(f"/api/v1/units?document_id={second.id}")

        assert len(response.json()["items"]) == 2

    async def test_a_viewer_may_look(
        self,
        api_client: AsyncClient,
        db_session: AsyncSession,
        viewer_user: User,
        batch: Batch,
    ) -> None:
        document = await make_document(db_session, batch)
        await make_units(db_session, document, [(1, 1)])
        await authenticate(api_client, viewer_user)

        response = await api_client.get(f"/api/v1/units?batch_id={batch.id}")

        assert response.status_code == 200

    async def test_another_workspace_sees_nothing(
        self,
        api_client: AsyncClient,
        db_session: AsyncSession,
        operator_user: User,
        batch: Batch,
    ) -> None:
        from certex.db.models import Workspace

        other_workspace = Workspace(name="Another Office", settings_json={})
        db_session.add(other_workspace)
        await db_session.flush()
        other_batch = Batch(workspace_id=other_workspace.id, name="Theirs", settings_json={})
        db_session.add(other_batch)
        await db_session.flush()
        other_document = await make_document(db_session, other_batch)
        await make_units(db_session, other_document, [(1, 1)])
        await authenticate(api_client, operator_user)

        response = await api_client.get("/api/v1/units")

        assert response.json()["items"] == []
        del batch


class TestSplitting:
    async def test_a_unit_is_split_in_two_at_the_page(
        self,
        api_client: AsyncClient,
        db_session: AsyncSession,
        operator_user: User,
        batch: Batch,
        enqueued: list[tuple[str, dict[str, TaskArgument]]],
    ) -> None:
        document = await make_document(db_session, batch)
        (unit,) = await make_units(db_session, document, [(1, 6)])
        await authenticate(api_client, operator_user)

        response = await api_client.post(f"/api/v1/units/{unit.id}/split", json={"at_page": 4})

        assert response.status_code == 200, response.text
        body = response.json()
        assert [(item["page_start"], item["page_end"]) for item in body] == [(1, 3), (4, 6)]
        assert [item["ordinal"] for item in body] == [0, 1]

    async def test_the_pages_still_tile_the_document(
        self,
        api_client: AsyncClient,
        db_session: AsyncSession,
        operator_user: User,
        batch: Batch,
        enqueued: list[tuple[str, dict[str, TaskArgument]]],
    ) -> None:
        document = await make_document(db_session, batch)
        units = await make_units(db_session, document, [(1, 3), (4, 6)])
        await authenticate(api_client, operator_user)

        await api_client.post(f"/api/v1/units/{units[0].id}/split", json={"at_page": 3})

        rows = await read_units(db_session, document.id)
        assert [(unit.page_start, unit.page_end) for unit in rows] == [(1, 2), (3, 3), (4, 6)]
        assert [unit.ordinal for unit in rows] == [0, 1, 2]

    async def test_both_halves_go_back_to_be_read_again(
        self,
        api_client: AsyncClient,
        db_session: AsyncSession,
        operator_user: User,
        batch: Batch,
        enqueued: list[tuple[str, dict[str, TaskArgument]]],
    ) -> None:
        document = await make_document(db_session, batch)
        (unit,) = await make_units(db_session, document, [(1, 6)])
        await make_extraction(db_session, unit, batch.workspace_id)
        await authenticate(api_client, operator_user)

        response = await api_client.post(f"/api/v1/units/{unit.id}/split", json={"at_page": 4})

        rows = await read_units(db_session, document.id)
        for row in rows:
            assert row.status is UnitStatus.TEXT_READY
            assert row.boundary_method is BoundaryMethod.MANUAL
            assert row.boundary_confidence == 1.0
            assert row.certificate_type is CertificateType.OTHER
        # The old row described pages that no longer belong together.
        remaining = await db_session.scalars(
            select(Extraction).where(Extraction.unit_id.in_([row.id for row in rows]))
        )
        assert list(remaining) == []
        assert enqueued == [
            (TASK_CLASSIFY_UNIT, {"unit_id": item["id"]}) for item in response.json()
        ]

    async def test_splitting_is_recorded_in_the_audit_log(
        self,
        api_client: AsyncClient,
        db_session: AsyncSession,
        operator_user: User,
        batch: Batch,
        enqueued: list[tuple[str, dict[str, TaskArgument]]],
    ) -> None:
        document = await make_document(db_session, batch)
        (unit,) = await make_units(db_session, document, [(1, 6)])
        await authenticate(api_client, operator_user)

        await api_client.post(f"/api/v1/units/{unit.id}/split", json={"at_page": 4})

        entry = await db_session.scalar(
            select(AuditLog).where(AuditLog.action == AuditAction.UNIT_SPLIT)
        )
        assert entry is not None
        assert entry.entity_id == unit.id
        assert entry.metadata_jsonb == {"page_number": 4, "count": 2}

    @pytest.mark.parametrize("at_page", [1, 7, 99])
    async def test_a_page_outside_the_unit_is_refused(
        self,
        api_client: AsyncClient,
        db_session: AsyncSession,
        operator_user: User,
        batch: Batch,
        enqueued: list[tuple[str, dict[str, TaskArgument]]],
        at_page: int,
    ) -> None:
        document = await make_document(db_session, batch)
        (unit,) = await make_units(db_session, document, [(1, 6)])
        await authenticate(api_client, operator_user)

        response = await api_client.post(
            f"/api/v1/units/{unit.id}/split", json={"at_page": at_page}
        )

        assert response.status_code in (400, 422)
        assert enqueued == []

    async def test_a_viewer_cannot_split(
        self,
        api_client: AsyncClient,
        db_session: AsyncSession,
        viewer_user: User,
        batch: Batch,
        enqueued: list[tuple[str, dict[str, TaskArgument]]],
    ) -> None:
        document = await make_document(db_session, batch)
        (unit,) = await make_units(db_session, document, [(1, 6)])
        await authenticate(api_client, viewer_user)

        response = await api_client.post(f"/api/v1/units/{unit.id}/split", json={"at_page": 4})

        assert response.status_code == 403
        assert enqueued == []


class TestMerging:
    async def test_adjacent_units_become_one(
        self,
        api_client: AsyncClient,
        db_session: AsyncSession,
        operator_user: User,
        batch: Batch,
        enqueued: list[tuple[str, dict[str, TaskArgument]]],
    ) -> None:
        document = await make_document(db_session, batch)
        units = await make_units(db_session, document, [(1, 2), (3, 4), (5, 6)])
        await authenticate(api_client, operator_user)

        response = await api_client.post(
            "/api/v1/units/merge",
            json={"unit_ids": [str(units[0].id), str(units[1].id)]},
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert (body["page_start"], body["page_end"]) == (1, 4)
        rows = await read_units(db_session, document.id)
        assert [(unit.page_start, unit.page_end) for unit in rows] == [(1, 4), (5, 6)]
        assert [unit.ordinal for unit in rows] == [0, 1]

    async def test_the_merged_unit_is_read_again(
        self,
        api_client: AsyncClient,
        db_session: AsyncSession,
        operator_user: User,
        batch: Batch,
        enqueued: list[tuple[str, dict[str, TaskArgument]]],
    ) -> None:
        document = await make_document(db_session, batch)
        units = await make_units(db_session, document, [(1, 2), (3, 4)])
        await make_extraction(db_session, units[0], batch.workspace_id)
        await authenticate(api_client, operator_user)

        response = await api_client.post(
            "/api/v1/units/merge",
            json={"unit_ids": [str(units[0].id), str(units[1].id)]},
        )

        survivor = response.json()
        assert survivor["status"] == UnitStatus.TEXT_READY.value
        assert survivor["boundary_method"] == BoundaryMethod.MANUAL.value
        assert enqueued == [(TASK_CLASSIFY_UNIT, {"unit_id": survivor["id"]})]
        remaining = await db_session.scalars(select(Extraction))
        assert list(remaining) == []

    async def test_merging_is_recorded_in_the_audit_log(
        self,
        api_client: AsyncClient,
        db_session: AsyncSession,
        operator_user: User,
        batch: Batch,
        enqueued: list[tuple[str, dict[str, TaskArgument]]],
    ) -> None:
        document = await make_document(db_session, batch)
        units = await make_units(db_session, document, [(1, 2), (3, 4)])
        await authenticate(api_client, operator_user)

        await api_client.post(
            "/api/v1/units/merge",
            json={"unit_ids": [str(units[0].id), str(units[1].id)]},
        )

        entry = await db_session.scalar(
            select(AuditLog).where(AuditLog.action == AuditAction.UNIT_MERGED)
        )
        assert entry is not None
        assert entry.metadata_jsonb == {"count": 2}

    async def test_units_with_a_gap_between_them_are_refused(
        self,
        api_client: AsyncClient,
        db_session: AsyncSession,
        operator_user: User,
        batch: Batch,
        enqueued: list[tuple[str, dict[str, TaskArgument]]],
    ) -> None:
        document = await make_document(db_session, batch)
        units = await make_units(db_session, document, [(1, 2), (3, 4), (5, 6)])
        await authenticate(api_client, operator_user)

        response = await api_client.post(
            "/api/v1/units/merge",
            json={"unit_ids": [str(units[0].id), str(units[2].id)]},
        )

        assert response.status_code == 409
        assert response.json()["code"] == "conflict"
        assert enqueued == []

    async def test_units_from_different_files_are_refused(
        self,
        api_client: AsyncClient,
        db_session: AsyncSession,
        operator_user: User,
        batch: Batch,
        enqueued: list[tuple[str, dict[str, TaskArgument]]],
    ) -> None:
        first = await make_document(db_session, batch)
        second = await make_document(db_session, batch)
        (one,) = await make_units(db_session, first, [(1, 2)])
        (two,) = await make_units(db_session, second, [(1, 2)])
        await authenticate(api_client, operator_user)

        response = await api_client.post(
            "/api/v1/units/merge", json={"unit_ids": [str(one.id), str(two.id)]}
        )

        assert response.status_code == 400
        assert enqueued == []

    async def test_one_unit_is_not_a_merge(
        self,
        api_client: AsyncClient,
        db_session: AsyncSession,
        operator_user: User,
        batch: Batch,
        enqueued: list[tuple[str, dict[str, TaskArgument]]],
    ) -> None:
        document = await make_document(db_session, batch)
        (unit,) = await make_units(db_session, document, [(1, 2)])
        await authenticate(api_client, operator_user)

        response = await api_client.post("/api/v1/units/merge", json={"unit_ids": [str(unit.id)]})

        assert response.status_code == 422

    async def test_a_unit_that_does_not_exist(
        self,
        api_client: AsyncClient,
        db_session: AsyncSession,
        operator_user: User,
        batch: Batch,
        enqueued: list[tuple[str, dict[str, TaskArgument]]],
    ) -> None:
        document = await make_document(db_session, batch)
        (unit,) = await make_units(db_session, document, [(1, 2)])
        await authenticate(api_client, operator_user)

        response = await api_client.post(
            "/api/v1/units/merge",
            json={"unit_ids": [str(unit.id), str(uuid.uuid4())]},
        )

        assert response.status_code == 404
        assert enqueued == []

    async def test_a_viewer_cannot_merge(
        self,
        api_client: AsyncClient,
        db_session: AsyncSession,
        viewer_user: User,
        batch: Batch,
        enqueued: list[tuple[str, dict[str, TaskArgument]]],
    ) -> None:
        document = await make_document(db_session, batch)
        units = await make_units(db_session, document, [(1, 2), (3, 4)])
        await authenticate(api_client, viewer_user)

        response = await api_client.post(
            "/api/v1/units/merge",
            json={"unit_ids": [str(units[0].id), str(units[1].id)]},
        )

        assert response.status_code == 403
        assert enqueued == []


class TestAuthentication:
    async def test_signing_in_is_required(self, api_client: AsyncClient) -> None:
        response: Any = await api_client.get("/api/v1/units")
        assert response.status_code == 401
