"""Stage 5a - turn a document's pages into certificate units.

A unit is one logical certificate: a page range inside a source file, and what will
eventually be one row of the CSV. Creating them is the pipeline's last chance to notice
that a file the office calls "the March returns" is really eighty certificates.

The whole stage is one transaction, and it begins by claiming the document. That makes
a redelivered task a no-op rather than a second set of units: the claim fails, nothing
is written, and the classify tasks the first run enqueued are still the right ones.
"""

from __future__ import annotations

import shutil
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path

import fitz
from sqlalchemy import delete, select

from certex.config import Settings
from certex.core.errors import AppError
from certex.db.models import CertificateUnit, Document, PageText
from certex.db.session import session_scope
from certex.enums import BoundaryMethod, DocumentStatus, UnitStatus
from certex.logging_setup import get_logger, safe_error
from certex.pipeline.dispatch import TASK_CLASSIFY_UNIT, Dispatch, enqueue
from certex.pipeline.filetypes import FileKind, kind_for_mime
from certex.pipeline.split.boundaries import (
    Boundaries,
    PageSummary,
    detect_boundaries,
    mean_pages_per_unit,
)
from certex.pipeline.state import claim_document, fail_document, refresh_batch_progress
from certex.storage.s3 import ObjectStorage, get_object_storage

__all__ = ["SplitStageResult", "run_split_stage"]

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class SplitStageResult:
    document_id: uuid.UUID
    ran: bool
    unit_count: int = 0
    method: BoundaryMethod | None = None
    confidence: float = 0.0
    failed_code: str | None = None


def _bookmark_pages(path: Path) -> list[int]:
    """The 1-based pages a PDF's outline points at.

    An outline is the producer stating where each certificate starts, which is better
    evidence than anything that can be inferred from the text. A file without one, or
    with an unreadable one, simply yields nothing.
    """
    try:
        with fitz.open(str(path)) as document:
            outline = document.get_toc(simple=True)
    except (RuntimeError, ValueError, fitz.mupdf.FzErrorBase) as exc:
        logger.info("split.outline_unreadable", error_type=safe_error(exc))
        return []
    pages: list[int] = []
    for entry in outline:
        if len(entry) >= 3 and isinstance(entry[2], int) and entry[2] > 0:
            pages.append(int(entry[2]))
    return pages


def _summaries(session_pages: list[PageText]) -> list[PageSummary]:
    return [PageSummary(page_number=page.page_number, text=page.raw_text) for page in session_pages]


def run_split_stage(
    document_id: uuid.UUID,
    *,
    settings: Settings | None = None,
    storage: ObjectStorage | None = None,
    dispatch: Dispatch = enqueue,
) -> SplitStageResult:
    """Split one document into certificate units and queue each for classification."""
    store = storage or get_object_storage()
    del settings  # the boundary cascade has no settings of its own yet

    with session_scope() as session:
        document = session.get(Document, document_id)
        if document is None or document.status is not DocumentStatus.SPLITTING:
            return SplitStageResult(document_id=document_id, ran=False)
        pages = list(
            session.scalars(
                select(PageText)
                .where(PageText.document_id == document_id)
                .order_by(PageText.page_number)
            )
        )
        summaries = _summaries(pages)
        batch_id = document.batch_id
        storage_key = document.storage_key
        kind = kind_for_mime(document.mime_type)

    bookmark_pages: list[int] = []
    if kind is FileKind.PDF and len(summaries) > 1:
        # Only a multi-page PDF can have certificates to separate, and only then is
        # downloading the file again worth it.
        workdir = Path(tempfile.mkdtemp(prefix="certex-split-"))
        try:
            local = workdir / "source"
            store.download_to_path(storage_key, local)
            bookmark_pages = _bookmark_pages(local)
        except AppError as exc:
            logger.info("split.bookmarks_unavailable", error_type=safe_error(exc))
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

    boundaries = detect_boundaries(summaries, bookmark_pages=bookmark_pages)

    try:
        unit_ids = _write_units(document_id, batch_id, boundaries)
    except AppError as exc:  # pragma: no cover - defensive; writes do not raise AppError
        with session_scope() as session:
            fail_document(session, document_id, code=exc.code.value, message=str(exc))
            refresh_batch_progress(session, batch_id)
        return SplitStageResult(document_id=document_id, ran=True, failed_code=exc.code.value)

    if unit_ids is None:
        return SplitStageResult(document_id=document_id, ran=False)

    with session_scope() as session:
        refresh_batch_progress(session, batch_id)

    logger.info(
        "pipeline.document_split",
        document_id=str(document_id),
        count=len(unit_ids),
        method=boundaries.method.value,
        pages_per_unit=round(mean_pages_per_unit(boundaries), 2),
    )
    for unit_id in unit_ids:
        dispatch(TASK_CLASSIFY_UNIT, {"unit_id": str(unit_id)})

    return SplitStageResult(
        document_id=document_id,
        ran=True,
        unit_count=len(unit_ids),
        method=boundaries.method,
        confidence=boundaries.confidence,
    )


def _write_units(
    document_id: uuid.UUID, batch_id: uuid.UUID, boundaries: Boundaries
) -> list[uuid.UUID] | None:
    """Replace the document's units in one transaction. None means another run won.

    The document is claimed in the same transaction as the write, so two deliveries of
    this task cannot both produce a set of units - and the losing one leaves nothing
    behind for a reviewer to notice.
    """
    with session_scope() as session:
        if not claim_document(
            session,
            document_id,
            from_statuses=(DocumentStatus.SPLITTING,),
            to_status=DocumentStatus.CLASSIFYING,
        ):
            return None
        session.execute(delete(CertificateUnit).where(CertificateUnit.document_id == document_id))
        units = [
            CertificateUnit(
                document_id=document_id,
                batch_id=batch_id,
                ordinal=ordinal,
                page_start=start,
                page_end=end,
                boundary_method=boundaries.method,
                boundary_confidence=boundaries.confidence,
                status=UnitStatus.TEXT_READY,
            )
            for ordinal, (start, end) in enumerate(boundaries.ranges)
        ]
        session.add_all(units)
        session.flush()
        return [unit.id for unit in units]
