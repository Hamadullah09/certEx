"""The picture of a page, for the reviewer looking at a value.

Reviewing an extracted value means comparing it with the page it came from, so every
document needs a page image - not only the scans. A scanned page already has one, made
when OCR straightened it; a text PDF or a Word file has never been drawn at all, so it is
rendered here on first request and kept, because the second reviewer to open that row
should not pay for it again.

The rendering deliberately matches what OCR would have done - same resolution, same
deskew - so a stored bounding box lands in the same place whichever kind of document it
came from.
"""

from __future__ import annotations

import shutil
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path

from certex.config import Settings
from certex.core.errors import NotFoundError, UnsupportedMediaTypeError
from certex.logging_setup import get_logger
from certex.pipeline.filetypes import FileKind, kind_for_mime
from certex.pipeline.ocr.preprocess import preprocess
from certex.pipeline.ocr.rasterize import encode_jpeg, rasterize_image, rasterize_pdf_page
from certex.storage.s3 import ObjectStorage, StorageKeys

__all__ = ["PageImage", "page_image"]

logger = get_logger(__name__)

REVIEW_DPI = 150
"""Enough to read a certificate on screen without sending megabytes per page."""


@dataclass(frozen=True, slots=True)
class PageImage:
    """The page as JPEG bytes, and whether it had to be drawn for this request."""

    payload: bytes
    rendered: bool


def page_image(
    *,
    workspace_id: uuid.UUID,
    document_id: uuid.UUID,
    page_number: int,
    storage_key: str,
    mime_type: str,
    storage: ObjectStorage,
    settings: Settings,
) -> PageImage:
    """The stored page image, or one rendered and stored now."""
    key = StorageKeys.page_image(workspace_id, document_id, page_number)
    existing = storage.get_bytes_if_exists(key)
    if existing is not None:
        return PageImage(payload=existing, rendered=False)

    payload = _render(
        storage_key=storage_key,
        mime_type=mime_type,
        page_number=page_number,
        storage=storage,
        settings=settings,
    )
    storage.upload_bytes(key, payload, content_type="image/jpeg")
    logger.info(
        "pages.rendered",
        document_id=str(document_id),
        page_number=page_number,
        byte_size=len(payload),
    )
    return PageImage(payload=payload, rendered=True)


def _render(
    *,
    storage_key: str,
    mime_type: str,
    page_number: int,
    storage: ObjectStorage,
    settings: Settings,
) -> bytes:
    kind = kind_for_mime(mime_type)
    if kind not in (FileKind.PDF, FileKind.IMAGE):
        # A Word document has no pages until something renders it, and this deployment
        # has no renderer. The review screen shows the text instead of a picture.
        raise UnsupportedMediaTypeError(
            "This kind of file has no page image to show.",
            remediation="Open the original file to see the page.",
        )

    workdir = Path(tempfile.mkdtemp(prefix="certex-page-"))
    try:
        local = workdir / "source"
        storage.download_to_path(storage_key, local)
        raster = (
            rasterize_pdf_page(local, page_number, dpi=REVIEW_DPI)
            if kind is FileKind.PDF
            else rasterize_image(local, frame=page_number)
        )
        # Same recipe as OCR, so a box stored against the OCR image lands correctly here.
        prepared = preprocess(
            raster.image,
            dpi=raster.dpi,
            upscale=settings.ocr_upscale_factor,
            upscale_width_threshold=settings.ocr_upscale_width_threshold,
        )
        return encode_jpeg(prepared.display)
    except FileNotFoundError as exc:  # pragma: no cover - download creates the file
        raise NotFoundError("The stored document is no longer available.") from exc
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
