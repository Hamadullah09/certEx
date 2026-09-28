"""Field accuracy on documents that carry a real text layer.

The specification's bar is 95% of fields read correctly across the corpus. This is the
test that says whether a change to the rules, the synonyms or the layout stage helped or
hurt - so it prints the full per-field table on every run, and fails on any single
document that falls apart even when the average would still pass.

Comparison is exact against the hand-labelled values, after normalisation: "F" not
"Female", "2019-03-14" not "14-03-2019". A value that is merely near enough is wrong,
because the CSV a records office uses has to be right.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from certex.enums import CertificateType
from tests.fixtures.builders import SAMPLES_BY_KEY, build_docx, build_text_pdf
from tests.fixtures.corpus import URDU_BIRTH, arabic_font, build_bilingual_pdf, build_table_pdf
from tests.golden.conftest import Scoreboard, docx_unit_pages, pdf_unit_pages, score_document

pytestmark = [pytest.mark.golden, pytest.mark.unit]

CORPUS_TARGET = 0.95
"""Section 13 of the specification: 95% field accuracy on text-layer documents."""

DOCUMENT_FLOOR = 0.90
"""No single document may read materially worse than the corpus target."""

TYPES = {
    "birth_lahore": CertificateType.BIRTH,
    "death_karachi": CertificateType.DEATH,
    "marriage_islamabad": CertificateType.MARRIAGE,
}


@pytest.fixture
def text_layer_corpus(board: Scoreboard, tmp_path: Path) -> Scoreboard:
    """Every shape of document that arrives with its text already in it."""
    for key, certificate_type in TYPES.items():
        sample = SAMPLES_BY_KEY[key]
        score_document(
            board,
            f"{key} (text pdf)",
            pdf_unit_pages(build_text_pdf(tmp_path / f"{key}.pdf", key)),
            certificate_type=certificate_type,
            expected=sample.expected,
        )

    score_document(
        board,
        "birth_lahore (word table)",
        docx_unit_pages(build_docx(tmp_path / "birth_table.docx", "birth_lahore", as_table=True)),
        certificate_type=CertificateType.BIRTH,
        expected=SAMPLES_BY_KEY["birth_lahore"].expected,
    )
    score_document(
        board,
        "birth_lahore (word paragraphs)",
        docx_unit_pages(
            build_docx(tmp_path / "birth_paragraphs.docx", "birth_lahore", as_table=False)
        ),
        certificate_type=CertificateType.BIRTH,
        expected=SAMPLES_BY_KEY["birth_lahore"].expected,
    )
    score_document(
        board,
        "death_karachi (ruled table pdf)",
        pdf_unit_pages(build_table_pdf(tmp_path / "death_table.pdf")),
        certificate_type=CertificateType.DEATH,
        expected=SAMPLES_BY_KEY["death_karachi"].expected,
    )

    if arabic_font() is not None:
        score_document(
            board,
            "urdu_birth (bilingual pdf)",
            pdf_unit_pages(build_bilingual_pdf(tmp_path / "urdu.pdf")),
            certificate_type=CertificateType.BIRTH,
            expected=URDU_BIRTH.expected,
        )
    return board


class TestTextLayerAccuracy:
    def test_the_corpus_meets_the_target(self, text_layer_corpus: Scoreboard) -> None:
        report = text_layer_corpus.report("Text-layer field accuracy")
        print(report)
        assert text_layer_corpus.accuracy >= CORPUS_TARGET, report

    def test_no_document_falls_apart(self, text_layer_corpus: Scoreboard) -> None:
        # An average hides a document that read almost nothing.
        poor = {
            name: text_layer_corpus.accuracy_for(name)
            for name in text_layer_corpus.documents
            if text_layer_corpus.accuracy_for(name) < DOCUMENT_FLOOR
        }
        assert not poor, text_layer_corpus.report("Text-layer field accuracy")

    def test_every_document_in_the_corpus_was_scored(self, text_layer_corpus: Scoreboard) -> None:
        expected_documents = 6 if arabic_font() is None else 7
        assert len(text_layer_corpus.documents) == expected_documents

    @pytest.mark.parametrize("key", list(TYPES))
    def test_the_fields_that_identify_a_person_are_read(
        self, board: Scoreboard, tmp_path: Path, key: str
    ) -> None:
        # Whatever the corpus average, a row with no name and no number is not a record.
        sample = SAMPLES_BY_KEY[key]
        score_document(
            board,
            key,
            pdf_unit_pages(build_text_pdf(tmp_path / f"{key}.pdf", key)),
            certificate_type=TYPES[key],
            expected=sample.expected,
        )
        identifying = {
            "certificate_number",
            "child_full_name",
            "deceased_full_name",
            "groom_full_name",
            "bride_full_name",
        }
        misses = [item for item in board.misses if item.field in identifying]
        assert not misses, board.report(key)


@pytest.mark.skipif(
    arabic_font() is None, reason="no Arabic-script font is installed on this machine"
)
class TestUrduAccuracy:
    def test_an_urdu_certificate_reads_as_well_as_an_english_one(
        self, board: Scoreboard, tmp_path: Path
    ) -> None:
        score_document(
            board,
            "urdu_birth",
            pdf_unit_pages(build_bilingual_pdf(tmp_path / "urdu.pdf")),
            certificate_type=CertificateType.BIRTH,
            expected=URDU_BIRTH.expected,
        )
        report = board.report("Urdu field accuracy")
        print(report)
        assert board.accuracy >= CORPUS_TARGET, report

    def test_urdu_values_are_stored_as_a_reviewer_would_type_them(
        self, board: Scoreboard, tmp_path: Path
    ) -> None:
        # Canonical Urdu letters, so a correction typed by a person compares equal.
        score_document(
            board,
            "urdu_birth",
            pdf_unit_pages(build_bilingual_pdf(tmp_path / "urdu.pdf")),
            certificate_type=CertificateType.BIRTH,
            expected={"child_full_name": URDU_BIRTH.expected["child_full_name"]},
        )
        assert not board.misses, board.report("Urdu names")
