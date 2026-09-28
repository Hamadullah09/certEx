"""Deciding where one certificate ends and the next begins.

The asymmetry drives every case here: splitting one certificate in two leaves two
half-filled rows a reviewer can see and fix, while running two certificates together
produces one plausible-looking row with values from two different people. So the tests
check not only that the cascade finds real boundaries, but that it declines to invent
them from weak evidence.
"""

from __future__ import annotations

import pytest

from certex.enums import BoundaryMethod
from certex.pipeline.split.boundaries import (
    PageSummary,
    certificate_numbers,
    detect_boundaries,
    has_heading,
)

pytestmark = pytest.mark.unit

BIRTH_HEADING = "CERTIFICATE OF BIRTH\nGOVERNMENT OF PAKISTAN"
DEATH_HEADING = "DEATH CERTIFICATE\nGOVERNMENT OF SINDH"
URDU_HEADING = "پیدائش کا سرٹیفکیٹ\nحکومت پنجاب"
BODY = "Name of Child: Ayesha Noor Malik\nPlace of Birth: Services Hospital, Lahore"


def page(number: int, text: str) -> PageSummary:
    return PageSummary(page_number=number, text=text)


def certificate_page(number: int, *, heading: str = BIRTH_HEADING, serial: str = "") -> PageSummary:
    serial_line = f"Certificate No.: {serial}\n" if serial else ""
    return page(number, f"{heading}\n{serial_line}{BODY}")


class TestHeadingDetection:
    @pytest.mark.parametrize(
        "text",
        [
            "CERTIFICATE OF BIRTH",
            "Death Certificate",
            "certificate of registration of marriage",
            "NIKAH NAMA",
            "پیدائش کا سرٹیفکیٹ",
        ],
    )
    def test_a_certificate_names_itself(self, text: str) -> None:
        assert has_heading(text)

    @pytest.mark.parametrize(
        "text",
        ["Page 2 of 4", "Union Council 42, Lahore", "Name of Child: Ayesha", ""],
    )
    def test_ordinary_lines_are_not_headings(self, text: str) -> None:
        assert not has_heading(text)


class TestCertificateNumbers:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("Certificate No.: BC-2019-004471", "BC-2019-004471"),
            ("Registration No REG/LHR/2019/88213", "REG/LHR/2019/88213"),
            ("Serial #: 000412", "000412"),
            ("سرٹیفکیٹ نمبر BC-2020-001122", "BC-2020-001122"),
        ],
    )
    def test_a_number_is_found(self, text: str, expected: str) -> None:
        assert certificate_numbers(text)[0] == expected

    def test_a_page_without_one_reports_nothing(self) -> None:
        assert certificate_numbers("Name of Child: Ayesha Noor Malik") == []


class TestOneCertificate:
    def test_a_single_page_is_one_certificate(self) -> None:
        result = detect_boundaries([certificate_page(1)])
        assert result.ranges == [(1, 1)]
        assert result.method is BoundaryMethod.SINGLE_DOCUMENT
        assert result.confidence == 1.0

    def test_a_two_page_certificate_is_not_split_by_its_second_page(self) -> None:
        # The back of a certificate has no heading of its own, and must not become a
        # row with nothing in it.
        pages = [certificate_page(1), page(2, "Registrar: Muhammad Aslam\nDate of Issue: 2019")]
        result = detect_boundaries(pages)
        assert result.ranges == [(1, 2)]
        assert result.method is BoundaryMethod.SINGLE_DOCUMENT

    def test_an_uncertain_single_unit_says_so(self) -> None:
        # Multi-page with no signal: one certificate is the answer, but not a confident
        # one, and the confidence is what sends it to review.
        result = detect_boundaries([page(1, BODY), page(2, BODY), page(3, BODY)])
        assert result.ranges == [(1, 3)]
        assert result.confidence < 0.8

    def test_no_pages_at_all(self) -> None:
        assert detect_boundaries([]).ranges == [(1, 1)]


class TestBookmarks:
    def test_an_outline_decides_the_boundaries(self) -> None:
        pages = [certificate_page(number) for number in range(1, 7)]
        result = detect_boundaries(pages, bookmark_pages=[1, 3, 5])

        assert result.ranges == [(1, 2), (3, 4), (5, 6)]
        assert result.method is BoundaryMethod.BOOKMARK
        assert result.confidence > 0.9

    def test_an_outline_that_is_a_table_of_contents_is_ignored(self) -> None:
        # An outline that starts after page one is an index, not a list of records.
        pages = [page(number, BODY) for number in range(1, 5)]
        result = detect_boundaries(pages, bookmark_pages=[2, 3])
        assert result.method is BoundaryMethod.SINGLE_DOCUMENT

    def test_an_outline_with_one_entry_is_not_a_split(self) -> None:
        pages = [page(number, BODY) for number in range(1, 4)]
        assert detect_boundaries(pages, bookmark_pages=[1]).ranges == [(1, 3)]


class TestHeadings:
    def test_a_heading_on_every_page_gives_one_certificate_per_page(self) -> None:
        pages = [certificate_page(number) for number in range(1, 5)]
        result = detect_boundaries(pages)

        assert result.ranges == [(1, 1), (2, 2), (3, 3), (4, 4)]
        assert result.method is BoundaryMethod.UNIFORM_PAGE_COUNT
        assert result.confidence > 0.9

    def test_two_page_certificates_are_found_by_their_headings(self) -> None:
        pages = [
            certificate_page(1),
            page(2, "Registrar: Muhammad Aslam"),
            certificate_page(3),
            page(4, "Registrar: Muhammad Aslam"),
        ]
        result = detect_boundaries(pages)
        assert result.ranges == [(1, 2), (3, 4)]

    def test_uneven_certificates_are_content_headers_not_a_stride(self) -> None:
        pages = [
            certificate_page(1),
            page(2, "continued"),
            certificate_page(3),
            page(4, "continued"),
            page(5, "continued"),
        ]
        result = detect_boundaries(pages)
        assert result.ranges == [(1, 2), (3, 5)]
        assert result.method is BoundaryMethod.CONTENT_HEADER

    def test_certificates_of_different_kinds_are_still_separated(self) -> None:
        pages = [
            certificate_page(1, heading=BIRTH_HEADING),
            certificate_page(2, heading=DEATH_HEADING),
            certificate_page(3, heading=URDU_HEADING),
        ]
        assert detect_boundaries(pages).ranges == [(1, 1), (2, 2), (3, 3)]

    def test_a_heading_that_starts_late_is_not_trusted(self) -> None:
        # Page one has no heading, so whatever the pattern is matching, it is not the
        # start of each certificate.
        pages = [page(1, BODY), certificate_page(2), certificate_page(3)]
        assert detect_boundaries(pages).method is BoundaryMethod.SINGLE_DOCUMENT


class TestSerialNumbers:
    def test_a_new_number_starts_a_certificate(self) -> None:
        pages = [
            certificate_page(1, heading="REGISTER OF BIRTHS", serial="BC-1"),
            certificate_page(2, heading="REGISTER OF BIRTHS", serial="BC-1"),
            certificate_page(3, heading="REGISTER OF BIRTHS", serial="BC-2"),
            certificate_page(4, heading="REGISTER OF BIRTHS", serial="BC-3"),
        ]
        result = detect_boundaries(pages)

        assert result.ranges == [(1, 2), (3, 3), (4, 4)]
        assert result.method is BoundaryMethod.SERIAL_NUMBER

    def test_numbers_are_ignored_when_a_page_has_none(self) -> None:
        # A signal that does not cover the file cannot be trusted to split it.
        pages = [
            certificate_page(1, heading="REGISTER", serial="BC-1"),
            page(2, BODY),
            certificate_page(3, heading="REGISTER", serial="BC-2"),
        ]
        assert detect_boundaries(pages).method is BoundaryMethod.SINGLE_DOCUMENT

    def test_one_number_throughout_is_one_certificate(self) -> None:
        pages = [
            certificate_page(number, heading="REGISTER", serial="BC-1") for number in (1, 2, 3)
        ]
        assert detect_boundaries(pages).ranges == [(1, 3)]


class TestStride:
    def test_a_rhythm_of_headings_is_applied_to_the_whole_file(self) -> None:
        # Six certificates of two pages each, but OCR lost two of the headings.
        pages: list[PageSummary] = []
        for index in range(6):
            first = index * 2 + 1
            heading_readable = index not in (2, 4)
            pages.append(certificate_page(first) if heading_readable else page(first, BODY))
            pages.append(page(first + 1, "Registrar: Muhammad Aslam"))

        result = detect_boundaries(pages)

        assert result.method is BoundaryMethod.FIXED_STRIDE
        assert result.ranges == [(1, 2), (3, 4), (5, 6), (7, 8), (9, 10), (11, 12)]
        assert 0.5 < result.confidence < 0.8, "an inferred rhythm is offered, not trusted"

    def test_a_stride_that_does_not_divide_the_file_is_rejected(self) -> None:
        pages = [
            certificate_page(1),
            page(2, BODY),
            certificate_page(3),
            page(4, BODY),
            page(5, BODY),
        ]
        result = detect_boundaries(pages)
        assert result.method is BoundaryMethod.CONTENT_HEADER  # headings cover it instead

    def test_scattered_headings_are_not_a_rhythm(self) -> None:
        pages = [page(number, BODY) for number in range(1, 10)]
        pages[2] = certificate_page(3)
        pages[3] = certificate_page(4)
        pages[7] = certificate_page(8)
        assert detect_boundaries(pages).method is BoundaryMethod.SINGLE_DOCUMENT
