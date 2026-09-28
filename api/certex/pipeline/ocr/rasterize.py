"""Turning a page into pixels.

OCR reads an image, so a PDF page is rendered and an uploaded scan is decoded, both
to a single-channel grayscale array. Two decisions matter downstream:

* **Resolution.** Tesseract's accuracy falls off sharply below roughly 300 DPI, so a
  PDF page is rendered at a configured DPI rather than at its natural size, and a
  scan that arrives without a resolution tag has one estimated from its width.
* **Geometry.** The array returned here is the reference frame for everything that
  follows: preprocessing may rotate and scale it, and every stored bounding box is a
  fraction of the image OCR actually read, so the review pane can draw boxes over the
  page image without knowing any of this.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal

import fitz
import numpy as np
from numpy.typing import NDArray
from PIL import Image, UnidentifiedImageError

from certex.core.errors import CorruptDocumentError

__all__ = [
    "GrayImage",
    "RasterPage",
    "encode_jpeg",
    "rasterize_image",
    "rasterize_pdf_page",
]

GrayImage = NDArray[np.uint8]
"""A two-dimensional array of grayscale pixels, origin top-left."""

_A4_WIDTH_INCHES: Final = 8.27
"""Almost every certificate is A4 or Letter; both are near enough for an estimate."""

_MIN_DPI: Final = 72
_MAX_DPI: Final = 1200


@dataclass(frozen=True, slots=True)
class RasterPage:
    """One page as pixels, with the resolution it was produced at."""

    image: GrayImage
    dpi: int
    source: Literal["pdf", "image"]

    @property
    def width(self) -> int:
        return int(self.image.shape[1])

    @property
    def height(self) -> int:
        return int(self.image.shape[0])


def rasterize_pdf_page(path: Path, page_number: int, *, dpi: int) -> RasterPage:
    """Render one page of a PDF to grayscale pixels at ``dpi``.

    The page's own /Rotate entry is applied by the renderer, so a page a scanner
    marked as rotated arrives upright.
    """
    try:
        with fitz.open(str(path)) as document:
            if not 1 <= page_number <= int(document.page_count):
                raise CorruptDocumentError(
                    f"The file has no page {page_number}.",
                    title="Page not found in document",
                )
            pixmap = document.load_page(page_number - 1).get_pixmap(
                dpi=dpi, colorspace=fitz.csGRAY, annots=False
            )
            # MuPDF pads each row to a stride; the trailing bytes are not pixels.
            rows = np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(
                pixmap.height, pixmap.stride
            )
            image = np.ascontiguousarray(rows[:, : pixmap.width])
    except (RuntimeError, ValueError, fitz.mupdf.FzErrorBase) as exc:
        raise CorruptDocumentError(
            f"Page {page_number} could not be rendered for reading.",
            remediation="Re-export or re-scan the file and upload it again.",
        ) from exc
    return RasterPage(image=image, dpi=dpi, source="pdf")


def rasterize_image(path: Path, *, frame: int = 1) -> RasterPage:
    """Decode one frame of an uploaded image to grayscale pixels."""
    try:
        with Image.open(path) as handle:
            if frame > 1:
                handle.seek(frame - 1)
            dpi = _image_dpi(handle)
            image = np.array(handle.convert("L"), dtype=np.uint8)
    except (UnidentifiedImageError, OSError, EOFError, ValueError) as exc:
        raise CorruptDocumentError(
            "The image could not be read; it appears damaged.",
            remediation="Re-scan or re-export the image and upload it again.",
        ) from exc
    return RasterPage(image=image, dpi=dpi, source="image")


def _image_dpi(handle: Image.Image) -> int:
    """The image's own resolution, or one estimated from its width.

    A resolution matters because it decides whether the image is upscaled before
    OCR. Scanner software usually records it; phone cameras and screenshots do not,
    and a tag of 1 or 96 DPI on a 2500-pixel-wide page is a lie worth ignoring.
    """
    estimated = round(handle.width / _A4_WIDTH_INCHES)
    raw = handle.info.get("dpi")
    if isinstance(raw, tuple) and raw:
        try:
            tagged = round(float(min(value for value in raw if float(value) > 0)))
        except (TypeError, ValueError):
            tagged = 0
        if _MIN_DPI <= tagged <= _MAX_DPI and tagged >= estimated * 0.5:
            return tagged
    return max(_MIN_DPI, min(_MAX_DPI, estimated))


def encode_jpeg(image: GrayImage, *, quality: int = 80) -> bytes:
    """JPEG bytes for the review pane's page image."""
    buffer = io.BytesIO()
    Image.fromarray(image, mode="L").save(buffer, format="JPEG", quality=quality, optimize=True)
    return buffer.getvalue()
