"""Driving Tesseract.

Tesseract is a subprocess, which shapes everything here:

* **Timeouts live in this code.** Celery's time limits are not available on every
  platform the worker runs on, so each Tesseract run carries its own deadline and a
  page that hangs the engine fails that page instead of the worker.
* **Only installed languages are requested.** Asking for a language whose data is
  missing makes Tesseract exit non-zero, so the requested set is intersected with what
  the binary reports and the substitution is logged.
* **Failures are told apart.** A missing binary is an infrastructure fault that will
  affect every scan; a page Tesseract refuses is a document fault. They get different
  errors so the operator is told the truth.

Page segmentation mode is not guessed. A certificate is a form: mode 6 (one uniform
block) reads its label/value rows well, mode 4 (variable-size columns) reads multi
column layouts well, and which wins varies page by page - so both are run and the more
confident result is kept. See :mod:`certex.pipeline.ocr.page_ocr`.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Final

import pytesseract
from PIL import Image
from pytesseract import Output

from certex.config import Settings
from certex.core.errors import OCRUnavailableError, ProcessingTimeoutError
from certex.logging_setup import get_logger, safe_error
from certex.pipeline.ocr.rasterize import GrayImage
from certex.pipeline.text.layout_builder import PositionedWord

__all__ = [
    "OcrOutcome",
    "Orientation",
    "available_languages",
    "configured_languages",
    "detect_orientation",
    "language_for_script",
    "resolve_binary",
    "run_tesseract",
    "tesseract_version",
]

logger = get_logger(__name__)

_ARABIC_SCRIPTS: Final = frozenset({"Arabic", "Persian", "Urdu"})
_LATIN_LANGUAGE: Final = "eng"
_ARABIC_LANGUAGE: Final = "urd"
_OSD_MIN_CONFIDENCE: Final = 1.0
"""Tesseract's own guidance: below about 1.0 its orientation guess is noise."""


@dataclass(frozen=True, slots=True)
class Orientation:
    """How far the page is turned, and which script Tesseract thinks it is in."""

    quarter_turns: int
    confidence: float
    script: str | None


@dataclass(frozen=True, slots=True)
class OcrOutcome:
    """One Tesseract run: its words, how sure it was, and how it was asked."""

    words: list[PositionedWord]
    mean_confidence: float | None
    psm: int
    language: str


def resolve_binary(settings: Settings) -> str | None:
    """The tesseract executable, from configuration or PATH, or None when absent."""
    configured = settings.tesseract_cmd
    if configured:
        candidate = str(configured)
        if os.path.isfile(candidate):
            return candidate
        return shutil.which(candidate)
    return shutil.which("tesseract")


def _prepare(settings: Settings) -> str:
    """Point pytesseract at the configured binary and language data."""
    binary = resolve_binary(settings)
    if binary is None:
        raise OCRUnavailableError(
            "The tesseract binary was not found, so scanned pages cannot be read."
        )
    pytesseract.pytesseract.tesseract_cmd = binary
    if settings.tessdata_prefix:
        # Tesseract finds its language data through the environment, and a worker
        # process may not have inherited it.
        os.environ["TESSDATA_PREFIX"] = str(settings.tessdata_prefix)
    return binary


@lru_cache(maxsize=4)
def _version_of(binary: str) -> str | None:
    try:
        completed = subprocess.run(  # noqa: S603 - path comes from configuration
            [binary, "--version"], capture_output=True, timeout=30, check=False, text=True
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("ocr.version_failed", error_type=safe_error(exc))
        return None
    first = (completed.stdout or completed.stderr or "").splitlines()
    return first[0].strip() if first else None


def tesseract_version(settings: Settings) -> str | None:
    """The engine's version string, for the health endpoint and logs."""
    binary = resolve_binary(settings)
    return _version_of(binary) if binary else None


@lru_cache(maxsize=4)
def _languages_of(binary: str, tessdata: str) -> frozenset[str]:
    environment = dict(os.environ)
    if tessdata:
        environment["TESSDATA_PREFIX"] = tessdata
    try:
        completed = subprocess.run(  # noqa: S603 - path comes from configuration
            [binary, "--list-langs"],
            capture_output=True,
            timeout=30,
            check=False,
            text=True,
            env=environment,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("ocr.list_langs_failed", error_type=safe_error(exc))
        return frozenset()
    lines = (completed.stdout or "").splitlines()
    return frozenset(line.strip() for line in lines[1:] if line.strip())


def available_languages(settings: Settings) -> frozenset[str]:
    """Language data the binary can actually load."""
    binary = resolve_binary(settings)
    if binary is None:
        return frozenset()
    return _languages_of(binary, str(settings.tessdata_prefix or ""))


def configured_languages(settings: Settings) -> str:
    """Every configured language this deployment can actually load."""
    installed = available_languages(settings)
    configured = [language for language in settings.ocr_language_list if language in installed]
    if configured:
        return "+".join(configured)
    # Nothing configured is installed: ask for English if it is there, otherwise
    # whatever is, and let the caller see the substitution in the page row.
    fallback = (
        _LATIN_LANGUAGE
        if _LATIN_LANGUAGE in installed
        else next(iter(sorted(installed)), _LATIN_LANGUAGE)
    )
    logger.warning("ocr.language_substituted", requested=settings.ocr_languages, used=fallback)
    return fallback


def language_for_script(script: str | None, *, settings: Settings) -> str:
    """The Tesseract language string for a page whose dominant script is ``script``.

    Asking for a language the page does not contain costs accuracy, and measurably:
    on a speckled English certificate, ``eng+urd`` mis-reads whole values as Urdu
    digits and letters that ``eng`` reads correctly. So the detected script narrows
    the request - English pages are read as English - and an Urdu page is read as
    ``urd+eng``, because a Pakistani certificate in Urdu still has English labels.

    When the script cannot be told - a page with too little text for orientation
    detection - every configured language is requested, which is the safe guess.
    """
    installed = available_languages(settings)
    configured = [language for language in settings.ocr_language_list if language in installed]
    if not configured:
        return configured_languages(settings)
    if script in _ARABIC_SCRIPTS and _ARABIC_LANGUAGE in configured:
        secondary = [item for item in configured if item != _ARABIC_LANGUAGE]
        return "+".join([_ARABIC_LANGUAGE, *secondary])
    if script == "Latin" and _LATIN_LANGUAGE in configured:
        return _LATIN_LANGUAGE
    return "+".join(configured)


def _as_pil(image: GrayImage) -> Image.Image:
    return Image.fromarray(image, mode="L")


def _run(call: str, action: Any, *, timeout: float) -> Any:
    """Call pytesseract, translating its failures into this system's errors."""
    try:
        return action()
    except pytesseract.TesseractNotFoundError as exc:
        raise OCRUnavailableError(
            "The tesseract binary could not be run, so scanned pages cannot be read."
        ) from exc
    except RuntimeError as exc:
        # pytesseract reports its own subprocess timeout as a RuntimeError.
        if "timeout" in str(exc).lower():
            raise ProcessingTimeoutError(
                f"Reading this page took longer than {timeout:.0f} seconds and was stopped."
            ) from exc
        logger.warning(f"ocr.{call}_failed", error_type=safe_error(exc))
        raise OCRUnavailableError("The OCR engine failed while reading this page.") from exc


_ROTATE_PATTERN: Final = re.compile(r"Rotate:\s*(\d+)")


def detect_orientation(
    image: GrayImage, *, settings: Settings, timeout: float
) -> Orientation | None:
    """Which way up the page is, or None when Tesseract cannot tell.

    A page with little text - a certificate that is mostly a seal and a photograph -
    gives Tesseract nothing to judge by, and it says so rather than guessing. That is
    not a failure: the page is then read as it arrived.
    """
    if "osd" not in available_languages(settings):
        # Orientation needs its own model. Without it the page is read as scanned.
        return None
    _prepare(settings)
    try:
        raw = _run(
            "osd",
            lambda: pytesseract.image_to_osd(
                _as_pil(image), output_type=Output.DICT, timeout=timeout
            ),
            timeout=timeout,
        )
    except OCRUnavailableError:
        return None
    if not isinstance(raw, dict):  # pragma: no cover - pytesseract contract
        return None
    try:
        rotate = int(raw.get("rotate", 0))
        confidence = float(raw.get("orientation_conf", 0.0))
    except (TypeError, ValueError):  # pragma: no cover - defensive
        return None
    script = raw.get("script")
    if confidence < _OSD_MIN_CONFIDENCE:
        return Orientation(quarter_turns=0, confidence=confidence, script=None)
    return Orientation(
        quarter_turns=(rotate // 90) % 4,
        confidence=confidence,
        script=str(script) if script else None,
    )


def run_tesseract(
    image: GrayImage, *, language: str, psm: int, settings: Settings, timeout: float
) -> OcrOutcome:
    """Read one page image, returning positioned words and the mean word confidence."""
    _prepare(settings)
    config = f"--psm {psm} --oem 3 -c preserve_interword_spaces=1"
    data = _run(
        "image_to_data",
        lambda: pytesseract.image_to_data(
            _as_pil(image),
            lang=language,
            config=config,
            output_type=Output.DICT,
            timeout=timeout,
        ),
        timeout=timeout,
    )
    words = _words_from(data)
    scores = [word.confidence for word in words if word.confidence is not None]
    return OcrOutcome(
        words=words,
        mean_confidence=round(sum(scores) / len(scores), 2) if scores else None,
        psm=psm,
        language=language,
    )


def _words_from(data: object) -> list[PositionedWord]:
    """Positioned words from Tesseract's tabular output.

    Tesseract reports every level of its layout tree; only the word level carries
    text. Its block/paragraph/line numbers are kept as the run identity, because
    within a line Tesseract emits words in reading order - including right to left for
    Urdu - and that order is better than any guess made from coordinates.
    """
    if not isinstance(data, dict):  # pragma: no cover - pytesseract contract
        return []
    texts = data.get("text") or []
    words: list[PositionedWord] = []
    for index, raw_text in enumerate(texts):
        text = str(raw_text).strip()
        if not text:
            continue
        try:
            confidence = float(data["conf"][index])
            left = float(data["left"][index])
            top = float(data["top"][index])
            width = float(data["width"][index])
            height = float(data["height"][index])
            line_key = (
                int(data["block_num"][index]),
                int(data["par_num"][index]),
                int(data["line_num"][index]),
            )
        except (KeyError, IndexError, TypeError, ValueError):  # pragma: no cover
            continue
        if confidence < 0:
            continue  # Tesseract marks structural rows with -1
        words.append(
            PositionedWord(
                text=text,
                x0=left,
                y0=top,
                x1=left + width,
                y1=top + height,
                confidence=max(0.0, min(100.0, confidence)),
                line_key=line_key,
                order=len(words),
            )
        )
    return words
