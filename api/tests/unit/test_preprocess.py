"""Straightening and cleaning a scan before OCR reads it.

Skew is the case that earns its tests: a page fed a degree or two askew loses
Tesseract several points of accuracy, and the angle has to be recovered from the page
itself. Each test here degrades a real certificate by a known amount and asks for that
amount back.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from certex.pipeline.ocr.preprocess import estimate_skew, preprocess, rotate_image
from certex.pipeline.ocr.rasterize import GrayImage, rasterize_pdf_page
from tests.fixtures.builders import build_text_pdf
from tests.fixtures.corpus import build_scanned_pdf

pytestmark = pytest.mark.unit


@pytest.fixture(scope="module")
def clean_page(tmp_path_factory: pytest.TempPathFactory) -> GrayImage:
    """A certificate rendered once, at a realistic scanning resolution."""
    directory = tmp_path_factory.mktemp("preprocess")
    return rasterize_pdf_page(build_text_pdf(directory / "birth.pdf"), 1, dpi=200).image


class TestSkew:
    @pytest.mark.parametrize("angle", [-3.0, -1.2, 0.8, 2.5])
    def test_a_known_skew_is_recovered(self, clean_page: GrayImage, angle: float) -> None:
        skewed = rotate_image(clean_page, angle)
        # The estimate is the rotation that undoes the skew, so it mirrors the angle.
        assert estimate_skew(skewed) == pytest.approx(-angle, abs=0.35)

    def test_a_straight_page_is_left_alone(self, clean_page: GrayImage) -> None:
        assert estimate_skew(clean_page) == 0.0

    def test_a_blank_page_has_no_skew(self) -> None:
        blank: GrayImage = np.full((400, 300), 255, dtype=np.uint8)
        assert estimate_skew(blank) == 0.0

    def test_a_tiny_image_has_no_skew(self) -> None:
        speck: GrayImage = np.zeros((8, 8), dtype=np.uint8)
        assert estimate_skew(speck) == 0.0

    def test_skew_beyond_the_limit_is_not_chased(self, clean_page: GrayImage) -> None:
        # A page 20 degrees off is not skewed, it is rotated, and orientation
        # detection deals with that. The estimator must not report a wild angle.
        assert abs(estimate_skew(rotate_image(clean_page, 20.0))) <= 5.0


class TestRotation:
    def test_the_canvas_grows_so_no_ink_is_cut_off(self, clean_page: GrayImage) -> None:
        rotated = rotate_image(clean_page, 10.0)
        assert rotated.shape[0] > clean_page.shape[0]
        assert rotated.shape[1] > clean_page.shape[1]

    def test_the_new_corners_are_paper_not_black(self, clean_page: GrayImage) -> None:
        rotated = rotate_image(clean_page, 10.0)
        assert int(rotated[0, 0]) > 200, "a black corner would read as ink"

    def test_rotating_by_nothing_changes_nothing(self, clean_page: GrayImage) -> None:
        assert rotate_image(clean_page, 0.0) is clean_page


class TestPreprocessing:
    def test_the_ocr_image_is_two_tone(self, clean_page: GrayImage) -> None:
        prepared = preprocess(clean_page, dpi=200)
        assert set(np.unique(prepared.image).tolist()) <= {0, 255}

    def test_the_review_image_keeps_its_greys(self, clean_page: GrayImage) -> None:
        prepared = preprocess(clean_page, dpi=200)
        assert len(np.unique(prepared.display)) > 2

    def test_both_images_share_one_geometry(self, clean_page: GrayImage) -> None:
        # Boxes found in the OCR image are stored as fractions and drawn over the
        # review image, so their aspect ratios must match.
        prepared = preprocess(clean_page, dpi=200)
        ocr_ratio = prepared.image.shape[1] / prepared.image.shape[0]
        display_ratio = prepared.display.shape[1] / prepared.display.shape[0]
        assert ocr_ratio == pytest.approx(display_ratio, rel=0.01)

    def test_a_skewed_page_comes_out_straight(self, clean_page: GrayImage) -> None:
        prepared = preprocess(rotate_image(clean_page, 2.0), dpi=200)
        assert prepared.rotation == pytest.approx(-2.0, abs=0.35)
        assert estimate_skew(prepared.display) == pytest.approx(0.0, abs=0.2)

    def test_deskew_can_be_switched_off(self, clean_page: GrayImage) -> None:
        prepared = preprocess(rotate_image(clean_page, 2.0), dpi=200, deskew=False)
        assert prepared.rotation == 0.0

    @pytest.mark.parametrize("variant", ["adaptive", "otsu"])
    def test_both_recipes_leave_ink_and_paper(self, clean_page: GrayImage, variant: str) -> None:
        prepared = preprocess(clean_page, dpi=200, variant=variant)  # type: ignore[arg-type]
        dark = int((prepared.image == 0).sum())
        light = int((prepared.image == 255).sum())
        assert dark > 1000 and light > dark, "a page is mostly paper with some ink"
        assert prepared.variant == variant


class TestUpscaling:
    def test_a_small_page_is_enlarged_for_tesseract(self) -> None:
        small: GrayImage = np.full((300, 400), 255, dtype=np.uint8)
        small[100:150, 50:350] = 0
        prepared = preprocess(small, dpi=96, upscale=2.0, upscale_width_threshold=1200)
        assert prepared.scale == 2.0
        assert prepared.image.shape[1] == 800

    def test_a_full_size_page_is_left_alone(self, clean_page: GrayImage) -> None:
        prepared = preprocess(clean_page, dpi=200, upscale=2.0, upscale_width_threshold=1200)
        assert prepared.scale == 1.0
        assert prepared.image.shape[1] == clean_page.shape[1]


class TestOrientation:
    def test_a_quarter_turn_is_undone(self, clean_page: GrayImage) -> None:
        sideways = np.rot90(clean_page, k=1).copy()  # 90 degrees anti-clockwise
        prepared = preprocess(sideways, dpi=200, quarter_turns=3)
        assert prepared.display.shape[:2] == clean_page.shape[:2]

    def test_rotation_is_reported_the_short_way_round(self, clean_page: GrayImage) -> None:
        prepared = preprocess(clean_page, dpi=200, quarter_turns=3, deskew=False)
        assert prepared.rotation == 90.0, "three quarter turns is a quarter turn back"


class TestRealScans:
    def test_a_degraded_scan_survives_the_whole_recipe(self, tmp_path: Path) -> None:
        path = build_scanned_pdf(tmp_path / "scan.pdf", skew_degrees=1.5, noise=0.03, blur=0.4)
        raster = rasterize_pdf_page(path, 1, dpi=300)

        prepared = preprocess(raster.image, dpi=300)

        assert prepared.rotation == pytest.approx(-1.5, abs=0.4)
        assert set(np.unique(prepared.image).tolist()) <= {0, 255}

        # The top margin is blank paper. Speckle there survives binarisation as ink
        # and Tesseract reads it as punctuation, so it has to come out nearly clean.
        margin = prepared.image[: int(prepared.image.shape[0] * 0.04), :]
        assert float((margin == 0).mean()) < 0.01
