"""Driving Tesseract, and the policy that reads a page well.

These tests run the real engine, because everything worth asserting - that a scan of a
certificate comes back as the right words, that orientation is detected, that the
language choice changes the answer - is a property of the engine rather than of the
code around it. They are marked ``ocr`` and skip where the binary is absent.

The one exception is failure handling, which is asserted against a binary that is not
there and against a deadline too short to meet.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from certex.config import Settings, get_settings
from certex.core.errors import OCRUnavailableError, ProcessingTimeoutError
from certex.pipeline.ocr.preprocess import preprocess, rotate_image
from certex.pipeline.ocr.rasterize import GrayImage, rasterize_pdf_page
from certex.pipeline.ocr.tesseract import (
    available_languages,
    configured_languages,
    detect_orientation,
    language_for_script,
    resolve_binary,
    run_tesseract,
    tesseract_version,
)
from tests.fixtures.builders import SAMPLES_BY_KEY
from tests.fixtures.corpus import build_scanned_pdf

pytestmark = pytest.mark.unit

needs_tesseract = pytest.mark.skipif(
    resolve_binary(get_settings()) is None,
    reason="the tesseract binary is not installed on this machine",
)

MISSING_BINARY = "certex-no-such-tesseract"


def settings_with(**overrides: object) -> Settings:
    return get_settings().model_copy(update=overrides)


@pytest.fixture(scope="module")
def scan_image(tmp_path_factory: pytest.TempPathFactory) -> GrayImage:
    """A clean scan of the birth certificate, prepared exactly as the stage prepares it."""
    directory = tmp_path_factory.mktemp("tesseract")
    raster = rasterize_pdf_page(build_scanned_pdf(directory / "scan.pdf"), 1, dpi=300)
    return preprocess(raster.image, dpi=300).image


class TestTheInstallation:
    def test_a_missing_binary_is_reported_as_missing(self) -> None:
        assert resolve_binary(settings_with(tesseract_cmd=MISSING_BINARY)) is None
        assert tesseract_version(settings_with(tesseract_cmd=MISSING_BINARY)) is None
        assert available_languages(settings_with(tesseract_cmd=MISSING_BINARY)) == frozenset()

    @needs_tesseract
    def test_the_engine_reports_its_version_and_languages(self) -> None:
        settings = get_settings()
        version = tesseract_version(settings)
        assert version is not None and "tesseract" in version.lower()
        assert "eng" in available_languages(settings)


class TestLanguageChoice:
    @needs_tesseract
    def test_an_english_page_is_read_as_english_alone(self) -> None:
        # Asking for Urdu as well costs accuracy on a noisy English page: whole values
        # come back as Urdu letters and digits.
        assert language_for_script("Latin", settings=get_settings()) == "eng"

    @needs_tesseract
    def test_an_urdu_page_keeps_english_as_a_second_language(self) -> None:
        # A Pakistani certificate in Urdu still has English labels on it.
        chosen = language_for_script("Arabic", settings=get_settings())
        assert chosen.startswith("urd")
        assert "eng" in chosen

    @needs_tesseract
    def test_an_undetected_script_asks_for_everything_configured(self) -> None:
        chosen = language_for_script(None, settings=get_settings())
        assert set(chosen.split("+")) == set(configured_languages(get_settings()).split("+"))

    @needs_tesseract
    def test_a_language_that_is_not_installed_is_not_requested(self) -> None:
        settings = settings_with(ocr_languages="eng+klingon")
        assert "klingon" not in configured_languages(settings)

    @needs_tesseract
    def test_nothing_installed_falls_back_rather_than_failing(self) -> None:
        settings = settings_with(ocr_languages="klingon")
        assert configured_languages(settings) == "eng"


@needs_tesseract
class TestReading:
    def test_a_clean_scan_comes_back_as_the_right_words(self, scan_image: GrayImage) -> None:
        outcome = run_tesseract(
            scan_image, language="eng", psm=6, settings=get_settings(), timeout=120
        )
        text = " ".join(word.text for word in outcome.words)
        assert "CERTIFICATE" in text
        assert "BC-2019-004471" in text
        assert "Ayesha" in text

    def test_words_carry_boxes_and_confidence(self, scan_image: GrayImage) -> None:
        outcome = run_tesseract(
            scan_image, language="eng", psm=6, settings=get_settings(), timeout=120
        )
        assert outcome.words
        for word in outcome.words:
            assert word.x1 > word.x0 and word.y1 > word.y0
            assert word.confidence is not None and 0.0 <= word.confidence <= 100.0
            assert word.line_key is not None, "runs are needed to rebuild reading order"
        assert outcome.mean_confidence is not None and outcome.mean_confidence > 70.0

    def test_a_clean_scan_reads_most_of_its_values(self, scan_image: GrayImage) -> None:
        outcome = run_tesseract(
            scan_image, language="eng", psm=6, settings=get_settings(), timeout=120
        )
        text = " ".join(word.text for word in outcome.words)
        expected = SAMPLES_BY_KEY["birth_lahore"].lines
        found = sum(1 for _label, value in expected if value in text)
        assert found >= len(expected) * 0.8, f"only {found}/{len(expected)} values read back"

    def test_the_segmentation_mode_is_honoured(self, scan_image: GrayImage) -> None:
        outcome = run_tesseract(
            scan_image, language="eng", psm=4, settings=get_settings(), timeout=120
        )
        assert outcome.psm == 4
        assert outcome.language == "eng"

    def test_a_blank_page_reads_as_nothing(self) -> None:
        blank: GrayImage = np.full((1000, 800), 255, dtype=np.uint8)
        outcome = run_tesseract(blank, language="eng", psm=6, settings=get_settings(), timeout=120)
        assert outcome.words == []
        assert outcome.mean_confidence is None


@needs_tesseract
class TestOrientation:
    def test_an_upright_page_needs_no_turning(self, tmp_path: Path) -> None:
        raster = rasterize_pdf_page(build_scanned_pdf(tmp_path / "scan.pdf"), 1, dpi=300)
        orientation = detect_orientation(raster.image, settings=get_settings(), timeout=120)
        assert orientation is not None
        assert orientation.quarter_turns == 0
        assert orientation.script == "Latin"

    def test_a_sideways_page_is_detected(self, tmp_path: Path) -> None:
        path = build_scanned_pdf(tmp_path / "sideways.pdf", rotation=90)
        raster = rasterize_pdf_page(path, 1, dpi=300)

        orientation = detect_orientation(raster.image, settings=get_settings(), timeout=120)

        assert orientation is not None
        assert orientation.quarter_turns != 0
        prepared = preprocess(
            raster.image, dpi=300, quarter_turns=orientation.quarter_turns, deskew=False
        )
        assert prepared.display.shape[0] > prepared.display.shape[1], "back to portrait"

    def test_a_page_with_no_text_is_not_guessed_at(self) -> None:
        blank: GrayImage = np.full((1200, 900), 255, dtype=np.uint8)
        orientation = detect_orientation(blank, settings=get_settings(), timeout=120)
        assert orientation is None or orientation.quarter_turns == 0

    def test_orientation_needs_no_binary_to_fail_safely(self) -> None:
        blank: GrayImage = np.full((600, 400), 255, dtype=np.uint8)
        assert (
            detect_orientation(
                blank, settings=settings_with(tesseract_cmd=MISSING_BINARY), timeout=5
            )
            is None
        )


class TestFailures:
    def test_reading_without_an_engine_says_so(self) -> None:
        blank: GrayImage = np.full((600, 400), 255, dtype=np.uint8)
        with pytest.raises(OCRUnavailableError) as raised:
            run_tesseract(
                blank,
                language="eng",
                psm=6,
                settings=settings_with(tesseract_cmd=MISSING_BINARY),
                timeout=5,
            )
        assert raised.value.code.value == "ocr_unavailable"
        assert "tesseract" in str(raised.value.remediation).lower()

    @needs_tesseract
    def test_a_page_that_runs_out_of_time_is_stopped(self, scan_image: GrayImage) -> None:
        # Celery's time limits are not available on every platform the worker runs
        # on, so the deadline has to hold here.
        with pytest.raises(ProcessingTimeoutError) as raised:
            run_tesseract(
                rotate_image(scan_image, 0.0),
                language="eng",
                psm=6,
                settings=get_settings(),
                timeout=0.001,
            )
        assert raised.value.code.value == "processing_timeout"
