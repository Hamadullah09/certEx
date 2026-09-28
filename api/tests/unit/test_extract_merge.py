"""Choosing between what the layers found.

The template wins by default because it encodes a person's decision about this exact
form. The margin exists because a template can go stale - the office reprints its form,
the anchor shifts, and the rule starts returning the wrong half of a line with a
confidence that never falls.
"""

from __future__ import annotations

import pytest

from certex.enums import ExtractionMethod
from certex.pipeline.extract.candidates import Candidate, FieldSource
from certex.pipeline.extract.merge import OVERRIDE_MARGIN, merge_candidates

pytestmark = pytest.mark.unit


def candidate(
    value: str, *, confidence: float, method: ExtractionMethod, field: str = "child_full_name"
) -> Candidate:
    return Candidate(
        field=field,
        value=value,
        confidence=confidence,
        method=method,
        source=FieldSource(page_number=1, snippet=value),
    )


def template(value: str, confidence: float = 0.95, **kwargs: str) -> dict[str, Candidate]:
    field = kwargs.get("field", "child_full_name")
    return {
        field: candidate(
            value, confidence=confidence, method=ExtractionMethod.TEMPLATE, field=field
        )
    }


def rules(value: str, confidence: float = 0.9, **kwargs: str) -> dict[str, Candidate]:
    field = kwargs.get("field", "child_full_name")
    return {
        field: candidate(value, confidence=confidence, method=ExtractionMethod.RULE, field=field)
    }


class TestPriority:
    def test_the_template_wins_when_both_layers_read_a_field(self) -> None:
        merged = merge_candidates(template("From template"), rules("From rules"))
        assert merged["child_full_name"].value == "From template"
        assert merged["child_full_name"].method is ExtractionMethod.TEMPLATE

    def test_the_template_wins_even_when_slightly_less_confident(self) -> None:
        merged = merge_candidates(template("From template", 0.7), rules("From rules", 0.85))
        assert merged["child_full_name"].value == "From template"

    def test_a_clearly_surer_reading_overturns_a_stale_template(self) -> None:
        # More than the margin better: the anchor has probably moved.
        merged = merge_candidates(template("Wrong half of a line", 0.5), rules("Ayesha", 0.95))
        assert merged["child_full_name"].value == "Ayesha"
        assert merged["child_full_name"].method is ExtractionMethod.RULE

    def test_the_margin_is_a_threshold_not_a_tendency(self) -> None:
        just_under = merge_candidates(
            template("From template", 0.5), rules("From rules", 0.5 + OVERRIDE_MARGIN)
        )
        just_over = merge_candidates(
            template("From template", 0.5), rules("From rules", 0.5 + OVERRIDE_MARGIN + 0.01)
        )
        assert just_under["child_full_name"].value == "From template"
        assert just_over["child_full_name"].value == "From rules"


class TestGaps:
    def test_a_field_only_one_layer_found_is_taken_from_that_layer(self) -> None:
        merged = merge_candidates(
            template("BC-2019-004471", field="certificate_number"),
            rules("Ayesha Noor Malik"),
        )
        assert merged["certificate_number"].value == "BC-2019-004471"
        assert merged["child_full_name"].value == "Ayesha Noor Malik"

    def test_an_empty_value_never_wins(self) -> None:
        merged = merge_candidates(template("", 0.99), rules("Ayesha Noor Malik", 0.6))
        assert merged["child_full_name"].value == "Ayesha Noor Malik"

    def test_an_empty_value_is_not_stored_at_all(self) -> None:
        assert merge_candidates(template("", 0.99)) == {}

    def test_no_layers_produce_nothing(self) -> None:
        assert merge_candidates() == {}

    def test_empty_layers_produce_nothing(self) -> None:
        assert merge_candidates({}, {}) == {}


class TestProvenanceSurvives:
    def test_the_winning_candidate_keeps_its_method_and_source(self) -> None:
        merged = merge_candidates(rules("Ayesha Noor Malik"))
        winner = merged["child_full_name"]
        assert winner.method is ExtractionMethod.RULE
        assert winner.source.snippet == "Ayesha Noor Malik"
