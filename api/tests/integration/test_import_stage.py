"""Loading a CSV into the register, against Postgres and object storage.

The behaviour worth proving is what an office is left with afterwards. A file with one
bad row must load the rest of it. A file with the wrong columns must load none of it.
And a certificate number the register already holds must not quietly replace what is
there, whichever policy the operator chose.
"""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Protocol

import pytest
from sqlalchemy import func, select

from certex.db.models import (
    Certificate,
    CertificateImport,
    CertificateTypeRecord,
    ImportRowError,
)
from certex.db.session import session_scope
from certex.enums import (
    CertificateSource,
    CertificateType,
    DuplicateStatus,
    ImportDuplicatePolicy,
    ImportStatus,
)
from certex.pipeline.stages.import_stage import ImportStageResult, run_import_stage
from certex.services import schema_service
from certex.storage.s3 import ObjectStorage, StorageKeys
from tests.integration.conftest import CommittedBatch

pytestmark = pytest.mark.integration

HEADER = "certificate_number,child_full_name,date_of_birth,father_full_name,registration_date"
GOOD_ROWS = (
    "BC/LHR/2019/1001,Ayesha Noor Malik,2019-04-03,Tariq Mahmood Malik,2019-05-01",
    "BC/LHR/2019/1002,Bilal Hussain,2018-11-20,Imran Hussain,2018-12-02",
    "BC/LHR/2019/1003,Sana Fatima,2017-06-14,Rashid Fatima,2017-07-01",
)


class Upload(Protocol):
    """The fixture below, as a type: puts a CSV in storage and records the import."""

    def __call__(
        self,
        text: str,
        *,
        policy: ImportDuplicatePolicy = ...,
        delimiter: str = ...,
        encoding: str = ...,
    ) -> Loaded: ...


@dataclass(slots=True)
class Loaded:
    """An import row committed and its file in storage, ready for the worker."""

    import_id: uuid.UUID
    workspace_id: uuid.UUID
    storage_key: str


@pytest.fixture
def registry(committed_batch: CommittedBatch) -> uuid.UUID:
    """The workspace's Birth type, with the schema shipped with the product."""
    with session_scope() as session:
        schema_service.ensure_builtin_types_sync(session, workspace_id=committed_batch.workspace_id)
    with session_scope() as session:
        type_id = session.scalar(
            select(CertificateTypeRecord.id).where(
                CertificateTypeRecord.workspace_id == committed_batch.workspace_id,
                CertificateTypeRecord.classifier_key == CertificateType.BIRTH,
            )
        )
        assert type_id is not None
        return type_id


@pytest.fixture
def uploaded(
    committed_batch: CommittedBatch, registry: uuid.UUID, object_storage: ObjectStorage
) -> Iterator[Upload]:
    """A factory that puts a CSV in storage and records it as a pending import."""
    keys: list[str] = []

    def upload(
        text: str,
        *,
        policy: ImportDuplicatePolicy = ImportDuplicatePolicy.SKIP,
        delimiter: str = ",",
        encoding: str = "utf-8",
    ) -> Loaded:
        import_id = uuid.uuid4()
        key = StorageKeys.import_file(committed_batch.workspace_id, import_id)
        payload = text.encode(encoding)
        object_storage.upload_bytes(key, payload, content_type="text/csv")
        keys.append(key)

        with session_scope() as session:
            version = schema_service.default_version_for_type_sync(
                session,
                workspace_id=committed_batch.workspace_id,
                certificate_type_id=registry,
            )
            session.add(
                CertificateImport(
                    id=import_id,
                    workspace_id=committed_batch.workspace_id,
                    certificate_type_id=registry,
                    schema_version_id=version.id if version else None,
                    original_filename="register.csv",
                    storage_key=key,
                    byte_size=len(payload),
                    sha256=hashlib.sha256(payload).hexdigest(),
                    delimiter=delimiter,
                    duplicate_policy=policy,
                    status=ImportStatus.PENDING,
                    created_by=committed_batch.user_id,
                )
            )
        return Loaded(
            import_id=import_id,
            workspace_id=committed_batch.workspace_id,
            storage_key=key,
        )

    yield upload
    for key in keys:
        object_storage.delete(key)


async def run(import_id: uuid.UUID) -> ImportStageResult:
    """The stage is synchronous by design; a test drives it off the event loop."""
    return await asyncio.to_thread(run_import_stage, import_id)


def read_import(import_id: uuid.UUID) -> CertificateImport:
    with session_scope() as session:
        record = session.get(CertificateImport, import_id)
        assert record is not None
        session.expunge(record)
        return record


def entries() -> list[Certificate]:
    with session_scope() as session:
        rows = list(session.scalars(select(Certificate).order_by(Certificate.certificate_number)))
        for row in rows:
            session.expunge(row)
        return rows


def errors(import_id: uuid.UUID) -> list[ImportRowError]:
    with session_scope() as session:
        rows = list(
            session.scalars(
                select(ImportRowError)
                .where(ImportRowError.import_id == import_id)
                .order_by(ImportRowError.row_number)
            )
        )
        for row in rows:
            session.expunge(row)
        return rows


def csv_of(*rows: str, header: str = HEADER) -> str:
    return "\r\n".join((header, *rows)) + "\r\n"


class TestALoadThatWorks:
    async def test_every_row_becomes_an_entry(self, uploaded: Upload) -> None:
        loaded = uploaded(csv_of(*GOOD_ROWS))

        await run(loaded.import_id)

        record = read_import(loaded.import_id)
        assert record.status is ImportStatus.COMPLETED
        assert (record.total_rows, record.created_rows) == (3, 3)
        assert record.failed_rows == 0
        assert len(entries()) == 3

    async def test_the_entries_say_where_they_came_from(self, uploaded: Upload) -> None:
        loaded = uploaded(csv_of(GOOD_ROWS[0]))
        await run(loaded.import_id)

        entry = entries()[0]
        assert entry.source is CertificateSource.IMPORT
        assert entry.certificate_number == "BC/LHR/2019/1001"
        assert entry.primary_name == "Ayesha Noor Malik"
        assert entry.event_date is not None
        assert entry.schema_version_id is not None

    async def test_a_record_typed_by_a_person_needs_no_review(self, uploaded: Upload) -> None:
        """It was not read by a machine, so there is no reading to check."""
        loaded = uploaded(csv_of(GOOD_ROWS[0]))
        await run(loaded.import_id)
        assert entries()[0].needs_review is False

    async def test_the_encoding_is_recorded(self, uploaded: Upload) -> None:
        loaded = uploaded("﻿" + csv_of(GOOD_ROWS[0]))
        await run(loaded.import_id)
        assert read_import(loaded.import_id).encoding == "utf-8-sig"

    async def test_a_semicolon_file_loads(self, uploaded: Upload) -> None:
        text = csv_of(*GOOD_ROWS).replace(",", ";")
        loaded = uploaded(text, delimiter=";")
        await run(loaded.import_id)
        assert read_import(loaded.import_id).created_rows == 3

    async def test_urdu_values_survive_the_round_trip(self, uploaded: Upload) -> None:
        name = "عائشہ نور"
        loaded = uploaded(csv_of(f"BC/LHR/2019/2001,{name},2019-04-03,,"))
        await run(loaded.import_id)
        assert entries()[0].primary_name == name


class TestABadRow:
    async def test_the_other_rows_still_load(self, uploaded: Upload) -> None:
        """Row 3 having no number is a fact about row 3."""
        loaded = uploaded(
            csv_of(
                GOOD_ROWS[0],
                ",Nameless Child,2019-04-03,,",
                GOOD_ROWS[1],
            )
        )
        await run(loaded.import_id)

        record = read_import(loaded.import_id)
        assert record.status is ImportStatus.PARTIAL
        assert record.created_rows == 2
        assert record.failed_rows == 1
        assert len(entries()) == 2

    async def test_the_error_names_the_line_and_the_column(self, uploaded: Upload) -> None:
        loaded = uploaded(csv_of(GOOD_ROWS[0], ",Nameless Child,2019-04-03,,"))
        await run(loaded.import_id)

        (problem,) = errors(loaded.import_id)
        assert problem.row_number == 3, "the header is line 1"
        assert problem.code == "missing_identifier"
        assert problem.field_name == "certificate_number"
        assert "certificate_number" in problem.message

    async def test_a_row_with_too_many_values_is_refused_rather_than_trimmed(
        self, uploaded: Upload
    ) -> None:
        """Silently dropping the last value would file a record missing a field."""
        loaded = uploaded(csv_of(GOOD_ROWS[0] + ",leftover"))
        await run(loaded.import_id)

        (problem,) = errors(loaded.import_id)
        assert problem.code == "extra_columns"
        assert entries() == []

    async def test_an_unreadable_date_does_not_stop_the_row(self, uploaded: Upload) -> None:
        """The value is kept as printed so a clerk can see and correct it."""
        loaded = uploaded(csv_of("BC/LHR/2019/3001,Ayesha Noor,not a date,,"))
        await run(loaded.import_id)

        assert read_import(loaded.import_id).created_rows == 1
        entry = entries()[0]
        assert entry.event_date is None
        assert entry.values_jsonb["date_of_birth"] == "not a date"


class TestABadFile:
    async def test_an_unknown_column_loads_nothing(self, uploaded: Upload) -> None:
        loaded = uploaded(
            csv_of(
                "BC/LHR/2019/1001,Ayesha,2019-04-03,Tariq,2019-05-01,green",
                header=HEADER + ",favourite_colour",
            )
        )
        await run(loaded.import_id)

        record = read_import(loaded.import_id)
        assert record.status is ImportStatus.FAILED
        assert record.error_code == "unknown_columns"
        assert "favourite_colour" in (record.error_message or "")
        assert entries() == [], "an office cannot un-import a register"

    async def test_a_missing_identifier_column_loads_nothing(self, uploaded: Upload) -> None:
        loaded = uploaded(csv_of("Ayesha Noor,2019-04-03", header="child_full_name,date_of_birth"))
        await run(loaded.import_id)

        record = read_import(loaded.import_id)
        assert record.status is ImportStatus.FAILED
        assert record.error_code == "missing_columns"
        assert "certificate_number" in (record.error_message or "")

    async def test_an_empty_file_says_so(self, uploaded: Upload) -> None:
        loaded = uploaded("")
        await run(loaded.import_id)
        assert read_import(loaded.import_id).error_code == "empty_file"

    async def test_the_message_tells_the_operator_what_to_do(self, uploaded: Upload) -> None:
        loaded = uploaded(csv_of("Ayesha Noor,2019-04-03", header="child_full_name,date_of_birth"))
        await run(loaded.import_id)
        assert "template" in (read_import(loaded.import_id).error_message or "")


class TestDuplicates:
    async def test_a_repeated_number_inside_one_file_is_skipped(self, uploaded: Upload) -> None:
        loaded = uploaded(csv_of(GOOD_ROWS[0], GOOD_ROWS[0]))
        await run(loaded.import_id)

        record = read_import(loaded.import_id)
        assert record.created_rows == 1
        assert record.skipped_rows == 1
        assert len(entries()) == 1

        (problem,) = errors(loaded.import_id)
        assert problem.code == "duplicate_number"
        assert problem.certificate_number == "BC/LHR/2019/1001"

    async def test_the_first_row_is_the_one_kept(self, uploaded: Upload) -> None:
        loaded = uploaded(
            csv_of(
                GOOD_ROWS[0],
                "BC/LHR/2019/1001,Someone Else Entirely,2019-04-03,,",
            )
        )
        await run(loaded.import_id)

        (entry,) = entries()
        assert entry.primary_name == "Ayesha Noor Malik", "never overwritten"

    async def test_the_policy_can_keep_both_for_a_reviewer(self, uploaded: Upload) -> None:
        loaded = uploaded(
            csv_of(GOOD_ROWS[0], "BC/LHR/2019/1001,Ayesha Noor,2019-04-03,,"),
            policy=ImportDuplicatePolicy.RECORD_AS_DUPLICATE,
        )
        await run(loaded.import_id)

        rows = entries()
        assert len(rows) == 2
        assert rows[1].duplicate_status is DuplicateStatus.SUSPECTED
        assert rows[1].duplicate_of_id == rows[0].id
        assert read_import(loaded.import_id).created_rows == 2

    async def test_a_number_the_register_already_holds_is_skipped(self, uploaded: Upload) -> None:
        first = uploaded(csv_of(GOOD_ROWS[0]))
        await run(first.import_id)

        second = uploaded(csv_of("BC/LHR/2019/1001,Different Person,2019-04-03,,"))
        await run(second.import_id)

        assert read_import(second.import_id).skipped_rows == 1
        assert len(entries()) == 1
        assert entries()[0].primary_name == "Ayesha Noor Malik"


class TestRunningTwice:
    async def test_a_second_delivery_does_nothing(self, uploaded: Upload) -> None:
        """Celery can deliver a task twice; the register must not double."""
        loaded = uploaded(csv_of(*GOOD_ROWS))
        await run(loaded.import_id)
        await run(loaded.import_id)

        assert len(entries()) == 3
        assert read_import(loaded.import_id).created_rows == 3

    async def test_a_cancelled_import_is_not_run(self, uploaded: Upload) -> None:
        loaded = uploaded(csv_of(*GOOD_ROWS))
        with session_scope() as session:
            record = session.get(CertificateImport, loaded.import_id)
            assert record is not None
            record.status = ImportStatus.CANCELLED

        await run(loaded.import_id)
        assert entries() == []


class TestALargerFile:
    async def test_a_thousand_rows_load_in_batches(self, uploaded: Upload) -> None:
        """Past the commit batch size, so the batching itself is exercised."""
        rows = [
            f"BC/LHR/2020/{index:05d},Child Number {index},2020-01-01,Father {index},2020-02-01"
            for index in range(1000)
        ]
        loaded = uploaded(csv_of(*rows))

        await run(loaded.import_id)

        record = read_import(loaded.import_id)
        assert record.status is ImportStatus.COMPLETED
        assert record.created_rows == 1000
        with session_scope() as session:
            count = session.scalar(select(func.count()).select_from(Certificate))
        assert count == 1000
