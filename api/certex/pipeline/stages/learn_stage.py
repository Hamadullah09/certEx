"""Stage 9: learn this office's form from a correction that was just made.

Runs after a reviewer corrects a row, never as part of the ingest pipeline, because
there is nothing to learn from until somebody has said what the right answer was.

The stage is deliberately forgiving about doing nothing. A correction to a certificate
whose scan was unreadable, to a form seen only once, or to a field whose value does not
appear on the page at all, all end here with no template written - and that is the
normal case, not a failure. What it must never do is write a rule that would then be
applied with template confidence to every other copy of the form.

Merging is per field. A template already holding a rule for ``father_full_name`` and a
correction teaching a better one keeps the newer rule and leaves every other field
alone: the reviewer corrected one field, and the rules for the rest are still whatever
earned them.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from certex.db.models import CertificateUnit, Document, Extraction, PageText, Template
from certex.db.session import session_scope
from certex.enums import CertificateType, ExtractionMethod
from certex.logging_setup import get_logger, safe_error
from certex.pipeline.extract.learn import learn_rules
from certex.pipeline.extract.rules import UnitPage
from certex.pipeline.extract.templates import fingerprint_pages
from certex.schemas.layout import PageLayout
from certex.schemas.template import TemplateRule, TemplateRules

__all__ = ["LearnStageResult", "run_learn_template_stage"]

logger = get_logger(__name__)

_MAX_NAME_LENGTH = 200


@dataclass(frozen=True, slots=True)
class LearnStageResult:
    unit_id: uuid.UUID
    ran: bool
    template_id: uuid.UUID | None = None
    learned: int = 0
    created: bool = False


@dataclass(slots=True)
class _LearnInput:
    workspace_id: uuid.UUID
    certificate_type: CertificateType
    issuing_authority: str | None
    corrected: dict[str, str] = field(default_factory=dict)
    pages: list[UnitPage] = field(default_factory=list)


def _load(unit_id: uuid.UUID) -> _LearnInput | None:
    """Everything learning reads, in one transaction."""
    with session_scope() as session:
        unit = session.get(CertificateUnit, unit_id)
        if unit is None:
            return None
        document = session.get(Document, unit.document_id)
        if document is None:
            return None
        row = session.scalar(select(Extraction).where(Extraction.unit_id == unit_id))
        if row is None:
            return None

        corrected = {
            name: str(row.fields_jsonb.get(name, ""))
            for name, method in row.field_methods_jsonb.items()
            if method == ExtractionMethod.MANUAL.value and row.fields_jsonb.get(name)
        }
        if not corrected:
            return None

        pages: list[UnitPage] = []
        texts = session.scalars(
            select(PageText)
            .where(
                PageText.document_id == unit.document_id,
                PageText.page_number >= unit.page_start,
                PageText.page_number <= unit.page_end,
            )
            .order_by(PageText.page_number)
        )
        for text in texts:
            try:
                layout = PageLayout.model_validate(text.layout_blocks_jsonb)
            except ValueError as exc:
                logger.warning(
                    "learn.layout_unreadable",
                    page_number=text.page_number,
                    error_type=safe_error(exc),
                )
                continue
            pages.append(UnitPage(page_number=text.page_number, layout=layout))

        return _LearnInput(
            workspace_id=document.workspace_id,
            certificate_type=unit.certificate_type,
            issuing_authority=_as_text(row.fields_jsonb.get("issuing_authority")),
            corrected=corrected,
            pages=pages,
        )


def _as_text(value: object) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def _template_name(data: _LearnInput) -> str:
    """What a clerk should see in the templates list.

    The issuing authority is the only thing on a certificate that names the office whose
    form this is, so it makes a far more recognisable name than a hash would.
    """
    kind = data.certificate_type.value.title()
    if data.issuing_authority:
        return f"{data.issuing_authority} - {kind.lower()} form"[:_MAX_NAME_LENGTH]
    return f"{kind} form"[:_MAX_NAME_LENGTH]


def _merged(existing: TemplateRules | None, learned: list[TemplateRule]) -> TemplateRules:
    """The newer rule wins for a field, and every other field is left as it was."""
    by_field: dict[str, TemplateRule] = {}
    anchors: list[str] = []
    if existing is not None:
        by_field = {rule.field: rule for rule in existing.rules}
        anchors = list(existing.anchors)
    for rule in learned:
        by_field[rule.field] = rule
    return TemplateRules(rules=list(by_field.values()), anchors=anchors)


def run_learn_template_stage(unit_id: uuid.UUID) -> LearnStageResult:
    """Learn what this correction says about the form, if anything."""
    data = _load(unit_id)
    if data is None or not data.pages:
        return LearnStageResult(unit_id=unit_id, ran=False)

    learned = learn_rules(data.pages, data.corrected)
    if not learned:
        logger.info("learn.nothing_to_learn", unit_id=str(unit_id), corrected=len(data.corrected))
        return LearnStageResult(unit_id=unit_id, ran=True)

    fingerprint = fingerprint_pages(data.pages)
    with session_scope() as session:
        template = session.scalar(
            select(Template).where(
                Template.workspace_id == data.workspace_id,
                Template.fingerprint == fingerprint,
            )
        )
        created = template is None
        existing: TemplateRules | None = None
        if template is not None:
            try:
                existing = TemplateRules.model_validate(template.rules_jsonb)
            except ValueError as exc:
                # A rule set this release cannot read is replaced rather than merged
                # into: keeping half of something unparseable would be worse.
                logger.warning("learn.rules_unreadable", error_type=safe_error(exc))

        rules = _merged(existing, learned)
        if template is None:
            template = Template(
                workspace_id=data.workspace_id,
                name=_template_name(data),
                fingerprint=fingerprint,
                certificate_type=data.certificate_type,
                rules_jsonb=rules.model_dump(mode="json"),
            )
            session.add(template)
        else:
            template.rules_jsonb = rules.model_dump(mode="json")
            # A template that was switched off and is now being taught again is back in
            # use: somebody corrected this form, which is the only vote that counts.
            template.is_active = True

        try:
            session.flush()
        except IntegrityError:
            # Two reviewers corrected two copies of the same form at the same moment.
            # The other one won the unique constraint; its rules are as good as these.
            session.rollback()
            logger.info("learn.raced", fingerprint=fingerprint)
            return LearnStageResult(unit_id=unit_id, ran=True)

        template_id = template.id
        logger.info(
            "learn.template_written",
            unit_id=str(unit_id),
            template_id=str(template_id),
            learned=len(learned),
            created=created,
        )
        return LearnStageResult(
            unit_id=unit_id,
            ran=True,
            template_id=template_id,
            learned=len(learned),
            created=created,
        )
