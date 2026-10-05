"""Turning a correction into a rule.

The thing that matters is not how many rules get learned - it is that a learned rule
reads the *next* copy of the form correctly. So most of these tests learn from one copy
and then apply what was learned to a second copy carrying different people's details,
which is the only check that distinguishes a real rule from one that memorised the
certificate in front of it.

The other half is about refusing to learn. A rule is applied with high confidence to
every copy of a form, so a wrong one is worse than none at all.
"""

from __future__ import annotations

import pytest

from certex.pipeline.extract.learn import learn_rules
from certex.pipeline.extract.rules import UnitPage
from certex.pipeline.extract.templates import apply_template
from certex.pipeline.text.layout_builder import PositionedWord, build_layout
from certex.schemas.layout import Table, TableCell
from certex.schemas.template import AnchorDirection, TemplateRules

pytestmark = pytest.mark.unit

WIDTH = 600.0
HEIGHT = 800.0


def page_of(lines: list[str], *, tables: list[Table] | None = None, scale: float = 1.0) -> UnitPage:
    """A synthetic page; ``scale`` stands in for the same form scanned larger."""
    words: list[PositionedWord] = []
    for row, line in enumerate(lines):
        if not line:
            continue
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


def read_back(pages: list[UnitPage], rules: list) -> dict[str, str]:
    """What a template made of these rules reads off these pages."""
    found = apply_template(pages, TemplateRules(rules=rules))
    return {name: candidate.value for name, candidate in found.items()}


class TestLearningFromALabelledLine:
    def test_a_rule_learned_from_one_copy_reads_the_next(self) -> None:
        corrected = page_of(
            [
                "CERTIFICATE OF BIRTH",
                "Name of Child: Ayesha Noor Malik",
                "Father's Name: Tariq Mahmood Malik",
            ]
        )
        rules = learn_rules([corrected], {"child_full_name": "Ayesha Noor Malik"})
        assert len(rules) == 1
        assert rules[0].direction is AnchorDirection.AFTER

        next_copy = page_of(
            [
                "CERTIFICATE OF BIRTH",
                "Name of Child: Bilal Ahmed Khan",
                "Father's Name: Ahmed Khan",
            ]
        )
        assert read_back([next_copy], rules) == {"child_full_name": "Bilal Ahmed Khan"}

    def test_the_anchor_is_the_label_and_not_the_whole_line(self) -> None:
        page = page_of(["Place of Birth: Services Hospital, Lahore"])
        rules = learn_rules([page], {"place_of_birth": "Services Hospital, Lahore"})
        assert rules[0].anchor == "Place of Birth"

    def test_a_rescan_at_a_different_size_still_reads(self) -> None:
        """Nothing learned is positional, so the same form fed larger still matches."""
        rules = learn_rules(
            [page_of(["Registrar: Muhammad Aslam"])], {"registrar_name": "Muhammad Aslam"}
        )
        larger = page_of(["Registrar: Fatima Bibi"], scale=1.4)
        assert read_back([larger], rules) == {"registrar_name": "Fatima Bibi"}

    def test_a_single_letter_value_anchors_on_the_letter_that_stands_alone(self) -> None:
        """ "F" for a sex field occurs inside "Father" too, which must not be matched."""
        page = page_of(["Father's Name: Tariq Mahmood", "Sex: F"])
        rules = learn_rules([page], {"sex": "F"})
        assert [rule.anchor for rule in rules] == ["Sex"]


class TestLearningFromTheLineBelow:
    def test_a_value_printed_under_its_label(self) -> None:
        page = page_of(["Permanent Address", "House 14, Gulberg III, Lahore"])
        rules = learn_rules([page], {"permanent_address": "House 14, Gulberg III, Lahore"})
        assert len(rules) == 1
        assert rules[0].direction is AnchorDirection.BELOW
        assert rules[0].anchor == "Permanent Address"

    def test_the_rule_allows_for_a_slightly_taller_rescan(self) -> None:
        """A gap learned exactly would fail on the next scan, which is never identical."""
        page = page_of(["Permanent Address", "House 14, Gulberg III, Lahore"])
        rule = learn_rules([page], {"permanent_address": "House 14, Gulberg III, Lahore"})[0]
        measured = page.layout.lines[1].bbox.y0 - page.layout.lines[0].bbox.y1
        assert rule.max_distance > measured


class TestLearningFromATable:
    def test_a_value_in_the_cell_beside_its_label(self) -> None:
        table = Table(
            rows=[
                [TableCell(text="Date of Birth"), TableCell(text="1987-03-14")],
                [TableCell(text="Time of Birth"), TableCell(text="04:25")],
            ]
        )
        page = page_of(["BIRTH CERTIFICATE"], tables=[table])
        rules = learn_rules([page], {"date_of_birth": "1987-03-14"})
        assert len(rules) == 1
        assert rules[0].direction is AnchorDirection.CELL_RIGHT
        assert rules[0].anchor == "Date of Birth"

    def test_the_rule_reads_the_same_cell_on_the_next_copy(self) -> None:
        learned = learn_rules(
            [
                page_of(
                    ["BIRTH CERTIFICATE"],
                    tables=[
                        Table(
                            rows=[[TableCell(text="Date of Birth"), TableCell(text="1987-03-14")]]
                        )
                    ],
                )
            ],
            {"date_of_birth": "1987-03-14"},
        )
        next_copy = page_of(
            ["BIRTH CERTIFICATE"],
            tables=[Table(rows=[[TableCell(text="Date of Birth"), TableCell(text="1992-11-02")]])],
        )
        assert read_back([next_copy], learned) == {"date_of_birth": "1992-11-02"}


class TestRefusingToLearn:
    def test_a_value_that_is_not_on_the_page_teaches_nothing(self) -> None:
        """A clerk reading a number off the paper because the scan was unreadable has
        told us about this certificate, not about the form."""
        page = page_of(["CERTIFICATE OF BIRTH", "Name of Child: Ayesha Noor Malik"])
        assert learn_rules([page], {"father_id_number": "35201-1234567-1"}) == []

    def test_a_label_carrying_digits_is_not_an_anchor(self) -> None:
        """The digits are the part that changes between copies, so a "label" holding
        them is really somebody's value and will not be there next time."""
        page = page_of(["Certificate No. 2019-004471 Issued At: Lahore"])
        assert learn_rules([page], {"issuing_authority": "Lahore"}) == []

    def test_a_label_too_short_to_be_a_label_is_refused(self) -> None:
        page = page_of(["To: Lahore"])
        assert learn_rules([page], {"issuing_authority": "Lahore"}) == []

    def test_an_empty_correction_teaches_nothing(self) -> None:
        page = page_of(["Name of Child: Ayesha Noor Malik"])
        assert learn_rules([page], {"child_full_name": None}) == []
        assert learn_rules([page], {"child_full_name": ""}) == []

    def test_a_value_alone_on_a_page_with_no_label_above_it(self) -> None:
        page = page_of(["Ayesha Noor Malik"])
        assert learn_rules([page], {"child_full_name": "Ayesha Noor Malik"}) == []


class TestLearningSeveralFieldsAtOnce:
    def test_each_corrected_field_gets_its_own_rule(self) -> None:
        page = page_of(
            [
                "CERTIFICATE OF BIRTH",
                "Name of Child: Ayesha Noor Malik",
                "Father's Name: Tariq Mahmood Malik",
                "Place of Birth: Services Hospital",
            ]
        )
        rules = learn_rules(
            [page],
            {
                "child_full_name": "Ayesha Noor Malik",
                "father_full_name": "Tariq Mahmood Malik",
                "place_of_birth": "Services Hospital",
            },
        )
        assert {rule.field for rule in rules} == {
            "child_full_name",
            "father_full_name",
            "place_of_birth",
        }

    def test_the_fields_it_cannot_place_do_not_stop_the_ones_it_can(self) -> None:
        page = page_of(["Name of Child: Ayesha Noor Malik"])
        rules = learn_rules(
            [page],
            {"child_full_name": "Ayesha Noor Malik", "mother_full_name": "Nasreen Akhtar"},
        )
        assert [rule.field for rule in rules] == ["child_full_name"]
