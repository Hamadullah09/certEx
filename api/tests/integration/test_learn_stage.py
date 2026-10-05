"""Learning a form from a correction, end to end against Postgres.

The loop this closes is the whole point of templates: a reviewer corrects one copy of
an office's form, and the next copy of that form is read correctly without anybody
touching it. So the test that matters here does both halves - it corrects a row, learns
from it, and then reads a *second* certificate on the same form to see the correction
carried across.
"""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

import pytest
from sqlalchemy import select

from certex.db.models import CertificateUnit, Extraction, Template
from certex.db.session import session_scope
from certex.enums import CertificateType, ExtractionMethod
from certex.pipeline.filetypes import MIME_PDF
from certex.pipeline.stages.classify_stage import run_classify_unit_stage
from certex.pipeline.stages.extract_stage import run_extract_unit_stage
from certex.pipeline.stages.learn_stage import run_learn_template_stage
from certex.pipeline.stages.split_stage import run_split_stage
from certex.pipeline.stages.text_stage import run_text_stage
from certex.schemas.template import TemplateRules
from tests.fixtures.builders import build_text_pdf
from tests.integration.conftest import CommittedBatch, Recorder

pytestmark = pytest.mark.integration


async def extracted_unit(
    committed_batch: CommittedBatch, recorder: Recorder, path: Path
) -> uuid.UUID:
    """One certificate carried all the way to a row, ready to be corrected."""
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
    return unit_id


def correct(unit_id: uuid.UUID, field: str, value: str) -> None:
    """What the correction route leaves behind: the value, marked as a person's."""
    with session_scope() as session:
        row = session.scalar(select(Extraction).where(Extraction.unit_id == unit_id))
        assert row is not None
        row.fields_jsonb = {**row.fields_jsonb, field: value}
        row.field_methods_jsonb = {
            **row.field_methods_jsonb,
            field: ExtractionMethod.MANUAL.value,
        }
        row.field_confidences_jsonb = {**row.field_confidences_jsonb, field: 1.0}


def templates_of(workspace_id: uuid.UUID) -> list[Template]:
    with session_scope() as session:
        rows = list(session.scalars(select(Template).where(Template.workspace_id == workspace_id)))
        for row in rows:
            session.expunge(row)
        return rows


async def learn(unit_id: uuid.UUID):
    return await asyncio.to_thread(run_learn_template_stage, unit_id)


class TestLearning:
    async def test_a_correction_teaches_the_form(
        self, committed_batch: CommittedBatch, recorder: Recorder, tmp_path: Path
    ) -> None:
        unit_id = await extracted_unit(
            committed_batch, recorder, build_text_pdf(tmp_path / "birth.pdf")
        )
        correct(unit_id, "registrar_name", "Muhammad Aslam")

        result = await learn(unit_id)

        assert result.ran is True
        assert result.created is True
        assert result.learned == 1
        learned = templates_of(committed_batch.workspace_id)
        assert len(learned) == 1
        rules = TemplateRules.model_validate(learned[0].rules_jsonb)
        assert [rule.field for rule in rules.rules] == ["registrar_name"]
        assert learned[0].certificate_type is CertificateType.BIRTH
        assert learned[0].is_active is True

    async def test_what_was_learned_reads_the_next_copy_of_the_form(
        self, committed_batch: CommittedBatch, recorder: Recorder, tmp_path: Path
    ) -> None:
        """The loop closed: correct one, and the next one on the same form follows."""
        first = await extracted_unit(
            committed_batch, recorder, build_text_pdf(tmp_path / "first.pdf")
        )
        correct(first, "registrar_name", "Muhammad Aslam")
        await learn(first)

        second = await extracted_unit(
            committed_batch, recorder, build_text_pdf(tmp_path / "second.pdf")
        )
        with session_scope() as session:
            row = session.scalar(select(Extraction).where(Extraction.unit_id == second))
            assert row is not None
            assert row.template_id is not None
            assert row.field_methods_jsonb["registrar_name"] == ExtractionMethod.TEMPLATE.value

    async def test_a_second_correction_adds_a_rule_without_losing_the_first(
        self, committed_batch: CommittedBatch, recorder: Recorder, tmp_path: Path
    ) -> None:
        unit_id = await extracted_unit(
            committed_batch, recorder, build_text_pdf(tmp_path / "birth.pdf")
        )
        correct(unit_id, "registrar_name", "Muhammad Aslam")
        await learn(unit_id)
        correct(unit_id, "place_of_birth", "Services Hospital, Lahore")
        result = await learn(unit_id)

        assert result.created is False
        learned = templates_of(committed_batch.workspace_id)
        assert len(learned) == 1, "the same form must not produce a second template"
        rules = TemplateRules.model_validate(learned[0].rules_jsonb)
        assert {rule.field for rule in rules.rules} == {"registrar_name", "place_of_birth"}

    async def test_the_template_is_named_after_the_office_that_issued_the_form(
        self, committed_batch: CommittedBatch, recorder: Recorder, tmp_path: Path
    ) -> None:
        """A hash is useless to a clerk deciding whether their form has been learned."""
        unit_id = await extracted_unit(
            committed_batch, recorder, build_text_pdf(tmp_path / "birth.pdf")
        )
        correct(unit_id, "registrar_name", "Muhammad Aslam")
        await learn(unit_id)

        assert "Union Council 42" in templates_of(committed_batch.workspace_id)[0].name


class TestNotLearning:
    async def test_a_row_nobody_corrected_teaches_nothing(
        self, committed_batch: CommittedBatch, recorder: Recorder, tmp_path: Path
    ) -> None:
        unit_id = await extracted_unit(
            committed_batch, recorder, build_text_pdf(tmp_path / "birth.pdf")
        )

        result = await learn(unit_id)

        assert result.ran is False
        assert templates_of(committed_batch.workspace_id) == []

    async def test_a_value_that_is_not_printed_on_the_page_teaches_nothing(
        self, committed_batch: CommittedBatch, recorder: Recorder, tmp_path: Path
    ) -> None:
        """Typed off the paper because the scan never carried it. Nothing to learn, and
        no template written - an empty template would be credited on later rows."""
        unit_id = await extracted_unit(
            committed_batch, recorder, build_text_pdf(tmp_path / "birth.pdf")
        )
        correct(unit_id, "father_id_number", "99999-0000000-9")

        result = await learn(unit_id)

        assert result.ran is True
        assert result.learned == 0
        assert templates_of(committed_batch.workspace_id) == []

    async def test_a_unit_that_does_not_exist_is_not_an_error(self) -> None:
        """A correction to a row whose unit was deleted while the task was queued."""
        result = await learn(uuid.uuid4())
        assert result.ran is False
