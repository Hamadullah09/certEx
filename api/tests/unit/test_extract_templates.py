"""Recognising a form, and reading it the way it was read last time.

A template is worth having only if it survives the things that change between two copies
of one form - different people's details, a rescan at another size, a page fed a degree
crooked - and stops applying when the form itself changes. Both halves are tested here:
what the fingerprint ignores, and what it does not.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from certex.enums import CertificateType, ExtractionMethod
from certex.pipeline.extract.extractor import extract_fields
from certex.pipeline.extract.rules import UnitPage
from certex.pipeline.extract.templates import (
    anchor_lines,
    apply_template,
    fingerprint_pages,
)
from certex.pipeline.text.layout_builder import PositionedWord, build_layout
from certex.pipeline.text.pdf_native import iter_pdf_layouts
from certex.schemas.layout import Table, TableCell
from certex.schemas.template import AnchorDirection, TemplateRule, TemplateRules
from tests.fixtures.builders import build_text_pdf

pytestmark = pytest.mark.unit

WIDTH = 600.0
HEIGHT = 800.0


def page_of(lines: list[str], *, tables: list[Table] | None = None, scale: float = 1.0) -> UnitPage:
    """A synthetic page; ``scale`` stands in for the same form scanned larger."""
    words: list[PositionedWord] = []
    for row, line in enumerate(lines):
        cursor = 50.0 * scale
        for order, token in enumerate(line.split(" ")):
            width = len(token) * 6.0 * scale
            words.append(
                PositionedWord(
                    text=token,
                    x0=cursor,
                    y0=(60.0 + row * 30.0) * scale,
                    x1=cursor + width,
                    y1=(60.0 + row * 30.0 + 12.0) * scale,
                    line_key=(row,),
                    order=order,
                )
            )
            cursor += width + 6.0 * scale
    layout = build_layout(
        words,
        page_number=1,
        width=WIDTH * scale,
        height=HEIGHT * scale,
        unit="pt",
        engine="pymupdf",
        tables=tables,
        engine_order=True,
    )
    return UnitPage(page_number=1, layout=layout)


FORM = [
    "CERTIFICATE OF BIRTH",
    "GOVERNMENT OF THE PUNJAB",
    "Certificate No.: {number}",
    "Name of Child: {child}",
    "Father's Name: {father}",
]


def filled(number: str, child: str, father: str, *, scale: float = 1.0) -> UnitPage:
    return page_of(
        [line.format(number=number, child=child, father=father) for line in FORM], scale=scale
    )


class TestFingerprinting:
    def test_two_copies_of_one_form_fingerprint_the_same(self) -> None:
        first = filled("BC-2019-004471", "Ayesha Noor Malik", "Tariq Mahmood Malik")
        second = filled("BC-2021-009912", "Bilal Ahmed", "Imran Shah")
        assert fingerprint_pages([first]) == fingerprint_pages([second])

    def test_the_same_form_scanned_at_another_size_fingerprints_the_same(self) -> None:
        # A rescan lands a few millimetres over and at a slightly different size.
        original = filled("BC-2019-004471", "Ayesha Noor Malik", "Tariq Mahmood Malik")
        rescanned = filled("BC-2019-004471", "Ayesha Noor Malik", "Tariq Mahmood Malik", scale=1.2)
        assert fingerprint_pages([original]) == fingerprint_pages([rescanned])

    def test_a_different_office_form_fingerprints_differently(self) -> None:
        punjab = filled("BC-2019-004471", "Ayesha Noor Malik", "Tariq Mahmood Malik")
        sindh = page_of(
            [
                "CERTIFICATE OF BIRTH",
                "GOVERNMENT OF SINDH",
                "Certificate No.: BC-2019-004471",
                "Child's Name: Ayesha Noor Malik",
                "Father's Name: Tariq Mahmood Malik",
            ]
        )
        assert fingerprint_pages([punjab]) != fingerprint_pages([sindh])

    def test_a_fingerprint_is_a_digest(self) -> None:
        value = fingerprint_pages([filled("BC-1", "A", "B")])
        assert len(value) == 64
        assert all(char in "0123456789abcdef" for char in value)

    def test_an_empty_page_still_fingerprints(self) -> None:
        assert len(fingerprint_pages([page_of([])])) == 64

    def test_a_real_certificate_fingerprints_stably(self, tmp_path: Path) -> None:
        pages = [
            UnitPage(page_number=layout.page_number, layout=layout)
            for layout in iter_pdf_layouts(build_text_pdf(tmp_path / "birth.pdf"))
        ]
        assert fingerprint_pages(pages) == fingerprint_pages(pages)


class TestAnchors:
    def test_anchors_are_the_printed_furniture_not_the_details(self) -> None:
        anchors = anchor_lines(filled("BC-2019-004471", "Ayesha Noor Malik", "X").layout)
        assert "certificate of birth" in anchors
        assert "government of the punjab" in anchors

    def test_a_line_carrying_a_number_is_not_an_anchor(self) -> None:
        anchors = anchor_lines(filled("BC-2019-004471", "Ayesha", "Tariq").layout)
        assert not any("bc-2019" in anchor for anchor in anchors)

    def test_a_label_is_an_anchor_without_its_value(self) -> None:
        anchors = anchor_lines(filled("BC-1", "Ayesha Noor Malik", "Tariq").layout)
        assert "name of child" in anchors
        assert not any("ayesha" in anchor for anchor in anchors)


class TestApplyingRules:
    def test_a_value_after_its_anchor(self) -> None:
        page = filled("BC-2019-004471", "Ayesha Noor Malik", "Tariq Mahmood Malik")
        rules = TemplateRules(rules=[TemplateRule(field="child_full_name", anchor="Name of Child")])
        found = apply_template([page], rules)
        assert found["child_full_name"].value == "Ayesha Noor Malik"
        assert found["child_full_name"].method is ExtractionMethod.TEMPLATE

    def test_a_value_below_its_anchor(self) -> None:
        page = page_of(["Name of Child", "Ayesha Noor Malik"])
        rules = TemplateRules(
            rules=[
                TemplateRule(
                    field="child_full_name",
                    anchor="Name of Child",
                    direction=AnchorDirection.BELOW,
                )
            ]
        )
        assert apply_template([page], rules)["child_full_name"].value == "Ayesha Noor Malik"

    def test_a_value_in_the_cell_beside_its_anchor(self) -> None:
        table = Table(rows=[[TableCell(text="Name of Child"), TableCell(text="Ayesha Noor Malik")]])
        page = page_of(["CERTIFICATE OF BIRTH"], tables=[table])
        rules = TemplateRules(
            rules=[
                TemplateRule(
                    field="child_full_name",
                    anchor="Name of Child",
                    direction=AnchorDirection.CELL_RIGHT,
                )
            ]
        )
        assert apply_template([page], rules)["child_full_name"].value == "Ayesha Noor Malik"

    def test_a_pattern_takes_only_what_it_matches(self) -> None:
        page = page_of(["Father's CNIC: 35201-1234567-1 (verified)"])
        rules = TemplateRules(
            rules=[
                TemplateRule(
                    field="father_id_number",
                    anchor="Father's CNIC",
                    pattern=r"\d{5}-\d{7}-\d",
                )
            ]
        )
        assert apply_template([page], rules)["father_id_number"].value == "35201-1234567-1"

    def test_a_value_that_does_not_match_the_pattern_is_not_taken(self) -> None:
        page = page_of(["Father's CNIC: applied for"])
        rules = TemplateRules(
            rules=[
                TemplateRule(
                    field="father_id_number",
                    anchor="Father's CNIC",
                    pattern=r"\d{5}-\d{7}-\d",
                )
            ]
        )
        assert apply_template([page], rules) == {}

    def test_an_anchor_that_is_no_longer_printed_yields_nothing(self) -> None:
        # The office reprinted its form; the template must fail visibly, not quietly
        # return the wrong half of some other line.
        page = filled("BC-1", "Ayesha Noor Malik", "Tariq")
        rules = TemplateRules(
            rules=[TemplateRule(field="child_full_name", anchor="Name of the Child (Full)")]
        )
        assert apply_template([page], rules) == {}

    def test_a_template_reads_the_same_form_scanned_larger(self) -> None:
        rescanned = filled("BC-1", "Ayesha Noor Malik", "Tariq", scale=1.4)
        rules = TemplateRules(rules=[TemplateRule(field="child_full_name", anchor="Name of Child")])
        assert apply_template([rescanned], rules)["child_full_name"].value == "Ayesha Noor Malik"

    def test_provenance_records_the_anchor(self) -> None:
        page = filled("BC-1", "Ayesha Noor Malik", "Tariq")
        rules = TemplateRules(rules=[TemplateRule(field="child_full_name", anchor="Name of Child")])
        source = apply_template([page], rules)["child_full_name"].source
        assert source.label == "Name of Child"
        assert source.page_number == 1
        assert source.bbox is not None


class TestTemplatesWithTheRulesEngine:
    def test_a_template_value_is_preferred(self) -> None:
        page = filled("BC-2019-004471", "Ayesha Noor Malik", "Tariq Mahmood Malik")
        rules = TemplateRules(
            rules=[TemplateRule(field="child_full_name", anchor="Certificate No.")]
        )

        extracted = extract_fields(
            [page], certificate_type=CertificateType.BIRTH, template_rules=rules
        )

        # Deliberately a bad rule: the point is that the template's answer is the one
        # that survives the merge, which is what makes a correction stick.
        assert extracted.value("child_full_name") == "BC-2019-004471"
        assert extracted.template_used is True

    def test_fields_the_template_says_nothing_about_still_come_from_the_rules(self) -> None:
        page = filled("BC-2019-004471", "Ayesha Noor Malik", "Tariq Mahmood Malik")
        rules = TemplateRules(rules=[TemplateRule(field="child_full_name", anchor="Name of Child")])

        extracted = extract_fields(
            [page], certificate_type=CertificateType.BIRTH, template_rules=rules
        )

        assert extracted.value("father_full_name") == "Tariq Mahmood Malik"
        assert extracted.fields["father_full_name"].method is ExtractionMethod.RULE

    def test_without_a_template_everything_comes_from_the_rules(self) -> None:
        page = filled("BC-2019-004471", "Ayesha Noor Malik", "Tariq Mahmood Malik")
        extracted = extract_fields([page], certificate_type=CertificateType.BIRTH)
        assert extracted.template_used is False
        assert extracted.value("child_full_name") == "Ayesha Noor Malik"


class TestATemplateCannotWidenTheBatchSchema:
    """A template is recognised by the form, not by the batch that learned it.

    Two batches can hold the same printed form and ask for different columns - that is
    the whole point of per-batch schemas. The template knows where twenty fields sit;
    a batch that asked for four must still get four, because the fifth has no column in
    its CSV, no field in its register entry and nothing a reviewer could correct.
    """

    def test_a_field_outside_the_schema_is_dropped(self) -> None:
        from certex.fields import FieldKind, FieldSchema, FieldSpec

        page = page_of(
            [
                "CERTIFICATE OF BIRTH",
                "Name of Child: Ayesha Noor Malik",
                "Issuing Authority: Union Council 42, Lahore",
            ]
        )
        narrow = FieldSchema(
            version="test",
            certificate_type=CertificateType.BIRTH,
            fields=(
                FieldSpec(
                    name="child_full_name",
                    label="Name of Child",
                    kind=FieldKind.NAME,
                ),
            ),
        )
        rules = TemplateRules(
            rules=[
                TemplateRule(field="child_full_name", anchor="Name of Child"),
                TemplateRule(field="issuing_authority", anchor="Issuing Authority"),
            ]
        )

        extracted = extract_fields(
            [page],
            certificate_type=CertificateType.BIRTH,
            template_rules=rules,
            schema=narrow,
        )

        assert "child_full_name" in extracted.fields
        assert "issuing_authority" not in extracted.fields, (
            "a template wrote a column this batch never asked for"
        )
