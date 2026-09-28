"""The OCR policy: two segmentation modes, one retry, and a scored choice.

Reading a page is not one Tesseract call, and the decisions around it are where the
accuracy is won: which language to ask for, which segmentation mode to believe, and
when to spend twice the time on a second attempt. Each of those is asserted here
against real scans, with the engine's calls counted where the point of the test is how
many were made.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from certex.config import Settings, get_settings
from certex.pipeline.ocr import page_ocr
from certex.pipeline.ocr.page_ocr import ocr_page
from certex.pipeline.ocr.rasterize import RasterPage, rasterize_image, rasterize_pdf_page
from certex.pipeline.ocr.tesseract import resolve_binary
from tests.fixtures.builders import SAMPLES_BY_KEY
from tests.fixtures.corpus import (
    arabic_font,
    build_scanned_bilingual_pdf,
    build_scanned_image,
    build_scanned_pdf,
)

pytestmark = [pytest.mark.unit, pytest.mark.ocr]

needs_tesseract = pytest.mark.skipif(
    resolve_binary(get_settings()) is None,
    reason="the tesseract binary is not installed on this machine",
)
needs_arabic_font = pytest.mark.skipif(
    arabic_font() is None, reason="no Arabic-script font is installed on this machine"
)


def settings_with(**overrides: object) -> Settings:
    return get_settings().model_copy(update=overrides)


def pdf_source(path: Path) -> Any:
    def render(dpi: int) -> RasterPage:
        return rasterize_pdf_page(path, 1, dpi=dpi)

    return render


def read(path: Path, *, settings: Settings | None = None) -> Any:
    return ocr_page(
        pdf_source(path),
        page_number=1,
        workspace_id=uuid.uuid4(),
        settings=settings or get_settings(),
        cache=None,
    )


@needs_tesseract
class TestReadingAScan:
    def test_the_values_on_the_page_come_back(self, tmp_path: Path) -> None:
        result = read(build_scanned_pdf(tmp_path / "scan.pdf"))

        text = result.layout.text
        expected = SAMPLES_BY_KEY["birth_lahore"].lines
        found = sum(1 for _label, value in expected if value in text)
        assert found >= len(expected) * 0.8, f"only {found}/{len(expected)} values read back"

    def test_the_layout_looks_like_any_other_page(self, tmp_path: Path) -> None:
        # Nothing downstream should be able to tell a read page from an OCR'd one.
        result = read(build_scanned_pdf(tmp_path / "scan.pdf"))

        assert result.layout.engine == "tesseract"
        assert result.layout.unit == "px"
        assert result.layout.words and result.layout.lines
        for word in result.layout.words:
            assert 0.0 <= word.bbox.x0 < word.bbox.x1 <= 1.0
            assert 0.0 <= word.bbox.y0 < word.bbox.y1 <= 1.0
            assert word.confidence is not None

    def test_a_label_and_its_value_land_on_one_line(self, tmp_path: Path) -> None:
        result = read(build_scanned_pdf(tmp_path / "scan.pdf"))
        lines = [line.text for line in result.layout.lines]
        assert any("Certificate No" in line and "BC-2019-004471" in line for line in lines)

    def test_the_page_image_is_produced_for_review(self, tmp_path: Path) -> None:
        result = read(build_scanned_pdf(tmp_path / "scan.pdf"))
        assert result.page_image is not None
        assert result.page_image[:2] == b"\xff\xd8", "JPEG magic"
        assert not result.from_cache
        assert len(result.content_hash) == 64

    def test_how_the_page_was_read_is_recorded(self, tmp_path: Path) -> None:
        result = read(build_scanned_pdf(tmp_path / "scan.pdf"))
        assert result.psm in (4, 6)
        assert result.dpi == get_settings().ocr_dpi
        assert result.variant in ("adaptive", "otsu")
        assert result.mean_confidence is not None and result.mean_confidence > 60.0

    def test_a_skewed_scan_is_straightened_before_reading(self, tmp_path: Path) -> None:
        result = read(build_scanned_pdf(tmp_path / "skewed.pdf", skew_degrees=1.6))
        assert result.rotation == pytest.approx(-1.6, abs=0.4)
        assert "BC-2019-004471" in result.layout.text

    def test_a_sideways_scan_is_turned_upright(self, tmp_path: Path) -> None:
        result = read(build_scanned_pdf(tmp_path / "sideways.pdf", rotation=90))
        assert abs(result.rotation) == pytest.approx(90.0, abs=1.0)
        assert "CERTIFICATE OF BIRTH" in result.layout.text.upper()


@needs_tesseract
class TestTheRetry:
    def test_a_good_page_is_read_once(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        engine = _watch_engine(monkeypatch)
        read(build_scanned_pdf(tmp_path / "scan.pdf"))
        assert len(engine.runs) == 2, "one call per segmentation mode, and no retry"
        assert {run["psm"] for run in engine.runs} == {4, 6}
        assert [recipe["variant"] for recipe in engine.recipes] == ["adaptive"]

    def test_a_page_that_reads_badly_gets_a_second_attempt(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        engine = _watch_engine(monkeypatch)
        # Demand a confidence no scan will reach, which is how a faint page behaves.
        read(
            build_scanned_pdf(tmp_path / "scan.pdf"),
            settings=settings_with(ocr_fallback_confidence=99.9),
        )

        assert len(engine.runs) == 4, "both segmentation modes, twice"
        assert [recipe["variant"] for recipe in engine.recipes] == ["adaptive", "otsu"]
        assert [recipe["dpi"] for recipe in engine.recipes] == [
            get_settings().ocr_dpi,
            get_settings().ocr_high_dpi,
        ]

    def test_the_retry_widens_the_language(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A bad first read can mean the page was not in the script that was detected,
        # so the second attempt asks for everything configured.
        engine = _watch_engine(monkeypatch)
        read(
            build_scanned_pdf(tmp_path / "scan.pdf"),
            settings=settings_with(ocr_fallback_confidence=99.9),
        )
        first_language = engine.runs[0]["language"]
        last_language = engine.runs[-1]["language"]
        assert first_language == "eng"
        assert set(last_language.split("+")) >= {"eng", "urd"}

    def test_an_image_document_retries_with_the_pixels_it_has(self, tmp_path: Path) -> None:
        # A scan cannot be re-rendered at a higher resolution; the retry still helps
        # by preparing the same pixels differently.
        path = build_scanned_image(tmp_path / "scan.png", dpi=300)

        def source(dpi: int) -> RasterPage:
            del dpi
            return rasterize_image(path)

        result = ocr_page(
            source,
            page_number=1,
            workspace_id=uuid.uuid4(),
            settings=settings_with(ocr_fallback_confidence=99.9),
            cache=None,
        )
        assert result.dpi == 300
        assert "BC-2019-004471" in result.layout.text


@dataclass(slots=True)
class _EngineWatch:
    """What the policy asked the engine and the preprocessor to do."""

    runs: list[dict[str, Any]] = field(default_factory=list)
    recipes: list[dict[str, Any]] = field(default_factory=list)


def _watch_engine(monkeypatch: pytest.MonkeyPatch) -> _EngineWatch:
    """Record each preprocessing recipe and each Tesseract run, and let both proceed."""
    watch = _EngineWatch()
    run_original = page_ocr.run_tesseract
    preprocess_original = page_ocr.preprocess

    def recording_run(image: Any, **kwargs: Any) -> Any:
        watch.runs.append({"psm": kwargs["psm"], "language": kwargs["language"]})
        return run_original(image, **kwargs)

    def recording_preprocess(image: Any, **kwargs: Any) -> Any:
        watch.recipes.append({"variant": kwargs.get("variant", "adaptive"), "dpi": kwargs["dpi"]})
        return preprocess_original(image, **kwargs)

    monkeypatch.setattr(page_ocr, "run_tesseract", recording_run)
    monkeypatch.setattr(page_ocr, "preprocess", recording_preprocess)
    return watch


@needs_tesseract
@needs_arabic_font
class TestUrduScans:
    def test_an_urdu_scan_is_read_in_urdu(self, tmp_path: Path) -> None:
        result = read(build_scanned_bilingual_pdf(tmp_path / "urdu.pdf"))
        assert result.language.startswith("urd"), result.language

    def test_urdu_text_comes_back(self, tmp_path: Path) -> None:
        result = read(build_scanned_bilingual_pdf(tmp_path / "urdu.pdf"))
        text = result.layout.text
        assert any(char >= "؀" and char <= "ۿ" for char in text), text[:200]
        assert "BC-2020-001122" in text

    def test_the_english_labels_are_still_read(self, tmp_path: Path) -> None:
        result = read(build_scanned_bilingual_pdf(tmp_path / "urdu.pdf"))
        assert "Certificate" in result.layout.text
