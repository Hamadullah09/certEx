"""Stage 4 - read the pages that have no usable text layer.

One task per page, because OCR is the slowest thing the pipeline does and a fifty-page
scan should use fifty workers' worth of time rather than one worker for a minute. That
makes finishing a fan-in: each page writes its own row, and whichever page turns out to
be the last one advances the document to splitting. The advance is a compare-and-set on
the document's status, so two pages finishing at the same instant enqueue splitting
once, not twice.

Alongside the text, each page leaves behind a JPEG of itself - straightened exactly as
OCR saw it - so the review pane can draw a box around the word a value came from.
"""

from __future__ import annotations

import shutil
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import func, select

from certex.config import Settings, get_settings
from certex.core.errors import AppError, UnsupportedMediaTypeError
from certex.db.models import Document, PageText
from certex.db.session import session_scope
from certex.enums import DocumentStatus, OcrEngine, PageExtractionSource
from certex.logging_setup import get_logger
from certex.pipeline.dispatch import TASK_SPLIT_DOCUMENT, Dispatch, enqueue
from certex.pipeline.filetypes import FileKind, kind_for_mime
from certex.pipeline.ocr.cache import OcrCache
from certex.pipeline.ocr.page_ocr import OcrPageResult, RasterSource, ocr_page
from certex.pipeline.ocr.preprocess import preprocess
from certex.pipeline.ocr.rasterize import (
    RasterPage,
    encode_jpeg,
    rasterize_image,
    rasterize_pdf_page,
)
from certex.pipeline.state import claim_document, fail_document, refresh_batch_progress
from certex.pipeline.text.normalize import detect_language
from certex.pipeline.text.quality import assess_text
from certex.storage.s3 import ObjectStorage, StorageKeys, get_object_storage

__all__ = ["OcrStageResult", "run_ocr_page_stage"]

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class OcrStageResult:
    document_id: uuid.UUID
    page_number: int
    ran: bool
    """False when the page was already read, or the document has moved on."""

    mean_confidence: float | None = None
    word_count: int = 0
    from_cache: bool = False
    advanced: bool = False
    """True when this page was the last one and the document moved to splitting."""

    failed_code: str | None = None


@dataclass(frozen=True, slots=True)
class _Job:
    """What the stage needs to read one page, read once under a transaction."""

    workspace_id: uuid.UUID
    batch_id: uuid.UUID
    storage_key: str
    mime_type: str


def _load_job(document_id: uuid.UUID, page_number: int) -> _Job | None:
    """The document's details, or None when this page no longer needs reading."""
    with session_scope() as session:
        document = session.get(Document, document_id)
        if document is None or document.status is not DocumentStatus.OCR:
            return None
        page = session.scalar(
            select(PageText).where(
                PageText.document_id == document_id, PageText.page_number == page_number
            )
        )
        if page is None or page.extraction_source is not PageExtractionSource.NONE:
            # No such page, or another delivery of this task already read it.
            return None
        return _Job(
            workspace_id=document.workspace_id,
            batch_id=document.batch_id,
            storage_key=document.storage_key,
            mime_type=document.mime_type,
        )


def _raster_source(local: Path, kind: FileKind, page_number: int) -> RasterSource:
    """A renderer for this page: a PDF page at any DPI, or an image's own pixels."""
    if kind is FileKind.PDF:

        def render_pdf(dpi: int) -> RasterPage:
            return rasterize_pdf_page(local, page_number, dpi=dpi)

        return render_pdf

    if kind is FileKind.IMAGE:

        def render_image(dpi: int) -> RasterPage:
            # An uploaded scan has the resolution it has; a higher DPI cannot be
            # rendered, so the retry differs only in how the image is prepared.
            del dpi
            return rasterize_image(local, frame=page_number)

        return render_image

    raise UnsupportedMediaTypeError("Only PDFs and images can be read by OCR.")


def _store_page(
    document_id: uuid.UUID,
    page_number: int,
    result: OcrPageResult,
    *,
    source: PageExtractionSource,
    settings: Settings,
) -> None:
    """Write one page's OCR result, in its own transaction."""
    text = result.layout.text
    quality = assess_text(
        text,
        min_chars=settings.text_quality_min_chars,
        min_dict_ratio=settings.text_quality_min_dict_ratio,
    )
    with session_scope() as session:
        page = session.scalar(
            select(PageText).where(
                PageText.document_id == document_id, PageText.page_number == page_number
            )
        )
        if page is None:  # pragma: no cover - the text stage created it
            return
        page.extraction_source = source
        page.raw_text = text
        page.layout_blocks_jsonb = result.layout.model_dump(mode="json")
        page.ocr_engine = OcrEngine.TESSERACT
        page.ocr_mean_confidence = result.mean_confidence
        page.language = detect_language(text) or result.language
        page.char_count = quality.char_count
        page.alnum_ratio = quality.alnum_ratio
        page.dict_hit_rate = quality.dict_hit_rate
        page.needed_ocr = True
        page.width = result.layout.width
        page.height = result.layout.height
        page.rotation = result.rotation
        page.content_hash = result.content_hash


def _pages_still_unread(document_id: uuid.UUID) -> int:
    with session_scope() as session:
        pending = session.scalar(
            select(func.count())
            .select_from(PageText)
            .where(
                PageText.document_id == document_id,
                PageText.needed_ocr.is_(True),
                PageText.extraction_source == PageExtractionSource.NONE,
            )
        )
        return int(pending or 0)


def _upload_page_image(
    storage: ObjectStorage, job: _Job, document_id: uuid.UUID, page_number: int, image: bytes
) -> None:
    storage.upload_bytes(
        StorageKeys.page_image(job.workspace_id, document_id, page_number),
        image,
        content_type="image/jpeg",
    )


def run_ocr_page_stage(
    document_id: uuid.UUID,
    page_number: int,
    *,
    settings: Settings | None = None,
    storage: ObjectStorage | None = None,
    dispatch: Dispatch = enqueue,
) -> OcrStageResult:
    """Read one page of a document, and advance the document when it was the last."""
    active = settings or get_settings()
    store = storage or get_object_storage()

    job = _load_job(document_id, page_number)
    if job is None:
        return OcrStageResult(document_id=document_id, page_number=page_number, ran=False)

    workdir = Path(tempfile.mkdtemp(prefix="certex-ocr-"))
    try:
        local = workdir / "source"
        store.download_to_path(job.storage_key, local)
        kind = kind_for_mime(job.mime_type)
        result = ocr_page(
            _raster_source(local, kind, page_number),
            page_number=page_number,
            workspace_id=job.workspace_id,
            settings=active,
            cache=OcrCache(store),
        )
        page_image = result.page_image
        if page_image is None and not store.object_exists(
            StorageKeys.page_image(job.workspace_id, document_id, page_number)
        ):
            # The OCR text came from the cache but this document has no page image
            # yet: render one, because the review pane needs it.
            page_image = _render_page_image(local, kind, page_number, settings=active)
        if page_image is not None:
            _upload_page_image(store, job, document_id, page_number, page_image)

        _store_page(
            document_id,
            page_number,
            result,
            source=(
                PageExtractionSource.IMAGE_OCR
                if kind is FileKind.IMAGE
                else PageExtractionSource.OCR
            ),
            settings=active,
        )
    except AppError as exc:
        with session_scope() as session:
            fail_document(session, document_id, code=exc.code.value, message=str(exc))
            refresh_batch_progress(session, job.batch_id)
        logger.warning(
            "ocr.page_failed",
            document_id=str(document_id),
            page_number=page_number,
            code=exc.code.value,
        )
        return OcrStageResult(
            document_id=document_id,
            page_number=page_number,
            ran=True,
            failed_code=exc.code.value,
        )
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    advanced = False
    if _pages_still_unread(document_id) == 0:
        with session_scope() as session:
            advanced = claim_document(
                session,
                document_id,
                from_statuses=(DocumentStatus.OCR,),
                to_status=DocumentStatus.SPLITTING,
            )
    if advanced:
        dispatch(TASK_SPLIT_DOCUMENT, {"document_id": str(document_id)})

    return OcrStageResult(
        document_id=document_id,
        page_number=page_number,
        ran=True,
        mean_confidence=result.mean_confidence,
        word_count=len(result.layout.words),
        from_cache=result.from_cache,
        advanced=advanced,
    )


def _render_page_image(
    local: Path, kind: FileKind, page_number: int, *, settings: Settings
) -> bytes:
    """The page as the reviewer should see it, straightened the way OCR straightened it."""
    raster = _raster_source(local, kind, page_number)(settings.ocr_dpi)
    prepared = preprocess(
        raster.image,
        dpi=raster.dpi,
        upscale=settings.ocr_upscale_factor,
        upscale_width_threshold=settings.ocr_upscale_width_threshold,
    )
    return encode_jpeg(prepared.display)
