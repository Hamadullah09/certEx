"""Stage 3 - read every page's text, and decide which pages need OCR.

For each document this stage:

1. **claims** it (QUEUED -> EXTRACTING_TEXT) so a redelivered task cannot run it
   twice;
2. downloads the stored file to a private temporary directory;
3. reads each page - a PDF text layer, a Word document, or nothing yet for an
   image - and scores the text layer's quality;
4. writes one ``page_texts`` row per page, committing page by page, so a 500-page
   file never holds more than one page in memory and a crash loses one page of
   work at most;
5. **advances** the document: to OCR when any page needs it, otherwise straight
   to splitting - and only then enqueues the next stage, after the commit, so the
   next task always sees what this one wrote.

A file that cannot be read fails alone, with a reason; nothing here can fail a
batch.
"""

from __future__ import annotations

import shutil
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, UnidentifiedImageError
from sqlalchemy import select

from certex.config import Settings, get_settings
from certex.core.errors import AppError, CorruptDocumentError, UnsupportedMediaTypeError
from certex.db.base import utcnow
from certex.db.models import Document, PageText
from certex.db.session import session_scope
from certex.enums import DocumentStatus, OcrEngine, PageExtractionSource
from certex.logging_setup import get_logger
from certex.pipeline.dispatch import (
    TASK_OCR_PAGE,
    TASK_SPLIT_DOCUMENT,
    Dispatch,
    TaskArgument,
    enqueue,
)
from certex.pipeline.filetypes import FileKind, kind_for_mime
from certex.pipeline.state import claim_document, fail_document, refresh_batch_progress
from certex.pipeline.text.doc_convert import convert_doc_to_docx
from certex.pipeline.text.docx_reader import read_docx_pages
from certex.pipeline.text.normalize import detect_language
from certex.pipeline.text.pdf_native import iter_pdf_layouts
from certex.pipeline.text.quality import TextQuality, assess_text
from certex.schemas.layout import PageLayout
from certex.storage.s3 import ObjectStorage, get_object_storage

__all__ = ["TextStageResult", "run_text_stage"]

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class TextStageResult:
    document_id: uuid.UUID
    ran: bool
    """False when the document was already past this stage, or no longer exists."""

    page_count: int = 0
    ocr_pages: list[int] = field(default_factory=list)
    failed_code: str | None = None


def _store_page(
    document_id: uuid.UUID,
    layout: PageLayout,
    *,
    source: PageExtractionSource,
    quality: TextQuality,
    needs_ocr: bool,
) -> None:
    """Insert or replace one page's row, in its own transaction."""
    with session_scope() as session:
        row = session.scalar(
            select(PageText).where(
                PageText.document_id == document_id,
                PageText.page_number == layout.page_number,
            )
        )
        if row is None:
            row = PageText(document_id=document_id, page_number=layout.page_number)
            session.add(row)
        text = layout.text
        row.extraction_source = PageExtractionSource.NONE if needs_ocr else source
        row.raw_text = text
        row.layout_blocks_jsonb = layout.model_dump(mode="json")
        row.ocr_engine = OcrEngine.NONE
        row.ocr_mean_confidence = None
        row.language = detect_language(text)
        row.char_count = quality.char_count
        row.alnum_ratio = quality.alnum_ratio
        row.dict_hit_rate = quality.dict_hit_rate
        row.needed_ocr = needs_ocr
        row.width = layout.width
        row.height = layout.height
        row.rotation = 0.0


def _read_pdf(path: Path, document_id: uuid.UUID, settings: Settings) -> tuple[int, list[int]]:
    pages = 0
    ocr_pages: list[int] = []
    for layout in iter_pdf_layouts(path):
        pages += 1
        quality = assess_text(
            layout.text,
            min_chars=settings.text_quality_min_chars,
            min_dict_ratio=settings.text_quality_min_dict_ratio,
        )
        if quality.needs_ocr:
            ocr_pages.append(layout.page_number)
        _store_page(
            document_id,
            layout,
            source=PageExtractionSource.NATIVE_PDF,
            quality=quality,
            needs_ocr=quality.needs_ocr,
        )
    if pages == 0:
        raise CorruptDocumentError("The PDF contains no pages.", title="Document has no pages")
    return pages, ocr_pages


def _read_docx(path: Path, document_id: uuid.UUID, settings: Settings) -> tuple[int, list[int]]:
    layouts = read_docx_pages(path)
    for layout in layouts:
        quality = assess_text(
            layout.text,
            min_chars=settings.text_quality_min_chars,
            min_dict_ratio=settings.text_quality_min_dict_ratio,
        )
        # A Word document has no image to OCR: its text is all there is, however
        # sparse, so it is never routed to OCR.
        _store_page(
            document_id,
            layout,
            source=PageExtractionSource.DOCX,
            quality=quality,
            needs_ocr=False,
        )
    return len(layouts), []


def _read_image(path: Path, document_id: uuid.UUID) -> tuple[int, list[int]]:
    """An image has no text layer: every frame is a page for OCR."""
    try:
        with Image.open(path) as image:
            frames = int(getattr(image, "n_frames", 1))
            width, height = image.size
    except (UnidentifiedImageError, OSError) as exc:
        raise CorruptDocumentError(
            "The image could not be opened; it appears damaged.",
            remediation="Re-scan or re-export the image and upload it again.",
        ) from exc

    empty_quality = assess_text("", min_chars=1, min_dict_ratio=1.0)
    for frame in range(1, frames + 1):
        _store_page(
            document_id,
            PageLayout(
                page_number=frame,
                width=float(width),
                height=float(height),
                unit="px",
                engine="none",
            ),
            source=PageExtractionSource.IMAGE_OCR,
            quality=empty_quality,
            needs_ocr=True,
        )
    return frames, list(range(1, frames + 1))


def run_text_stage(
    document_id: uuid.UUID,
    *,
    settings: Settings | None = None,
    storage: ObjectStorage | None = None,
    dispatch: Dispatch = enqueue,
) -> TextStageResult:
    active = settings or get_settings()
    store = storage or get_object_storage()

    with session_scope() as session:
        document = session.get(Document, document_id)
        if document is None:
            return TextStageResult(document_id=document_id, ran=False)
        if not claim_document(
            session,
            document_id,
            from_statuses=(DocumentStatus.QUEUED, DocumentStatus.EXTRACTING_TEXT),
            to_status=DocumentStatus.EXTRACTING_TEXT,
        ):
            return TextStageResult(document_id=document_id, ran=False)
        if document.processing_started_at is None:
            document.processing_started_at = utcnow()
        storage_key = document.storage_key
        mime_type = document.mime_type
        batch_id = document.batch_id

    workdir = Path(tempfile.mkdtemp(prefix="certex-text-"))
    try:
        local = workdir / "source"
        store.download_to_path(storage_key, local)
        kind = kind_for_mime(mime_type)

        if kind is FileKind.DOC:
            local = convert_doc_to_docx(local, workdir, settings=active)
            kind = FileKind.DOCX

        if kind is FileKind.PDF:
            page_count, ocr_pages = _read_pdf(local, document_id, active)
        elif kind is FileKind.DOCX:
            page_count, ocr_pages = _read_docx(local, document_id, active)
        elif kind is FileKind.IMAGE:
            page_count, ocr_pages = _read_image(local, document_id)
        else:
            raise UnsupportedMediaTypeError(f"Files of type {mime_type} cannot be processed.")
    except AppError as exc:
        with session_scope() as session:
            fail_document(session, document_id, code=exc.code.value, message=str(exc))
            refresh_batch_progress(session, batch_id)
        return TextStageResult(document_id=document_id, ran=True, failed_code=exc.code.value)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    next_tasks: list[tuple[str, dict[str, TaskArgument]]]
    with session_scope() as session:
        document = session.get(Document, document_id)
        if document is None:  # pragma: no cover - deleted mid-stage
            return TextStageResult(document_id=document_id, ran=True)
        document.page_count = page_count
        if ocr_pages:
            claim_document(
                session,
                document_id,
                from_statuses=(DocumentStatus.EXTRACTING_TEXT,),
                to_status=DocumentStatus.OCR,
            )
            next_tasks = [
                (TASK_OCR_PAGE, {"document_id": str(document_id), "page_number": page})
                for page in ocr_pages
            ]
        else:
            claim_document(
                session,
                document_id,
                from_statuses=(DocumentStatus.EXTRACTING_TEXT,),
                to_status=DocumentStatus.SPLITTING,
            )
            next_tasks = [(TASK_SPLIT_DOCUMENT, {"document_id": str(document_id)})]

    logger.info(
        "pipeline.text_extracted",
        document_id=str(document_id),
        page_count=page_count,
        count=len(ocr_pages),
    )
    for task_name, kwargs in next_tasks:
        dispatch(task_name, kwargs)
    return TextStageResult(
        document_id=document_id, ran=True, page_count=page_count, ocr_pages=ocr_pages
    )
