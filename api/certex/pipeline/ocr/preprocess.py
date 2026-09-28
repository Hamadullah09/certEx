"""Preparing a scan for OCR.

A flatbed scan of a certificate is rarely square, rarely clean and often too small:
the sheet is fed a degree or two askew, the glass adds speckle, the paper is grey.
Tesseract is measurably better on an image that has been straightened, de-speckled and
binarised, and substantially better when small text is upscaled before it reads.

Two images come out of this module and the difference matters:

* ``image`` is what Tesseract reads - straightened, de-speckled, black on white, and
  upscaled when the page is small.
* ``display`` is what the reviewer sees - the same straightening applied to the
  grayscale page, with no binarisation, because a thresholded scan is unpleasant to
  read and hides the marks that explain a bad extraction.

Both share one geometry, so a box found in the OCR image, stored as a fraction of its
size, lands in the right place over the display image at any zoom.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal

import cv2
import numpy as np

from certex.pipeline.ocr.rasterize import GrayImage

__all__ = [
    "PREPROCESS_VERSION",
    "PreprocessVariant",
    "Preprocessed",
    "estimate_skew",
    "preprocess",
    "rotate_image",
]

PREPROCESS_VERSION: Final = 1
"""Bumped when the pipeline changes, so cached OCR from an older recipe is not reused."""

PreprocessVariant = Literal["adaptive", "otsu"]
"""``adaptive`` handles uneven lighting; ``otsu`` is the retry for faint, even scans."""

_MAX_SKEW_DEGREES: Final = 5.0
"""Beyond this a page is not skewed, it is rotated; orientation detection handles that."""

_SKEW_WORKING_WIDTH: Final = 800
"""Skew is measured on a downscaled copy: the angle is the same and the search is fast."""

_WHITE: Final = 255


def _gray(array: object) -> GrayImage:
    """OpenCV's own type stubs are loose about element types; this pins ours."""
    return np.asarray(array, dtype=np.uint8)


@dataclass(frozen=True, slots=True)
class Preprocessed:
    """The two images OCR and the reviewer each need, and how they were produced."""

    image: GrayImage
    display: GrayImage
    rotation: float
    """Total rotation applied, in degrees: orientation quarter turns plus deskew."""

    scale: float
    """How much ``image`` was upscaled relative to ``display``."""

    variant: PreprocessVariant


def rotate_image(image: GrayImage, angle: float, *, border: int = _WHITE) -> GrayImage:
    """Rotate about the centre, growing the canvas so no ink is cut off."""
    if angle == 0.0:
        return image
    height, width = image.shape[:2]
    centre = (width / 2.0, height / 2.0)
    matrix = cv2.getRotationMatrix2D(centre, angle, 1.0)
    cosine, sine = abs(matrix[0, 0]), abs(matrix[0, 1])
    grown_width = int(height * sine + width * cosine)
    grown_height = int(height * cosine + width * sine)
    matrix[0, 2] += grown_width / 2.0 - centre[0]
    matrix[1, 2] += grown_height / 2.0 - centre[1]
    return _gray(
        cv2.warpAffine(
            image,
            matrix,
            (grown_width, grown_height),
            flags=cv2.INTER_CUBIC,
            borderMode=cv2.BORDER_CONSTANT,
            borderValue=(float(border),),
        )
    )


def _ink_mask(image: GrayImage) -> GrayImage:
    """Ink as white on black, which is what the projection profile counts."""
    _threshold, mask = cv2.threshold(image, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    return _gray(mask)


def estimate_skew(image: GrayImage, *, limit: float = _MAX_SKEW_DEGREES) -> float:
    """The angle that straightens the page, in degrees, positive counter-clockwise.

    Measured by projection profile: when the lines of a page are horizontal, summing
    the ink in each row gives tall peaks at the lines and near-zero between them, so
    the variance of that profile is highest at the correct angle. This beats fitting a
    box around the text - a certificate's text block is not a rectangle - and it needs
    no line detection.
    """
    height, width = image.shape[:2]
    if min(height, width) < 32:
        return 0.0
    scale = min(1.0, _SKEW_WORKING_WIDTH / float(width))
    working = (
        _gray(
            cv2.resize(
                image, (int(width * scale), int(height * scale)), interpolation=cv2.INTER_AREA
            )
        )
        if scale < 1.0
        else image
    )
    mask = _ink_mask(working)
    if int(cv2.countNonZero(mask)) < 50:
        return 0.0

    def sharpness(angle: float) -> float:
        rotated = rotate_image(mask, angle, border=0)
        profile = rotated.sum(axis=1, dtype=np.float64)
        return float(profile.var())

    coarse = max(np.arange(-limit, limit + 0.5, 1.0), key=sharpness)
    fine = max(np.arange(coarse - 1.0, coarse + 1.1, 0.1), key=sharpness)
    # The refinement can step past the limit; a page beyond it is rotated rather than
    # skewed, and orientation detection - not this - is what fixes that.
    angle = round(float(min(max(fine, -limit), limit)), 2)
    return 0.0 if abs(angle) < 0.1 else angle


def _binarise(image: GrayImage, *, variant: PreprocessVariant, dpi: int) -> GrayImage:
    if variant == "otsu":
        # A faint but evenly lit scan: one global threshold keeps thin strokes that
        # a local threshold erodes.
        blurred = cv2.GaussianBlur(image, (3, 3), 0)
        _threshold, binary = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        return _gray(binary)
    # Adaptive: a window around 1/10 inch tracks shadows and grey paper.
    block = max(3, int(dpi / 10) | 1)
    return _gray(
        cv2.adaptiveThreshold(
            cv2.medianBlur(image, 3),
            _WHITE,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY,
            block,
            15,
        )
    )


def preprocess(
    image: GrayImage,
    *,
    dpi: int,
    variant: PreprocessVariant = "adaptive",
    quarter_turns: int = 0,
    deskew: bool = True,
    upscale: float = 2.0,
    upscale_width_threshold: int = 1200,
) -> Preprocessed:
    """Straighten, clean and binarise a page for OCR.

    ``quarter_turns`` comes from orientation detection and is applied first: a page
    scanned sideways must be upright before its skew means anything.
    """
    display = image
    rotation = 0.0
    if quarter_turns % 4:
        display = np.rot90(display, k=-(quarter_turns % 4)).copy()
        rotation = float((quarter_turns % 4) * -90)

    if deskew:
        angle = estimate_skew(display)
        if angle:
            display = rotate_image(display, angle)
            rotation += angle

    binary = _binarise(display, variant=variant, dpi=dpi)

    scale = 1.0
    if upscale > 1.0 and binary.shape[1] < upscale_width_threshold:
        scale = upscale
        binary = _gray(
            cv2.resize(
                binary,
                (int(binary.shape[1] * scale), int(binary.shape[0] * scale)),
                interpolation=cv2.INTER_CUBIC,
            )
        )

    return Preprocessed(
        image=binary,
        display=display,
        # The short way round: a page turned three quarters clockwise is reported as
        # a quarter turn anti-clockwise, which is how a person would describe it.
        rotation=round((rotation + 180.0) % 360.0 - 180.0, 2),
        scale=scale,
        variant=variant,
    )
