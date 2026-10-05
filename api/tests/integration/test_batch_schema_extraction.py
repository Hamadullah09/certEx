"""A batch's own columns, carried all the way through the pipeline.

The acceptance rule in one test file: an office defines its columns once, and every
document uploaded into that batch is read for *those* columns - under the office's own
names, not the built-in ones - with nobody configuring anything again.

The second upload matters as much as the first. A system that read the first document
correctly and then forgot would be indistinguishable from a working one until somebody
checked the thousandth row.
"""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

import pytest
from sqlalchemy import select

from certex.db.models import Batch, CertificateUnit, Extraction
from certex.db.session import session_scope
from certex.enums import FieldRole, SchemaVersionStatus
from certex.fields import FieldKind
from certex.pipeline.filetypes import MIME_PDF
from certex.pipeline.stages.classify_stage import run_classify_unit_stage
from certex.pipeline.stages.extract_stage import run_extract_unit_stage
from certex.pipeline.stages.split_stage import run_split_stage
from certex.pipeline.stages.text_stage import run_text_stage
from certex.services import schema_service
from tests.fixtures.builders import build_text_pdf
from tests.integration.conftest import CommittedBatch, Recorder

pytestmark = [pytest.mark.integration]


# Deliberately not the built-in names. "reg_no" is what this office calls the column;
# the label is what is printed beside the value on their form, which is what the rules
# engine actually matches on.
OFFICE_COLUMNS = [
    schema_service.SchemaDraftField(
        name="reg_no",
        label="Certificate No.",
        kind=FieldKind.REFERENCE,
        role=FieldRole.IDENTIFIER,
        required=True,
    ),
    schema_service.SchemaDraftField(name="baby_name", label="Name of Child", kind=FieldKind.NAME),
    schema_service.SchemaDraftField(name="born_on", label="Date of Birth", kind=FieldKind.DATE),
    schema_service.SchemaDraftField(name="baba_name", label="Father's Name", kind=FieldKind.NAME),
]


def pin_columns(batch_id: uuid.UUID, workspace_id: uuid.UUID) -> uuid.UUID:
    """Give a batch its own schema, the way creating it through the API would.

    Written directly rather than over HTTP because this file is about what the pipeline
    does with the pinned columns; that the API pins them is covered in
    ``test_batch_columns.py``.
    """
    from certex.db.models import (
        CertificateSchema,
        CertificateTypeRecord,
        SchemaField,
        SchemaVersion,
    )

    with session_scope() as session:
        # Seeded lazily in production, by the first read of the type list.
        schema_service.ensure_builtin_types_sync(session, workspace_id=workspace_id)
        certificate_type = session.scalar(
            select(CertificateTypeRecord).where(
                CertificateTypeRecord.workspace_id == workspace_id,
                CertificateTypeRecord.key == "BIRTH",
            )
        )
        assert certificate_type is not None, "the builtin types should be seeded"

        schema = CertificateSchema(
            workspace_id=workspace_id,
            certificate_type_id=certificate_type.id,
            name=f"Office columns {uuid.uuid4().hex[:8]}",
        )
        session.add(schema)
        session.flush()

        version = SchemaVersion(
            schema_id=schema.id,
            workspace_id=workspace_id,
            version=1,
            status=SchemaVersionStatus.PUBLISHED,
        )
        session.add(version)
        session.flush()

        for position, field in enumerate(OFFICE_COLUMNS):
            session.add(
                SchemaField(
                    schema_version_id=version.id,
                    position=position,
                    name=field.name,
                    label=field.label,
                    kind=field.kind.value,
                    role=field.role,
                    required=field.required,
                )
            )

        batch = session.get(Batch, batch_id)
        assert batch is not None
        batch.certificate_type_id = certificate_type.id
        batch.schema_version_id = version.id
        session.flush()
        return version.id


async def read_one(committed_batch: CommittedBatch, recorder: Recorder, path: Path) -> Extraction:
    """One document carried from upload to a finished row."""
    document_id = committed_batch.add_document(path, mime_type=MIME_PDF)
    await asyncio.to_thread(run_text_stage, document_id, dispatch=recorder)
    await asyncio.to_thread(run_split_stage, document_id, dispatch=recorder)
    with session_scope() as session:
        unit = session.scalar(
            select(CertificateUnit).where(CertificateUnit.document_id == document_id)
        )
        assert unit is not None
        unit_id = unit.id
    await asyncio.to_thread(run_classify_unit_stage, unit_id, dispatch=recorder)
    await asyncio.to_thread(run_extract_unit_stage, unit_id, dispatch=recorder)
    recorder.calls.clear()

    with session_scope() as session:
        row = session.scalar(select(Extraction).where(Extraction.unit_id == unit_id))
        assert row is not None
        session.expunge(row)
        return row


class TestTheBatchColumnsGovernExtraction:
    async def test_values_are_stored_under_the_office_column_names(
        self, committed_batch: CommittedBatch, recorder: Recorder, tmp_path: Path
    ) -> None:
        pin_columns(committed_batch.batch_id, committed_batch.workspace_id)

        row = await read_one(committed_batch, recorder, build_text_pdf(tmp_path / "first.pdf"))

        assert row.fields_jsonb.get("reg_no") == "BC-2019-004471"
        assert row.fields_jsonb.get("baby_name") == "Ayesha Noor Malik"
        assert row.fields_jsonb.get("born_on") == "1987-03-14"
        assert row.fields_jsonb.get("baba_name") == "Tariq Mahmood Malik"

    async def test_only_the_configured_columns_are_stored(
        self, committed_batch: CommittedBatch, recorder: Recorder, tmp_path: Path
    ) -> None:
        """The form prints seventeen values; this batch asked for four.

        Everything else on the page is still read - it has to be, to find the four -
        but it does not become a column of this batch's records.
        """
        pin_columns(committed_batch.batch_id, committed_batch.workspace_id)

        row = await read_one(committed_batch, recorder, build_text_pdf(tmp_path / "first.pdf"))

        assert set(row.fields_jsonb) <= {"reg_no", "baby_name", "born_on", "baba_name"}
        assert "child_full_name" not in row.fields_jsonb, "a built-in name leaked in"
        assert "mother_full_name" not in row.fields_jsonb

    async def test_the_second_upload_uses_the_same_columns_without_being_told(
        self, committed_batch: CommittedBatch, recorder: Recorder, tmp_path: Path
    ) -> None:
        """The rule the whole feature rests on."""
        pin_columns(committed_batch.batch_id, committed_batch.workspace_id)

        first = await read_one(committed_batch, recorder, build_text_pdf(tmp_path / "a.pdf"))
        second = await read_one(committed_batch, recorder, build_text_pdf(tmp_path / "b.pdf"))

        assert set(first.fields_jsonb) == set(second.fields_jsonb)
        assert second.fields_jsonb.get("baby_name") == "Ayesha Noor Malik"

    async def test_a_batch_that_pinned_nothing_still_reads_the_builtin_columns(
        self, committed_batch: CommittedBatch, recorder: Recorder, tmp_path: Path
    ) -> None:
        """Every batch created before any of this existed must keep working."""
        row = await read_one(committed_batch, recorder, build_text_pdf(tmp_path / "legacy.pdf"))

        assert row.fields_jsonb.get("child_full_name") == "Ayesha Noor Malik"
        assert "baby_name" not in row.fields_jsonb


class TestEditingTheSchemaLater:
    async def test_a_new_schema_version_does_not_change_what_this_batch_reads(
        self, committed_batch: CommittedBatch, recorder: Recorder, tmp_path: Path
    ) -> None:
        """Pinning is the point.

        An office adding a column next year must not retroactively change how a batch
        read last year - its records are already in the register under the old columns,
        and an export that disagreed with them would be worse than one that is old.
        """
        version_id = pin_columns(committed_batch.batch_id, committed_batch.workspace_id)

        from certex.db.models import SchemaField, SchemaVersion

        with session_scope() as session:
            original = session.get(SchemaVersion, version_id)
            assert original is not None
            newer = SchemaVersion(
                schema_id=original.schema_id,
                workspace_id=committed_batch.workspace_id,
                version=2,
                status=SchemaVersionStatus.PUBLISHED,
            )
            session.add(newer)
            session.flush()
            session.add(
                SchemaField(
                    schema_version_id=newer.id,
                    position=0,
                    name="blood_group",
                    label="Blood Group",
                    kind=FieldKind.TEXT.value,
                    role=FieldRole.NONE,
                )
            )

        row = await read_one(committed_batch, recorder, build_text_pdf(tmp_path / "after.pdf"))

        assert "blood_group" not in row.fields_jsonb
        assert row.fields_jsonb.get("baby_name") == "Ayesha Noor Malik"
