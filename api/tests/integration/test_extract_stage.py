"""The extraction stage, end to end against Postgres.

The reading itself is measured by the golden tests; what is asserted here is everything
a reviewer and the export depend on afterwards: the row exists, each value says how sure
it is and where it came from, an office's template is found and credited, and running
the task twice leaves one row rather than two.
"""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

import pytest
from sqlalchemy import select

from certex.db.models import CertificateUnit, Extraction, Template
from certex.db.session import session_scope
from certex.enums import (
    BoundaryMethod,
    CertificateType,
    ClassificationMethod,
    ExtractionMethod,
    ReviewStatus,
    UnitStatus,
)
from certex.fields import FIELD_SCHEMA_VERSION
from certex.pipeline.dispatch import TASK_VALIDATE_UNIT
from certex.pipeline.extract.rules import UnitPage
from certex.pipeline.extract.templates import fingerprint_pages
from certex.pipeline.filetypes import MIME_PDF
from certex.pipeline.stages.classify_stage import run_classify_unit_stage
from certex.pipeline.stages.extract_stage import ExtractStageResult, run_extract_unit_stage
from certex.pipeline.stages.split_stage import run_split_stage
from certex.pipeline.stages.text_stage import run_text_stage
from certex.pipeline.text.pdf_native import iter_pdf_layouts
from certex.schemas.template import AnchorDirection, TemplateRule, TemplateRules
from tests.fixtures.builders import SAMPLES_BY_KEY, build_text_pdf
from tests.integration.conftest import CommittedBatch, Recorder

pytestmark = pytest.mark.integration


async def run_extract(unit_id: uuid.UUID, recorder: Recorder) -> ExtractStageResult:
    return await asyncio.to_thread(run_extract_unit_stage, unit_id, dispatch=recorder)


def read_row(unit_id: uuid.UUID) -> Extraction | None:
    with session_scope() as session:
        row = session.scalar(select(Extraction).where(Extraction.unit_id == unit_id))
        if row is not None:
            session.expunge(row)
        return row


def read_unit(unit_id: uuid.UUID) -> CertificateUnit:
    with session_scope() as session:
        unit = session.get(CertificateUnit, unit_id)
        assert unit is not None
        session.expunge(unit)
        return unit


async def classified_unit(
    committed_batch: CommittedBatch, recorder: Recorder, path: Path
) -> uuid.UUID:
    """A certificate carried through text, split and classify, ready to be read."""
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
    recorder.calls.clear()
    return unit_id


@pytest.fixture
async def birth_unit(
    committed_batch: CommittedBatch, recorder: Recorder, tmp_path: Path
) -> uuid.UUID:
    return await classified_unit(committed_batch, recorder, build_text_pdf(tmp_path / "birth.pdf"))


class TestTheRow:
    async def test_the_certificate_becomes_a_row(
        self, birth_unit: uuid.UUID, recorder: Recorder
    ) -> None:
        result = await run_extract(birth_unit, recorder)

        assert result.ran is True
        assert result.field_count > 10
        row = read_row(birth_unit)
        assert row is not None
        assert row.certificate_type is CertificateType.BIRTH
        assert row.schema_version == FIELD_SCHEMA_VERSION

    async def test_the_values_are_the_ones_printed_on_the_certificate(
        self, birth_unit: uuid.UUID, recorder: Recorder
    ) -> None:
        await run_extract(birth_unit, recorder)

        row = read_row(birth_unit)
        assert row is not None
        for name, want in SAMPLES_BY_KEY["birth_lahore"].expected.items():
            assert row.fields_jsonb.get(name) == want, name

    async def test_every_value_says_how_sure_it_is(
        self, birth_unit: uuid.UUID, recorder: Recorder
    ) -> None:
        await run_extract(birth_unit, recorder)

        row = read_row(birth_unit)
        assert row is not None
        assert set(row.field_confidences_jsonb) == set(row.fields_jsonb)
        for name, confidence in row.field_confidences_jsonb.items():
            assert isinstance(confidence, float)
            assert 0.0 <= confidence <= 1.0, name

    async def test_every_value_says_which_layer_read_it(
        self, birth_unit: uuid.UUID, recorder: Recorder
    ) -> None:
        await run_extract(birth_unit, recorder)

        row = read_row(birth_unit)
        assert row is not None
        assert set(row.field_methods_jsonb.values()) == {ExtractionMethod.RULE.value}

    async def test_every_value_says_where_it_came_from(
        self, birth_unit: uuid.UUID, recorder: Recorder
    ) -> None:
        # The review pane draws a box around the words a value was read from.
        await run_extract(birth_unit, recorder)

        row = read_row(birth_unit)
        assert row is not None
        source = row.field_sources_jsonb["child_full_name"]
        assert isinstance(source, dict)
        assert source["page_number"] == 1
        assert "Ayesha" in str(source["snippet"])
        assert source["bbox"] is not None
        assert source["label"]

    async def test_the_row_records_how_the_page_was_read(
        self, birth_unit: uuid.UUID, recorder: Recorder
    ) -> None:
        await run_extract(birth_unit, recorder)

        row = read_row(birth_unit)
        assert row is not None
        assert row.ocr_used is False
        assert row.ocr_mean_confidence is None
        assert row.detected_language == "eng"
        assert row.processed_at is not None

    async def test_the_row_waits_for_validation_before_it_is_routed(
        self, birth_unit: uuid.UUID, recorder: Recorder
    ) -> None:
        # Confidence and routing belong to validation; claiming either here would put a
        # row in front of a reviewer, or past one, on no evidence.
        await run_extract(birth_unit, recorder)

        row = read_row(birth_unit)
        assert row is not None
        assert row.row_confidence == 0.0
        assert row.review_status is ReviewStatus.NEEDS_REVIEW


class TestHandover:
    async def test_the_unit_moves_on_and_validation_is_queued(
        self, birth_unit: uuid.UUID, recorder: Recorder
    ) -> None:
        await run_extract(birth_unit, recorder)

        assert read_unit(birth_unit).status is UnitStatus.EXTRACTED
        assert recorder.calls == [(TASK_VALIDATE_UNIT, {"unit_id": str(birth_unit)})]

    async def test_a_unit_that_has_not_been_classified_is_left_alone(
        self, committed_batch: CommittedBatch, recorder: Recorder, tmp_path: Path
    ) -> None:
        document_id = committed_batch.add_document(
            build_text_pdf(tmp_path / "birth.pdf"), mime_type=MIME_PDF
        )
        await asyncio.to_thread(run_text_stage, document_id, dispatch=recorder)
        await asyncio.to_thread(run_split_stage, document_id, dispatch=recorder)
        with session_scope() as session:
            unit = session.scalar(
                select(CertificateUnit).where(CertificateUnit.document_id == document_id)
            )
            assert unit is not None
            unit_id = unit.id
        recorder.calls.clear()

        result = await run_extract(unit_id, recorder)

        assert result.ran is False
        assert read_row(unit_id) is None
        assert recorder.calls == []

    async def test_a_unit_that_no_longer_exists_is_not_an_error(
        self, committed_batch: CommittedBatch, recorder: Recorder
    ) -> None:
        result = await run_extract(uuid.uuid4(), recorder)
        assert result.ran is False
        del committed_batch


class TestRunningTwice:
    async def test_the_row_is_replaced_not_duplicated(
        self, birth_unit: uuid.UUID, recorder: Recorder
    ) -> None:
        # A re-run after a correction, or a redelivered task, must leave one row.
        first = await run_extract(birth_unit, recorder)
        second = await run_extract(birth_unit, recorder)

        assert first.ran is True
        assert second.ran is True, "an extracted unit may be read again"
        with session_scope() as session:
            rows = list(session.scalars(select(Extraction).where(Extraction.unit_id == birth_unit)))
        assert len(rows) == 1

    async def test_the_second_run_reads_the_same_values(
        self, birth_unit: uuid.UUID, recorder: Recorder
    ) -> None:
        await run_extract(birth_unit, recorder)
        first = read_row(birth_unit)
        await run_extract(birth_unit, recorder)
        second = read_row(birth_unit)

        assert first is not None and second is not None
        assert first.fields_jsonb == second.fields_jsonb


class TestTemplates:
    async def test_an_office_template_is_found_and_used(
        self, committed_batch: CommittedBatch, recorder: Recorder, tmp_path: Path
    ) -> None:
        path = build_text_pdf(tmp_path / "birth.pdf")
        pages = [
            UnitPage(page_number=layout.page_number, layout=layout)
            for layout in iter_pdf_layouts(path)
        ]
        template_id = uuid.uuid4()
        with session_scope() as session:
            session.add(
                Template(
                    id=template_id,
                    workspace_id=committed_batch.workspace_id,
                    name="Punjab birth form",
                    fingerprint=fingerprint_pages(pages),
                    certificate_type=CertificateType.BIRTH,
                    rules_jsonb=TemplateRules(
                        rules=[
                            TemplateRule(
                                field="issuing_authority",
                                anchor="Registrar",
                                direction=AnchorDirection.AFTER,
                            )
                        ]
                    ).model_dump(mode="json"),
                )
            )
        unit_id = await classified_unit(committed_batch, recorder, path)

        result = await run_extract(unit_id, recorder)

        assert result.template_used is True
        row = read_row(unit_id)
        assert row is not None
        assert row.template_id == template_id
        # The template's rule wins over the rules engine for the field it covers.
        assert row.field_methods_jsonb["issuing_authority"] == ExtractionMethod.TEMPLATE.value
        assert row.fields_jsonb["issuing_authority"] == "Muhammad Aslam"

    async def test_a_used_template_is_credited(
        self, committed_batch: CommittedBatch, recorder: Recorder, tmp_path: Path
    ) -> None:
        path = build_text_pdf(tmp_path / "birth.pdf")
        pages = [
            UnitPage(page_number=layout.page_number, layout=layout)
            for layout in iter_pdf_layouts(path)
        ]
        template_id = uuid.uuid4()
        with session_scope() as session:
            session.add(
                Template(
                    id=template_id,
                    workspace_id=committed_batch.workspace_id,
                    name="Punjab birth form",
                    fingerprint=fingerprint_pages(pages),
                    certificate_type=CertificateType.BIRTH,
                    rules_jsonb=TemplateRules(
                        rules=[TemplateRule(field="registrar_name", anchor="Registrar")]
                    ).model_dump(mode="json"),
                )
            )
        unit_id = await classified_unit(committed_batch, recorder, path)

        await run_extract(unit_id, recorder)

        with session_scope() as session:
            template = session.get(Template, template_id)
            assert template is not None
            assert template.hit_count == 1

    async def test_a_template_whose_form_has_changed_is_not_credited(
        self, committed_batch: CommittedBatch, recorder: Recorder, tmp_path: Path
    ) -> None:
        # The anchor is gone, so the template contributes nothing and the rules engine
        # answers instead - visibly, in the stored method.
        path = build_text_pdf(tmp_path / "birth.pdf")
        pages = [
            UnitPage(page_number=layout.page_number, layout=layout)
            for layout in iter_pdf_layouts(path)
        ]
        with session_scope() as session:
            session.add(
                Template(
                    workspace_id=committed_batch.workspace_id,
                    name="Old form",
                    fingerprint=fingerprint_pages(pages),
                    certificate_type=CertificateType.BIRTH,
                    rules_jsonb=TemplateRules(
                        rules=[
                            TemplateRule(
                                field="child_full_name", anchor="Full Name of the Child (Block)"
                            )
                        ]
                    ).model_dump(mode="json"),
                )
            )
        unit_id = await classified_unit(committed_batch, recorder, path)

        result = await run_extract(unit_id, recorder)

        assert result.template_used is False
        row = read_row(unit_id)
        assert row is not None
        assert row.template_id is None
        assert row.fields_jsonb["child_full_name"] == "Ayesha Noor Malik"

    async def test_another_workspaces_template_is_not_used(
        self, committed_batch: CommittedBatch, recorder: Recorder, tmp_path: Path
    ) -> None:
        from certex.db.models import Workspace

        path = build_text_pdf(tmp_path / "birth.pdf")
        pages = [
            UnitPage(page_number=layout.page_number, layout=layout)
            for layout in iter_pdf_layouts(path)
        ]
        with session_scope() as session:
            stranger = Workspace(name="Another Office", settings_json={})
            session.add(stranger)
            session.flush()
            session.add(
                Template(
                    workspace_id=stranger.id,
                    name="Theirs",
                    fingerprint=fingerprint_pages(pages),
                    certificate_type=CertificateType.BIRTH,
                    rules_jsonb=TemplateRules(
                        rules=[TemplateRule(field="registrar_name", anchor="Certificate No.")]
                    ).model_dump(mode="json"),
                )
            )
        unit_id = await classified_unit(committed_batch, recorder, path)

        result = await run_extract(unit_id, recorder)

        assert result.template_used is False
        row = read_row(unit_id)
        assert row is not None
        assert row.fields_jsonb["registrar_name"] == "Muhammad Aslam"


class TestCorrectionsSurvive:
    async def test_a_value_a_person_typed_is_not_overwritten_by_a_re_run(
        self, birth_unit: uuid.UUID, recorder: Recorder
    ) -> None:
        # A reviewer whose corrections are silently undone stops reviewing.
        await run_extract(birth_unit, recorder)
        with session_scope() as session:
            row = session.scalar(select(Extraction).where(Extraction.unit_id == birth_unit))
            assert row is not None
            row.fields_jsonb = {**row.fields_jsonb, "father_full_name": "Tariq Mahmood Malik"}
            row.field_methods_jsonb = {
                **row.field_methods_jsonb,
                "father_full_name": ExtractionMethod.MANUAL.value,
            }
            row.field_confidences_jsonb = {
                **row.field_confidences_jsonb,
                "father_full_name": 1.0,
            }
            unit = session.get(CertificateUnit, birth_unit)
            assert unit is not None
            unit.status = UnitStatus.CLASSIFIED  # as a reprocess request leaves it

        await run_extract(birth_unit, recorder)

        row = read_row(birth_unit)
        assert row is not None
        assert row.fields_jsonb["father_full_name"] == "Tariq Mahmood Malik"
        assert row.field_methods_jsonb["father_full_name"] == ExtractionMethod.MANUAL.value
        assert row.field_confidences_jsonb["father_full_name"] == 1.0

    async def test_everything_else_is_read_again(
        self, birth_unit: uuid.UUID, recorder: Recorder
    ) -> None:
        await run_extract(birth_unit, recorder)
        with session_scope() as session:
            row = session.scalar(select(Extraction).where(Extraction.unit_id == birth_unit))
            assert row is not None
            row.fields_jsonb = {**row.fields_jsonb, "child_full_name": "Wrong Name"}
            unit = session.get(CertificateUnit, birth_unit)
            assert unit is not None
            unit.status = UnitStatus.CLASSIFIED

        await run_extract(birth_unit, recorder)

        assert read_row(birth_unit).fields_jsonb["child_full_name"] == "Ayesha Noor Malik"


class TestExtraFields:
    async def test_a_label_the_schema_does_not_know_is_kept(
        self, committed_batch: CommittedBatch, recorder: Recorder, tmp_path: Path
    ) -> None:
        unit_id = await classified_unit(
            committed_batch, recorder, build_text_pdf(tmp_path / "birth.pdf")
        )

        result = await run_extract(unit_id, recorder)

        row = read_row(unit_id)
        assert row is not None
        assert isinstance(row.extra_fields_jsonb, dict)
        assert result.extra_field_count == len(row.extra_fields_jsonb)


class TestAnUnclassifiedCertificate:
    async def test_the_common_fields_are_still_read(
        self, committed_batch: CommittedBatch, recorder: Recorder, tmp_path: Path
    ) -> None:
        # The classifier abstained, so the type is OTHER - but a certificate number and
        # an issuing authority are worth having anyway.
        unit_id = await classified_unit(
            committed_batch, recorder, build_text_pdf(tmp_path / "birth.pdf")
        )
        with session_scope() as session:
            unit = session.get(CertificateUnit, unit_id)
            assert unit is not None
            unit.certificate_type = CertificateType.OTHER
            unit.type_confidence = 0.0
            unit.classification_method = ClassificationMethod.DEFAULT
            unit.boundary_method = BoundaryMethod.SINGLE_DOCUMENT

        await run_extract(unit_id, recorder)

        row = read_row(unit_id)
        assert row is not None
        assert row.fields_jsonb["certificate_number"] == "BC-2019-004471"
        assert "child_full_name" not in row.fields_jsonb
