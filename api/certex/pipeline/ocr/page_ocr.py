"""Reading one scanned page.

This is the OCR policy, kept apart from the database work in the stage so it can be
tested on images alone.

The policy exists because Tesseract is not one setting that suits every page:

* **Two segmentation modes, and the better one wins.** A certificate is a form. Mode 6
  reads a single uniform block well, mode 4 reads columns well, and which is right
  varies page by page - so both run and the stronger result is kept.
* **A second, different attempt when the first reads badly.** Faint or small scans come
  back with low confidence or with words that are in no dictionary. The retry renders
  the page at a higher resolution, binarises it differently, and asks for every
  configured language - because a bad first read can also mean the page was not in the
  script that orientation detection reported.
* **Confidence alone does not choose.** Tesseract is often confident about nonsense, so
  a candidate is scored on its mean confidence *and* on how much of what it read is
  real words in the configured languages.

What comes back is a layout in the same shape a text-layer page produces, so nothing
downstream needs to know whether a page was read or OCR'd.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final

from certex.config import Settings
from certex.logging_setup import get_logger
from certex.pipeline.ocr.cache import CachedOcr, OcrCache, page_digest
from certex.pipeline.ocr.preprocess import Preprocessed, PreprocessVariant, preprocess
from certex.pipeline.ocr.rasterize import RasterPage, encode_jpeg
from certex.pipeline.ocr.tesseract import (
    OcrOutcome,
    configured_languages,
    detect_orientation,
    language_for_script,
    run_tesseract,
)
from certex.pipeline.text.layout_builder import build_layout
from certex.pipeline.text.quality import assess_text
from certex.schemas.layout import PageLayout

__all__ = ["OCR_VERSION", "OcrPageResult", "RasterSource", "ocr_page"]

logger = get_logger(__name__)

OCR_VERSION: Final = 1
"""This policy's version. Part of the cache key, so a change here invalidates old results."""

_SEGMENTATION_MODES: Final = (6, 4)
"""Tesseract page segmentation: 6 = one uniform block, 4 = columns of variable size."""

_DICTIONARY_WEIGHT: Final = 40.0
"""How far real-word evidence can outweigh confidence when picking a candidate.

A candidate that is 100% dictionary words can beat one that is up to 40 confidence
points surer of itself - enough to reject confident nonsense, not enough to prefer a
page that read almost nothing.
"""

RasterSource = Callable[[int], RasterPage]
"""Renders the page at a given DPI. For an uploaded image the pixels are all there are,
so the same image comes back whatever DPI is asked for."""


@dataclass(frozen=True, slots=True)
class OcrPageResult:
    """What one page's OCR produced, and how."""

    layout: PageLayout
    mean_confidence: float | None
    language: str
    psm: int
    dpi: int
    rotation: float
    variant: str
    content_hash: str
    from_cache: bool
    page_image: bytes | None
    """JPEG of the straightened page for the review pane; None when served from cache."""


@dataclass(frozen=True, slots=True)
class _Attempt:
    outcome: OcrOutcome
    prepared: Preprocessed
    dpi: int
    score: float
    dict_hit_rate: float


def _score(outcome: OcrOutcome, *, settings: Settings) -> tuple[float, float]:
    """(score, dictionary hit rate) for one Tesseract run."""
    text = " ".join(word.text for word in outcome.words)
    quality = assess_text(
        text,
        min_chars=settings.text_quality_min_chars,
        min_dict_ratio=settings.text_quality_min_dict_ratio,
    )
    confidence = outcome.mean_confidence or 0.0
    if not outcome.words:
        return 0.0, 0.0
    return confidence + _DICTIONARY_WEIGHT * quality.dict_hit_rate, quality.dict_hit_rate


def _read(
    raster: RasterPage,
    *,
    variant: PreprocessVariant,
    quarter_turns: int,
    language: str,
    settings: Settings,
) -> _Attempt:
    """Preprocess one rendering and read it in every segmentation mode."""
    prepared = preprocess(
        raster.image,
        dpi=raster.dpi,
        variant=variant,
        quarter_turns=quarter_turns,
        upscale=settings.ocr_upscale_factor,
        upscale_width_threshold=settings.ocr_upscale_width_threshold,
    )
    attempts: list[_Attempt] = []
    for psm in _SEGMENTATION_MODES:
        outcome = run_tesseract(
            prepared.image,
            language=language,
            psm=psm,
            settings=settings,
            timeout=settings.ocr_timeout_seconds,
        )
        score, hit_rate = _score(outcome, settings=settings)
        attempts.append(
            _Attempt(
                outcome=outcome,
                prepared=prepared,
                dpi=raster.dpi,
                score=score,
                dict_hit_rate=hit_rate,
            )
        )
    return max(attempts, key=lambda attempt: attempt.score)


def _needs_another_attempt(attempt: _Attempt, *, settings: Settings) -> bool:
    confidence = attempt.outcome.mean_confidence or 0.0
    return (
        not attempt.outcome.words
        or confidence < settings.ocr_fallback_confidence
        or attempt.dict_hit_rate < settings.text_quality_min_dict_ratio
    )


def _layout_of(attempt: _Attempt, *, page_number: int) -> PageLayout:
    height, width = attempt.prepared.image.shape[:2]
    return build_layout(
        attempt.outcome.words,
        page_number=page_number,
        width=float(width),
        height=float(height),
        unit="px",
        engine="tesseract",
        engine_order=True,
    )


def _recipe(raster: RasterPage, *, settings: Settings) -> str:
    return (
        f"v={OCR_VERSION}|size={raster.width}x{raster.height}|dpi={raster.dpi}"
        f"|high_dpi={settings.ocr_high_dpi}|langs={settings.ocr_languages}"
        f"|psm={'+'.join(str(mode) for mode in _SEGMENTATION_MODES)}"
        f"|upscale={settings.ocr_upscale_factor}@{settings.ocr_upscale_width_threshold}"
        f"|floor={settings.ocr_fallback_confidence}"
    )


def ocr_page(
    source: RasterSource,
    *,
    page_number: int,
    workspace_id: uuid.UUID,
    settings: Settings,
    cache: OcrCache | None = None,
) -> OcrPageResult:
    """Read one page, with a cache lookup, orientation detection and one retry."""
    raster = source(settings.ocr_dpi)
    digest = page_digest(raster.image.tobytes(), recipe=_recipe(raster, settings=settings))

    caching = cache is not None and settings.ocr_cache_enabled
    if cache is not None and caching:
        cached = cache.get(workspace_id, digest)
        if cached is not None:
            logger.info("ocr.cache_hit", page_number=page_number)
            return OcrPageResult(
                layout=cached.layout,
                mean_confidence=cached.mean_confidence,
                language=cached.language,
                psm=cached.psm,
                dpi=cached.dpi,
                rotation=cached.rotation,
                variant=cached.variant,
                content_hash=digest,
                from_cache=True,
                page_image=None,
            )

    orientation = detect_orientation(
        raster.image, settings=settings, timeout=settings.ocr_timeout_seconds
    )
    quarter_turns = orientation.quarter_turns if orientation else 0
    language = language_for_script(orientation.script if orientation else None, settings=settings)

    best = _read(
        raster,
        variant="adaptive",
        quarter_turns=quarter_turns,
        language=language,
        settings=settings,
    )
    if _needs_another_attempt(best, settings=settings):
        logger.info(
            "ocr.retrying",
            page_number=page_number,
            confidence=best.outcome.mean_confidence,
            dict_hit_rate=best.dict_hit_rate,
        )
        retry_raster = source(settings.ocr_high_dpi)
        retry = _read(
            retry_raster,
            variant="otsu",
            quarter_turns=quarter_turns,
            language=configured_languages(settings),
            settings=settings,
        )
        if retry.score > best.score:
            best = retry

    layout = _layout_of(best, page_number=page_number)
    result = OcrPageResult(
        layout=layout,
        mean_confidence=best.outcome.mean_confidence,
        language=best.outcome.language,
        psm=best.outcome.psm,
        dpi=best.dpi,
        rotation=best.prepared.rotation,
        variant=best.prepared.variant,
        content_hash=digest,
        from_cache=False,
        page_image=encode_jpeg(best.prepared.display),
    )
    if cache is not None and caching:
        cache.put(
            workspace_id,
            digest,
            CachedOcr(
                layout=layout,
                mean_confidence=result.mean_confidence,
                language=result.language,
                psm=result.psm,
                dpi=result.dpi,
                rotation=result.rotation,
                variant=result.variant,
            ),
        )
    logger.info(
        "ocr.page_read",
        page_number=page_number,
        psm=result.psm,
        dpi=result.dpi,
        count=len(layout.words),
    )
    return result
