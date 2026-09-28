"""Scoring extraction against hand-labelled documents.

A single percentage tells you whether the last change helped; it does not tell you which
field broke. So every run prints a per-field table, and the assertions are about the
whole corpus *and* about every individual document, because an average hides a document
that reads nothing.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from certex.enums import CertificateType
from certex.pipeline.extract.extractor import extract_fields
from certex.pipeline.extract.rules import UnitPage
from certex.pipeline.text.docx_reader import read_docx_pages
from certex.pipeline.text.pdf_native import iter_pdf_layouts

__all__ = ["Scoreboard", "docx_unit_pages", "pdf_unit_pages", "score_document"]


@dataclass(frozen=True, slots=True)
class FieldOutcome:
    document: str
    field: str
    expected: str
    actual: str | None

    @property
    def correct(self) -> bool:
        return self.actual == self.expected


@dataclass(slots=True)
class Scoreboard:
    """What every document read, field by field."""

    outcomes: list[FieldOutcome] = field(default_factory=list)

    def add(self, outcome: FieldOutcome) -> None:
        self.outcomes.append(outcome)

    @property
    def accuracy(self) -> float:
        return sum(1 for item in self.outcomes if item.correct) / max(len(self.outcomes), 1)

    def accuracy_for(self, document: str) -> float:
        rows = [item for item in self.outcomes if item.document == document]
        return sum(1 for item in rows if item.correct) / max(len(rows), 1)

    @property
    def documents(self) -> list[str]:
        seen: list[str] = []
        for item in self.outcomes:
            if item.document not in seen:
                seen.append(item.document)
        return seen

    @property
    def misses(self) -> list[FieldOutcome]:
        return [item for item in self.outcomes if not item.correct]

    def report(self, title: str) -> str:
        """A per-field table, printed whether or not the run passed."""
        by_field: dict[str, list[FieldOutcome]] = {}
        for item in self.outcomes:
            by_field.setdefault(item.field, []).append(item)

        lines = [
            "",
            f"{title}: {self.accuracy:.1%} ({len(self.outcomes) - len(self.misses)}"
            f"/{len(self.outcomes)} fields)",
            "",
            f"  {'field':<24}  {'read':>7}  {'of':>3}",
        ]
        for name, rows in sorted(by_field.items()):
            correct = sum(1 for row in rows if row.correct)
            marker = "" if correct == len(rows) else "  <-"
            lines.append(f"  {name:<24}  {correct:>7}  {len(rows):>3}{marker}")

        lines.append("")
        for name in self.documents:
            lines.append(f"  {name:<40} {self.accuracy_for(name):.1%}")
        if self.misses:
            lines.append("")
            lines.append("  misses:")
            for item in self.misses:
                lines.append(
                    f"    {item.document} {item.field}: "
                    f"expected {item.expected!r}, read {item.actual!r}"
                )
        return "\n".join(lines)


def pdf_unit_pages(path: Path) -> list[UnitPage]:
    return [
        UnitPage(page_number=layout.page_number, layout=layout) for layout in iter_pdf_layouts(path)
    ]


def docx_unit_pages(path: Path) -> list[UnitPage]:
    return [UnitPage(page_number=page.page_number, layout=page) for page in read_docx_pages(path)]


def score_document(
    board: Scoreboard,
    name: str,
    pages: Sequence[UnitPage],
    *,
    certificate_type: CertificateType,
    expected: dict[str, str],
) -> None:
    """Extract one document and record every expected field as read or missed."""
    extracted = extract_fields(pages, certificate_type=certificate_type)
    for field_name, want in expected.items():
        board.add(
            FieldOutcome(
                document=name,
                field=field_name,
                expected=want,
                actual=extracted.value(field_name),
            )
        )


@pytest.fixture
def board() -> Scoreboard:
    return Scoreboard()
