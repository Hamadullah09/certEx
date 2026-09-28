"""Deciding what kind of certificate a unit is.

Everything downstream branches on this, and a wrong answer shows up as a row of empty
columns rather than as an error - so the tests are as much about when the classifier
should abstain as about when it should decide. Real certificate text is used where the
point is accuracy, and hand-written text where the point is a specific trap.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from certex.enums import CertificateType, ClassificationMethod
from certex.pipeline.classify.classifier import classify_text
from certex.pipeline.text.docx_reader import read_docx_pages
from certex.pipeline.text.pdf_native import iter_pdf_layouts
from tests.fixtures.builders import build_docx, build_text_pdf
from tests.fixtures.corpus import arabic_font, build_bilingual_pdf

pytestmark = pytest.mark.unit

needs_arabic_font = pytest.mark.skipif(
    arabic_font() is None, reason="no Arabic-script font is installed on this machine"
)

DEATH_MENTIONING_BIRTH = """DEATH CERTIFICATE
GOVERNMENT OF SINDH
Name of Deceased: Abdul Rehman Qureshi
Date of Birth: 1948-06-30
Date of Death: 2021-11-17
Cause of Death: Cardiac arrest
Father's Name: Ghulam Qureshi"""

MARRIAGE_MENTIONING_BIRTHS = """CERTIFICATE OF MARRIAGE (NIKAH NAMA)
Date of Marriage: 2015-09-12
Groom's Name: Hamza Bin Yousaf
Groom's Date of Birth: 1988-01-22
Bride's Name: Sana Fatima
Bride's Date of Birth: 1991-07-08"""

COVERING_NOTE = "Please attach a copy of the birth certificate and the identity card."

FIELDS_WITHOUT_A_TITLE = """Date of Birth: 1987-03-14
Father's Name: Tariq Mahmood Malik
Mother's Name: Nasreen Akhtar"""


class TestRealCertificates:
    @pytest.mark.parametrize(
        ("sample_key", "expected"),
        [
            ("birth_lahore", CertificateType.BIRTH),
            ("death_karachi", CertificateType.DEATH),
            ("marriage_islamabad", CertificateType.MARRIAGE),
        ],
    )
    def test_a_text_pdf_is_classified(
        self, tmp_path: Path, sample_key: str, expected: CertificateType
    ) -> None:
        layout = next(iter_pdf_layouts(build_text_pdf(tmp_path / f"{sample_key}.pdf", sample_key)))

        result = classify_text(layout.text)

        assert result.certificate_type is expected
        assert result.method is ClassificationMethod.KEYWORD
        assert result.confidence > 0.8

    def test_a_word_certificate_is_classified(self, tmp_path: Path) -> None:
        page = read_docx_pages(build_docx(tmp_path / "birth.docx"))[0]
        result = classify_text(page.text)
        assert result.certificate_type is CertificateType.BIRTH
        assert result.confidence > 0.8

    @needs_arabic_font
    def test_an_urdu_certificate_is_classified_from_its_urdu(self, tmp_path: Path) -> None:
        layout = next(iter_pdf_layouts(build_bilingual_pdf(tmp_path / "urdu.pdf")))

        result = classify_text(layout.text)

        assert result.certificate_type is CertificateType.BIRTH
        assert result.confidence > 0.8
        assert any("سرٹیفکیٹ" in phrase for phrase in result.matched)


class TestTraps:
    def test_a_death_certificate_is_not_a_birth_certificate(self) -> None:
        # Every death certificate carries a date of birth.
        result = classify_text(DEATH_MENTIONING_BIRTH)
        assert result.certificate_type is CertificateType.DEATH
        assert result.scores[CertificateType.DEATH] > result.scores[CertificateType.BIRTH]

    def test_a_marriage_certificate_carries_two_birth_dates_and_stays_a_marriage(self) -> None:
        result = classify_text(MARRIAGE_MENTIONING_BIRTHS)
        assert result.certificate_type is CertificateType.MARRIAGE

    def test_a_letter_mentioning_a_certificate_is_not_one(self) -> None:
        result = classify_text(COVERING_NOTE)
        assert result.confidence < 0.6, "a passing mention must not read as a certificate"

    def test_a_title_alone_is_not_confidence(self) -> None:
        # Enough to guess the type, not enough to be sure of it.
        result = classify_text("CERTIFICATE OF BIRTH")
        assert result.certificate_type is CertificateType.BIRTH
        assert result.confidence < 0.6

    def test_fields_without_a_title_are_weak_evidence(self) -> None:
        result = classify_text(FIELDS_WITHOUT_A_TITLE)
        assert result.confidence < 0.6


class TestAbstaining:
    def test_an_empty_unit_is_not_guessed_at(self) -> None:
        result = classify_text("")
        assert result.certificate_type is CertificateType.OTHER
        assert result.confidence == 0.0
        assert result.method is ClassificationMethod.DEFAULT

    def test_a_page_of_something_else_entirely(self) -> None:
        result = classify_text("INVOICE\nBill To: Acme Traders\nAmount Due: PKR 12,000")
        assert result.certificate_type is CertificateType.OTHER
        assert result.method is ClassificationMethod.DEFAULT

    def test_a_tie_is_not_broken_by_a_coin_flip(self) -> None:
        # Both titles present, neither clearly the document's own.
        tied = "This form serves as a birth certificate and a death certificate."
        result = classify_text(tied)
        assert result.certificate_type is CertificateType.OTHER
        assert result.method is ClassificationMethod.DEFAULT


class TestOcrDamage:
    def test_a_mangled_heading_still_classifies(self) -> None:
        # "BIRTII" is what Tesseract makes of "BIRTH" on a poor scan.
        mangled = """CERTIFICATE OF BIRTII
Name of Child: Ayesha Noor Malik
Place of Birth: Services Hospital
Time of Birth: 04:25"""
        result = classify_text(mangled)
        assert result.certificate_type is CertificateType.BIRTH
        assert any(phrase.startswith("~") for phrase in result.matched), "matched fuzzily"

    def test_a_heading_too_mangled_to_recognise_falls_back_to_the_fields(self) -> None:
        unreadable = """C#RT!F!C@T# 0F 8!RT#
Name of Child: Ayesha Noor Malik
Place of Birth: Services Hospital
Time of Birth: 04:25"""
        result = classify_text(unreadable)
        assert result.certificate_type is CertificateType.BIRTH
        assert result.confidence < 0.8, "without its title, the page is less certain"


class TestEvidence:
    def test_the_phrases_behind_the_decision_are_reported(self) -> None:
        result = classify_text(DEATH_MENTIONING_BIRTH)
        assert "death certificate" in result.matched
        assert "cause of death" in result.matched

    def test_every_type_is_scored(self) -> None:
        result = classify_text(DEATH_MENTIONING_BIRTH)
        assert set(result.scores) == {
            CertificateType.BIRTH,
            CertificateType.DEATH,
            CertificateType.MARRIAGE,
        }

    def test_a_repeated_phrase_does_not_count_twice(self) -> None:
        once = classify_text("DEATH CERTIFICATE\nName of Deceased: X\nCause of Death: Y")
        twice = classify_text(
            "DEATH CERTIFICATE\nName of Deceased: X\nCause of Death: Y\nCause of Death: Y"
        )
        assert once.scores[CertificateType.DEATH] == twice.scores[CertificateType.DEATH]
