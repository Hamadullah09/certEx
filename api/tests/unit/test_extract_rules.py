"""Reading fields off a certificate by their printed labels.

Real certificates are used where the point is that a form reads correctly, and synthetic
pages where the point is one specific trap: a heading that contains a field's label, a
bilingual row labelled twice, two similar labels on one form. Each trap here is one that
produced a wrong value before it was fixed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from certex.enums import CertificateType
from certex.pipeline.extract.extractor import extract_fields
from certex.pipeline.extract.rules import UnitPage, extract_extra_fields, extract_with_rules
from certex.pipeline.text.docx_reader import read_docx_pages
from certex.pipeline.text.layout_builder import PositionedWord, build_layout
from certex.pipeline.text.pdf_native import iter_pdf_layouts
from certex.schemas.layout import PageLayout, Table, TableCell
from tests.fixtures.builders import SAMPLES_BY_KEY, build_docx, build_text_pdf
from tests.fixtures.corpus import URDU_BIRTH, arabic_font, build_bilingual_pdf, build_table_pdf

pytestmark = pytest.mark.unit

needs_arabic_font = pytest.mark.skipif(
    arabic_font() is None, reason="no Arabic-script font is installed on this machine"
)

WIDTH = 600.0
HEIGHT = 800.0
SIZE = 12.0
CHAR = 6.0


def page_of(lines: list[str], *, tables: list[Table] | None = None) -> UnitPage:
    """A synthetic page: one line per string, laid out like a printed form.

    The words are handed over in the order they are written, the way MuPDF and Tesseract
    report a line they have already put into reading order - so an Urdu line here reads
    as written rather than being re-derived from geometry, which the layout tests cover.
    """
    words: list[PositionedWord] = []
    for row, line in enumerate(lines):
        cursor = 50.0
        for order, token in enumerate(line.split(" ")):
            if not token:
                cursor += CHAR
                continue
            width = len(token) * CHAR
            words.append(
                PositionedWord(
                    text=token,
                    x0=cursor,
                    y0=60.0 + row * 30.0,
                    x1=cursor + width,
                    y1=60.0 + row * 30.0 + SIZE,
                    line_key=(row,),
                    order=order,
                )
            )
            cursor += width + CHAR
    layout = build_layout(
        words,
        page_number=1,
        width=WIDTH,
        height=HEIGHT,
        unit="pt",
        engine="pymupdf",
        tables=tables,
        engine_order=True,
    )
    return UnitPage(page_number=1, layout=layout)


def pdf_pages(path: Path) -> list[UnitPage]:
    return [
        UnitPage(page_number=layout.page_number, layout=layout) for layout in iter_pdf_layouts(path)
    ]


def docx_pages(path: Path) -> list[UnitPage]:
    return [UnitPage(page_number=page.page_number, layout=page) for page in read_docx_pages(path)]


def values(pages: list[UnitPage], certificate_type: CertificateType) -> dict[str, str]:
    found = extract_with_rules(pages, certificate_type=certificate_type)
    return {name: candidate.value for name, candidate in found.items()}


def stored(pages: list[UnitPage], certificate_type: CertificateType) -> dict[str, str]:
    """What would be written to the row: read, then put in canonical form."""
    extracted = extract_fields(pages, certificate_type=certificate_type)
    return {name: candidate.value for name, candidate in extracted.fields.items()}


class TestRealCertificates:
    @pytest.mark.parametrize(
        ("sample_key", "certificate_type"),
        [
            ("birth_lahore", CertificateType.BIRTH),
            ("death_karachi", CertificateType.DEATH),
            ("marriage_islamabad", CertificateType.MARRIAGE),
        ],
    )
    def test_every_labelled_value_is_read(
        self, tmp_path: Path, sample_key: str, certificate_type: CertificateType
    ) -> None:
        pages = pdf_pages(build_text_pdf(tmp_path / f"{sample_key}.pdf", sample_key))
        found = stored(pages, certificate_type)

        expected = SAMPLES_BY_KEY[sample_key].expected
        missing = {name: want for name, want in expected.items() if found.get(name) != want}
        assert not missing, missing

    def test_a_word_certificate_reads_the_same(self, tmp_path: Path) -> None:
        pages = docx_pages(build_docx(tmp_path / "birth.docx", as_table=True))
        found = values(pages, CertificateType.BIRTH)
        assert found["child_full_name"] == "Ayesha Noor Malik"
        assert found["father_id_number"] == "35201-1234567-1"

    def test_a_ruled_table_reads_from_its_cells(self, tmp_path: Path) -> None:
        pages = pdf_pages(build_table_pdf(tmp_path / "table.pdf"))
        found = values(pages, CertificateType.DEATH)
        assert found["deceased_full_name"] == "Abdul Rehman Qureshi"
        assert found["cause_of_death"] == "Cardiac arrest"


class TestWhereTheValueSits:
    def test_beside_the_label(self) -> None:
        page = page_of(["Name of Child: Ayesha Noor Malik"])
        assert values([page], CertificateType.BIRTH)["child_full_name"] == "Ayesha Noor Malik"

    def test_under_the_label(self) -> None:
        # Some forms print the value beneath its label rather than beside it.
        page = page_of(["Name of Child", "Ayesha Noor Malik"])
        assert values([page], CertificateType.BIRTH)["child_full_name"] == "Ayesha Noor Malik"

    def test_the_line_below_is_not_taken_when_it_is_another_label(self) -> None:
        page = page_of(["Name of Child", "Date of Birth: 1987-03-14"])
        assert "child_full_name" not in values([page], CertificateType.BIRTH)

    def test_in_the_cell_beside_the_label(self) -> None:
        table = Table(
            rows=[
                [TableCell(text="Name of Child"), TableCell(text="Ayesha Noor Malik")],
                [TableCell(text="Sex"), TableCell(text="Female")],
            ]
        )
        page = page_of(["CERTIFICATE OF BIRTH"], tables=[table])
        found = values([page], CertificateType.BIRTH)
        assert found["child_full_name"] == "Ayesha Noor Malik"

    def test_the_best_matching_row_of_a_table_wins(self) -> None:
        # "Date of Birth" and "Time of Birth" are similar enough for a damaged-label
        # match; the row that matches exactly has to win.
        table = Table(
            rows=[
                [TableCell(text="Date of Birth"), TableCell(text="1987-03-14")],
                [TableCell(text="Time of Birth"), TableCell(text="04:25")],
            ]
        )
        page = page_of(["CERTIFICATE OF BIRTH"], tables=[table])
        found = values([page], CertificateType.BIRTH)
        assert found["time_of_birth"] == "04:25"
        assert found["date_of_birth"] == "1987-03-14"


class TestTraps:
    def test_a_heading_containing_a_label_does_not_become_a_value(self) -> None:
        # "GOVERNMENT OF PAKISTAN - LOCAL GOVERNMENT DEPARTMENT" contains "local
        # government"; taking what follows would file "DEPARTMENT" as the issuer.
        page = page_of(
            [
                "GOVERNMENT OF PAKISTAN - LOCAL GOVERNMENT DEPARTMENT",
                "Issuing Authority: Union Council 42, Lahore",
            ]
        )
        assert values([page], CertificateType.BIRTH)["issuing_authority"] == (
            "Union Council 42, Lahore"
        )

    def test_a_synonym_inside_a_value_is_not_mistaken_for_a_second_label(self) -> None:
        # "Union Council" is also a way of naming the issuer; it must not truncate the
        # value to "42, Lahore".
        page = page_of(["Issuing Authority: Union Council 42, Lahore"])
        assert values([page], CertificateType.BIRTH)["issuing_authority"] == (
            "Union Council 42, Lahore"
        )

    def test_the_most_specific_label_wins(self) -> None:
        page = page_of(
            [
                "Groom's Date of Birth: 1988-01-22",
                "Bride's Date of Birth: 1991-07-08",
            ]
        )
        found = values([page], CertificateType.MARRIAGE)
        assert found["groom_date_of_birth"] == "1988-01-22"
        assert found["bride_date_of_birth"] == "1991-07-08"

    def test_a_damaged_label_is_still_found(self) -> None:
        # A letter misread as a digit is the everyday damage on a scanned form.
        page = page_of(["Name 0f Child: Ayesha Noor Malik"])
        assert values([page], CertificateType.BIRTH)["child_full_name"] == "Ayesha Noor Malik"

    def test_a_generic_label_only_counts_at_the_start_of_a_line(self) -> None:
        # "Name" alone is how some forms label the subject, but "Father's Name" contains
        # it too, and reading after it would file the father's name as the child's.
        page = page_of(["Father's Name: Tariq Mahmood Malik"])
        found = values([page], CertificateType.BIRTH)
        assert found.get("father_full_name") == "Tariq Mahmood Malik"
        assert "child_full_name" not in found

    def test_a_form_that_labels_the_subject_as_name_still_reads(self) -> None:
        page = page_of(["Name: Ayesha Noor Malik", "Father's Name: Tariq Mahmood Malik"])
        found = values([page], CertificateType.BIRTH)
        assert found["child_full_name"] == "Ayesha Noor Malik"
        assert found["father_full_name"] == "Tariq Mahmood Malik"

    def test_a_label_is_never_confused_with_a_different_field(self) -> None:
        # "Mother's Name" and "Father's Name" are two characters apart, which is also how
        # far a damaged label sits from its true one. Reading one as the other would put
        # the father's name in the mother's column, and nothing downstream could tell.
        page = page_of(["Father's Name: Tariq Mahmood Malik"])
        found = values([page], CertificateType.BIRTH)
        assert found.get("father_full_name") == "Tariq Mahmood Malik"
        assert "mother_full_name" not in found

    def test_a_birth_date_is_not_read_as_a_time(self) -> None:
        page = page_of(["Date of Birth: 1987-03-14"])
        assert "time_of_birth" not in values([page], CertificateType.BIRTH)

    def test_a_label_with_no_value_yields_nothing(self) -> None:
        page = page_of(["Mother's Name:", "Registrar: Muhammad Aslam"])
        assert "mother_full_name" not in values([page], CertificateType.BIRTH)


class TestBilingualForms:
    def test_the_value_follows_the_second_label(self) -> None:
        page = page_of(["Sex / جنس لڑکی"])
        assert values([page], CertificateType.BIRTH)["sex"] == "لڑکی"

    def test_an_urdu_label_alone_is_matched(self) -> None:
        page = page_of(["بچے کا نام عائشہ نور ملک"])
        assert values([page], CertificateType.BIRTH)["child_full_name"] == "عائشہ نور ملک"

    @needs_arabic_font
    def test_a_real_bilingual_certificate_reads(self, tmp_path: Path) -> None:
        pages = pdf_pages(build_bilingual_pdf(tmp_path / "urdu.pdf"))
        found = values(pages, CertificateType.BIRTH)

        for name, want in URDU_BIRTH.expected.items():
            if name in ("sex", "date_of_birth"):
                continue  # normalised forms, covered by the value tests
            assert found.get(name) == want, f"{name}: {found.get(name)!r}"


class TestConfidence:
    def test_a_clean_label_and_value_scores_high(self) -> None:
        page = page_of(["Name of Child: Ayesha Noor Malik"])
        found = extract_with_rules([page], certificate_type=CertificateType.BIRTH)
        assert found["child_full_name"].confidence > 0.9

    def test_a_value_under_its_label_scores_lower_than_one_beside_it(self) -> None:
        beside = page_of(["Name of Child: Ayesha Noor Malik"])
        below = page_of(["Name of Child", "Ayesha Noor Malik"])
        first = extract_with_rules([beside], certificate_type=CertificateType.BIRTH)
        second = extract_with_rules([below], certificate_type=CertificateType.BIRTH)
        assert second["child_full_name"].confidence < first["child_full_name"].confidence

    def test_ocr_confidence_is_carried_into_the_field(self) -> None:
        # The same page read by OCR with middling confidence is a less certain value.
        words = [
            PositionedWord(
                text=token,
                x0=50.0 + index * 60,
                y0=60.0,
                x1=50.0 + index * 60 + 50,
                y1=72.0,
                confidence=50.0,
                line_key=(0, 0, 0),
                order=index,
            )
            for index, token in enumerate(["Sex:", "Female"])
        ]
        layout = build_layout(
            words,
            page_number=1,
            width=WIDTH,
            height=HEIGHT,
            unit="px",
            engine="tesseract",
            engine_order=True,
        )
        found = extract_with_rules(
            [UnitPage(page_number=1, layout=layout)], certificate_type=CertificateType.BIRTH
        )
        assert found["sex"].value == "Female"
        assert found["sex"].confidence < 0.85


class TestProvenance:
    def test_a_value_says_where_it_came_from(self) -> None:
        page = page_of(["Name of Child: Ayesha Noor Malik"])
        candidate = extract_with_rules([page], certificate_type=CertificateType.BIRTH)[
            "child_full_name"
        ]
        assert candidate.source.page_number == 1
        assert "Ayesha" in candidate.source.snippet
        assert candidate.source.bbox is not None
        assert candidate.source.label is not None

    def test_the_box_covers_the_value_not_the_label(self) -> None:
        page = page_of(["Name of Child: Ayesha Noor Malik"])
        candidate = extract_with_rules([page], certificate_type=CertificateType.BIRTH)[
            "child_full_name"
        ]
        assert candidate.source.bbox is not None
        assert candidate.source.bbox.x0 > 50.0 / WIDTH


class TestExtraFields:
    def test_a_label_the_schema_does_not_know_is_kept(self) -> None:
        page = page_of(["Blood Group: O+", "Name of Child: Ayesha Noor Malik"])
        extras = extract_extra_fields([page], certificate_type=CertificateType.BIRTH)
        assert extras["blood_group"].value == "O+"

    def test_a_known_field_is_not_repeated_as_an_extra(self) -> None:
        page = page_of(["Name of Child: Ayesha Noor Malik"])
        extras = extract_extra_fields([page], certificate_type=CertificateType.BIRTH)
        assert "name_of_child" not in extras

    def test_a_line_that_is_not_a_field_is_ignored(self) -> None:
        page = page_of(["This is a computer generated certificate."])
        assert extract_extra_fields([page], certificate_type=CertificateType.BIRTH) == {}


class TestEmptyInput:
    def test_a_page_with_no_text(self) -> None:
        empty = PageLayout(page_number=1, unit="px", engine="none")
        pages = [UnitPage(page_number=1, layout=empty)]
        assert extract_with_rules(pages, certificate_type=CertificateType.BIRTH) == {}
        assert extract_extra_fields(pages, certificate_type=CertificateType.BIRTH) == {}

    def test_no_pages_at_all(self) -> None:
        assert extract_with_rules([], certificate_type=CertificateType.BIRTH) == {}
