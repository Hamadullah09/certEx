"""Finishing a document publishes its rows into the register.

Up to this point the pipeline has produced a reading of a file. This is where it
becomes a record the office holds: found by certificate number, searchable by the
names on it, and linked to the pages it was read from. The tests below run the real
stages against Postgres, because the thing worth proving is what is in the database
afterwards.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import func, select

from certex.db.models import (
    Certificate,
    CertificateDate,
    CertificateDocument,
    CertificateName,
    CertificateTypeRecord,
    CertificateUnit,
    Document,
    Extraction,
)
from certex.db.session import session_scope
from certex.enums import (
    CertificateSource,
    CertificateType,
    DuplicateStatus,
    FieldRole,
)
from certex.pipeline.filetypes import MIME_PDF
from certex.pipeline.stages.classify_stage import run_classify_unit_stage
from certex.pipeline.stages.extract_stage import run_extract_unit_stage
from certex.pipeline.stages.finalize_stage import run_finalize_document_stage
from certex.pipeline.stages.split_stage import run_split_stage
from certex.pipeline.stages.text_stage import run_text_stage
from certex.pipeline.stages.validate_stage import run_validate_unit_stage
from certex.services import schema_service
from tests.fixtures.builders import build_text_pdf
from tests.integration.conftest import CommittedBatch, Recorder

pytestmark = pytest.mark.integration


@pytest.fixture
def registry(committed_batch: CommittedBatch) -> Iterator[dict[CertificateType, uuid.UUID]]:
    """The workspace's certificate types, seeded as a real workspace would have them."""
    with session_scope() as session:
        schema_service.ensure_builtin_types_sync(session, workspace_id=committed_batch.workspace_id)
    with session_scope() as session:
        rows = list(
            session.scalars(
                select(CertificateTypeRecord).where(
                    CertificateTypeRecord.workspace_id == committed_batch.workspace_id
                )
            )
        )
        mapping = {row.classifier_key: row.id for row in rows if row.classifier_key is not None}
    yield mapping


def units_of(document_id: uuid.UUID) -> list[CertificateUnit]:
    with session_scope() as session:
        units = list(
            session.scalars(
                select(CertificateUnit)
                .where(CertificateUnit.document_id == document_id)
                .order_by(CertificateUnit.ordinal)
            )
        )
        for unit in units:
            session.expunge(unit)
        return units


def entries() -> list[Certificate]:
    with session_scope() as session:
        rows = list(session.scalars(select(Certificate).order_by(Certificate.created_at)))
        for row in rows:
            session.expunge(row)
        return rows


def only_entry() -> Certificate:
    rows = entries()
    assert len(rows) == 1, f"expected one register entry, found {len(rows)}"
    return rows[0]


async def read_document(
    committed_batch: CommittedBatch, recorder: Recorder, path: Path
) -> uuid.UUID:
    """One document taken through the whole pipeline except finalising."""
    document_id = committed_batch.add_document(path, mime_type=MIME_PDF)
    await asyncio.to_thread(run_text_stage, document_id, dispatch=recorder)
    await asyncio.to_thread(run_split_stage, document_id, dispatch=recorder)
    for unit in units_of(document_id):
        await asyncio.to_thread(run_classify_unit_stage, unit.id, dispatch=recorder)
        await asyncio.to_thread(run_extract_unit_stage, unit.id, dispatch=recorder)
        await asyncio.to_thread(run_validate_unit_stage, unit.id, dispatch=recorder)
    recorder.calls.clear()
    return document_id


async def finalize(document_id: uuid.UUID) -> int:
    result = await asyncio.to_thread(run_finalize_document_stage, document_id)
    return result.certificates_written


class TestPublishing:
    async def test_a_finished_document_becomes_a_register_entry(
        self,
        committed_batch: CommittedBatch,
        recorder: Recorder,
        registry: dict[CertificateType, uuid.UUID],
        tmp_path: Path,
    ) -> None:
        document_id = await read_document(
            committed_batch, recorder, build_text_pdf(tmp_path / "birth.pdf")
        )

        assert await finalize(document_id) == 1

        entry = only_entry()
        assert entry.certificate_number
        assert entry.certificate_number_key
        assert entry.certificate_type_id == registry[CertificateType.BIRTH]
        assert entry.source is CertificateSource.EXTRACTION
        assert entry.workspace_id == committed_batch.workspace_id

    async def test_the_entry_records_which_schema_version_read_it(
        self,
        committed_batch: CommittedBatch,
        recorder: Recorder,
        registry: dict[CertificateType, uuid.UUID],
        tmp_path: Path,
    ) -> None:
        """Without it, nobody could say later what its field names meant."""
        document_id = await read_document(
            committed_batch, recorder, build_text_pdf(tmp_path / "birth.pdf")
        )
        await finalize(document_id)
        assert only_entry().schema_version_id is not None

    async def test_the_entry_points_back_at_the_extraction(
        self,
        committed_batch: CommittedBatch,
        recorder: Recorder,
        registry: dict[CertificateType, uuid.UUID],
        tmp_path: Path,
    ) -> None:
        document_id = await read_document(
            committed_batch, recorder, build_text_pdf(tmp_path / "birth.pdf")
        )
        await finalize(document_id)

        with session_scope() as session:
            extraction_id = session.scalar(select(Extraction.id))
        assert only_entry().source_extraction_id == extraction_id

    async def test_the_names_on_it_are_all_searchable(
        self,
        committed_batch: CommittedBatch,
        recorder: Recorder,
        registry: dict[CertificateType, uuid.UUID],
        tmp_path: Path,
    ) -> None:
        document_id = await read_document(
            committed_batch, recorder, build_text_pdf(tmp_path / "birth.pdf")
        )
        await finalize(document_id)

        with session_scope() as session:
            roles = set(session.scalars(select(CertificateName.role)))
        assert FieldRole.SUBJECT_NAME in roles
        assert FieldRole.FATHER_NAME in roles

    async def test_the_dates_are_stored_as_dates(
        self,
        committed_batch: CommittedBatch,
        recorder: Recorder,
        registry: dict[CertificateType, uuid.UUID],
        tmp_path: Path,
    ) -> None:
        document_id = await read_document(
            committed_batch, recorder, build_text_pdf(tmp_path / "birth.pdf")
        )
        await finalize(document_id)

        with session_scope() as session:
            rows = list(session.scalars(select(CertificateDate)))
        assert rows, "a birth certificate has dates"
        assert all(row.value.year > 1900 for row in rows)
        assert only_entry().event_date is not None

    async def test_provenance_travels_with_the_entry(
        self,
        committed_batch: CommittedBatch,
        recorder: Recorder,
        registry: dict[CertificateType, uuid.UUID],
        tmp_path: Path,
    ) -> None:
        """The entry outlives its batch, so "why does it say that" must stay answerable."""
        document_id = await read_document(
            committed_batch, recorder, build_text_pdf(tmp_path / "birth.pdf")
        )
        await finalize(document_id)

        entry = only_entry()
        assert entry.provenance_jsonb
        assert all("method" in detail for detail in entry.provenance_jsonb.values())
        assert entry.confidences_jsonb

    async def test_a_read_entry_waits_for_review(
        self,
        committed_batch: CommittedBatch,
        recorder: Recorder,
        registry: dict[CertificateType, uuid.UUID],
        tmp_path: Path,
    ) -> None:
        document_id = await read_document(
            committed_batch, recorder, build_text_pdf(tmp_path / "birth.pdf")
        )
        await finalize(document_id)
        assert only_entry().needs_review is True


class TestDocumentLink:
    async def test_the_entry_is_linked_to_the_pages_it_was_read_from(
        self,
        committed_batch: CommittedBatch,
        recorder: Recorder,
        registry: dict[CertificateType, uuid.UUID],
        tmp_path: Path,
    ) -> None:
        document_id = await read_document(
            committed_batch, recorder, build_text_pdf(tmp_path / "birth.pdf")
        )
        await finalize(document_id)

        with session_scope() as session:
            links = list(session.scalars(select(CertificateDocument)))
        assert len(links) == 1
        assert links[0].document_id == document_id
        assert links[0].unit_id is not None
        assert links[0].page_start is not None

    async def test_the_link_is_by_identity_not_by_filename(
        self,
        committed_batch: CommittedBatch,
        recorder: Recorder,
        registry: dict[CertificateType, uuid.UUID],
        tmp_path: Path,
    ) -> None:
        """Renaming the upload must not detach the scan from the record."""
        document_id = await read_document(
            committed_batch, recorder, build_text_pdf(tmp_path / "birth.pdf")
        )
        await finalize(document_id)

        with session_scope() as session:
            document = session.get(Document, document_id)
            assert document is not None
            document.original_filename = "something-else-entirely.pdf"

        with session_scope() as session:
            link = session.scalar(select(CertificateDocument))
        assert link is not None
        assert link.document_id == document_id


class TestRunningTwice:
    async def test_finalising_again_adds_nothing(
        self,
        committed_batch: CommittedBatch,
        recorder: Recorder,
        registry: dict[CertificateType, uuid.UUID],
        tmp_path: Path,
    ) -> None:
        """A retried Celery task must not double the register."""
        document_id = await read_document(
            committed_batch, recorder, build_text_pdf(tmp_path / "birth.pdf")
        )
        assert await finalize(document_id) == 1

        # The document is already COMPLETED, so the stage declines to run at all -
        # and even if it did, the entry is already there.
        await finalize(document_id)
        assert len(entries()) == 1

        with session_scope() as session:
            links = session.scalar(select(func.count()).select_from(CertificateDocument))
        assert links == 1


class TestSecondUpload:
    async def test_the_same_certificate_twice_is_flagged_not_overwritten(
        self,
        committed_batch: CommittedBatch,
        recorder: Recorder,
        registry: dict[CertificateType, uuid.UUID],
        tmp_path: Path,
    ) -> None:
        """A batch of scans cannot stop on a repeated number, so it is recorded and asked about."""
        first = await read_document(committed_batch, recorder, build_text_pdf(tmp_path / "one.pdf"))
        await finalize(first)

        # The same certificate arriving from a second archive. Two builds of the
        # same certificate are not byte-identical, so content-hash deduplication
        # does not intercept it - which is the case worth testing, because that is
        # how a re-scan of one certificate actually arrives.
        second = await read_document(
            committed_batch, recorder, build_text_pdf(tmp_path / "two.pdf")
        )
        await finalize(second)

        rows = entries()
        assert len(rows) == 2, "neither entry is thrown away"
        assert rows[1].duplicate_status is DuplicateStatus.SUSPECTED
        assert rows[1].duplicate_of_id == rows[0].id
        assert rows[1].needs_review is True

    async def test_the_first_entry_is_untouched(
        self,
        committed_batch: CommittedBatch,
        recorder: Recorder,
        registry: dict[CertificateType, uuid.UUID],
        tmp_path: Path,
    ) -> None:
        first = await read_document(committed_batch, recorder, build_text_pdf(tmp_path / "one.pdf"))
        await finalize(first)
        before = entries()[0]

        second = await read_document(
            committed_batch, recorder, build_text_pdf(tmp_path / "two.pdf")
        )
        await finalize(second)

        after = entries()[0]
        assert after.id == before.id
        assert after.record_version == 1
        assert after.duplicate_status is DuplicateStatus.NONE
        assert after.values_jsonb == before.values_jsonb


class TestWithoutARegistry:
    async def test_a_workspace_with_no_types_still_finishes_its_batch(
        self, committed_batch: CommittedBatch, recorder: Recorder, tmp_path: Path
    ) -> None:
        """No ``registry`` fixture here: nothing has been seeded.

        Publishing is skipped and the document still completes. The extraction is
        untouched and still exports, which is how the pipeline behaved before the
        register existed.
        """
        document_id = await read_document(
            committed_batch, recorder, build_text_pdf(tmp_path / "birth.pdf")
        )

        assert await finalize(document_id) == 0
        assert entries() == []

        with session_scope() as session:
            rows = session.scalar(select(func.count()).select_from(Extraction))
        assert rows == 1, "the reading is kept even when it cannot be filed"
