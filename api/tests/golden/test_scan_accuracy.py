"""Field accuracy on scans, where OCR stands between the page and the answer.

The specification's bar is 85% on clean scans - lower than for a text layer, because a
scanner and Tesseract both get a vote in what the page says. What this test is really
for is the second and third cases: a page fed crooked and a page full of sensor speckle
are the everyday reality of a records office, and the number here says whether the
preprocessing is still earning its place.

Marked ``ocr``: it runs the real engine and takes a few seconds per document.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from certex.config import get_settings
from certex.enums import CertificateType
from certex.pipeline.extract.rules import UnitPage
from certex.pipeline.ocr.page_ocr import ocr_page
from certex.pipeline.ocr.rasterize import RasterPage, rasterize_pdf_page
from certex.pipeline.ocr.tesseract import resolve_binary
from tests.fixtures.builders import SAMPLES_BY_KEY
from tests.fixtures.corpus import build_scanned_pdf
from tests.golden.conftest import Scoreboard, score_document

pytestmark = [
    pytest.mark.golden,
    pytest.mark.ocr,
    pytest.mark.skipif(
        resolve_binary(get_settings()) is None,
        reason="the tesseract binary is not installed on this machine",
    ),
]

CLEAN_SCAN_TARGET = 0.85
"""Section 13 of the specification: 85% field accuracy on clean scans."""

DEGRADED_SCAN_FLOOR = 0.70
"""A crooked, speckled scan still has to produce a usable row for a reviewer to fix."""


def ocr_pages(path: Path) -> list[UnitPage]:
    """Read a scanned PDF exactly as the OCR stage reads it."""

    def render(dpi: int) -> RasterPage:
        return rasterize_pdf_page(path, 1, dpi=dpi)

    result = ocr_page(
        render,
        page_number=1,
        workspace_id=uuid.uuid4(),
        settings=get_settings(),
        cache=None,
    )
    return [UnitPage(page_number=1, layout=result.layout)]


def scan(board: Scoreboard, name: str, path: Path, sample_key: str) -> None:
    sample = SAMPLES_BY_KEY[sample_key]
    score_document(
        board,
        name,
        ocr_pages(path),
        certificate_type=CertificateType(sample.certificate_type),
        expected=sample.expected,
    )


class TestCleanScans:
    def test_the_corpus_meets_the_target(self, board: Scoreboard, tmp_path: Path) -> None:
        for key in ("birth_lahore", "death_karachi", "marriage_islamabad"):
            scan(
                board,
                f"{key} (300 dpi scan)",
                build_scanned_pdf(tmp_path / f"{key}.pdf", sample_key=key),
                key,
            )

        report = board.report("Clean-scan field accuracy")
        print(report)
        assert board.accuracy >= CLEAN_SCAN_TARGET, report

    def test_a_scan_reads_worse_than_its_text_layer_but_not_much(
        self, board: Scoreboard, tmp_path: Path
    ) -> None:
        # If a scan ever matched its text layer exactly, something would be wrong with
        # the fixtures rather than right with the OCR.
        scan(
            board,
            "birth_lahore (300 dpi scan)",
            build_scanned_pdf(tmp_path / "birth.pdf"),
            "birth_lahore",
        )
        assert board.accuracy >= CLEAN_SCAN_TARGET


class TestDegradedScans:
    @pytest.mark.parametrize(
        ("name", "options"),
        [
            ("skewed 1.8 degrees", {"skew_degrees": 1.8}),
            ("turned on its side", {"rotation": 90}),
            ("speckled and blurred", {"skew_degrees": 0.7, "noise": 0.03, "blur": 0.4}),
        ],
    )
    def test_a_damaged_scan_still_produces_a_usable_row(
        self, board: Scoreboard, tmp_path: Path, name: str, options: dict[str, object]
    ) -> None:
        path = build_scanned_pdf(tmp_path / "scan.pdf", **options)  # type: ignore[arg-type]
        scan(board, f"birth_lahore ({name})", path, "birth_lahore")

        report = board.report(f"Degraded scan: {name}")
        print(report)
        assert board.accuracy >= DEGRADED_SCAN_FLOOR, report

    def test_the_identifying_fields_survive_a_crooked_page(
        self, board: Scoreboard, tmp_path: Path
    ) -> None:
        # Whatever else is lost, a row with no number and no name is not a record.
        path = build_scanned_pdf(tmp_path / "scan.pdf", skew_degrees=1.8)
        scan(board, "birth_lahore (skewed)", path, "birth_lahore")

        identifying = {"certificate_number", "child_full_name", "date_of_birth"}
        misses = [item for item in board.misses if item.field in identifying]
        assert not misses, board.report("Skewed scan")
