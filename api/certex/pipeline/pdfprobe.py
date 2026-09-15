"""Cheap structural inspection of a PDF.

Runs at ingest so that the three failures a records office actually hits -
password protection, a truncated export, and a zero-page file - are reported
immediately with a specific message, rather than surfacing three stages later as
"extraction produced nothing".

Reads only the cross-reference table and page tree; it never rasterises or pulls
text, so probing a 500-page scan costs milliseconds.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from pathlib import Path
from typing import IO

from pypdf import PdfReader
from pypdf.errors import DependencyError, EmptyFileError, PdfReadError, PdfStreamError

from certex.core.errors import CorruptDocumentError, EncryptedDocumentError
from certex.logging_setup import get_logger, safe_error

__all__ = ["PdfProbe", "PdfProblem", "probe_pdf", "raise_for_probe"]

logger = get_logger(__name__)


class PdfProblem(str, enum.Enum):
    NONE = "none"
    ENCRYPTED = "encrypted"
    """Password protected and no usable password was supplied."""

    CORRUPT = "corrupt"
    EMPTY = "empty"
    """Parsed successfully but contains zero pages."""

    UNSUPPORTED_ENCRYPTION = "unsupported_encryption"
    """Uses an algorithm pypdf cannot handle even with the right password."""


@dataclass(frozen=True, slots=True)
class PdfProbe:
    page_count: int
    is_encrypted: bool
    problem: PdfProblem
    detail: str | None = None

    @property
    def is_readable(self) -> bool:
        return self.problem is PdfProblem.NONE


def probe_pdf(source: Path | IO[bytes], *, password: str | None = None) -> PdfProbe:
    """Inspect a PDF without raising. Returns a verdict the caller can act on."""
    try:
        reader = PdfReader(source, strict=False)
    except EmptyFileError as exc:
        return PdfProbe(0, False, PdfProblem.EMPTY, safe_error(exc))
    except (PdfReadError, PdfStreamError, OSError, ValueError) as exc:
        return PdfProbe(0, False, PdfProblem.CORRUPT, safe_error(exc))

    encrypted = bool(getattr(reader, "is_encrypted", False))

    if encrypted:
        try:
            # An empty password is extremely common: many systems encrypt purely
            # to set permissions, leaving the document openable by anyone.
            outcome = reader.decrypt(password or "")
        except DependencyError as exc:
            return PdfProbe(0, True, PdfProblem.UNSUPPORTED_ENCRYPTION, safe_error(exc))
        except (PdfReadError, NotImplementedError, ValueError) as exc:
            return PdfProbe(0, True, PdfProblem.ENCRYPTED, safe_error(exc))

        if not outcome:
            return PdfProbe(0, True, PdfProblem.ENCRYPTED, "password rejected")

    try:
        page_count = len(reader.pages)
    except (PdfReadError, PdfStreamError, OSError, ValueError, RecursionError) as exc:
        # A damaged page tree parses far enough to open but not to enumerate.
        return PdfProbe(0, encrypted, PdfProblem.CORRUPT, safe_error(exc))

    if page_count <= 0:
        return PdfProbe(0, encrypted, PdfProblem.EMPTY, "the document contains no pages")

    return PdfProbe(page_count, encrypted, PdfProblem.NONE)


def raise_for_probe(probe: PdfProbe, *, filename_known: bool = True) -> None:
    """Translate a probe verdict into the caller-facing error, or return."""
    if probe.is_readable:
        return

    where = "This PDF" if filename_known else "The PDF"

    if probe.problem is PdfProblem.ENCRYPTED:
        raise EncryptedDocumentError(
            f"{where} is password protected and could not be opened.",
            remediation=(
                "Re-upload the batch with the document password supplied in the "
                "batch settings, or remove the protection before uploading."
            ),
        )

    if probe.problem is PdfProblem.UNSUPPORTED_ENCRYPTION:
        raise EncryptedDocumentError(
            f"{where} uses an encryption scheme this system cannot open.",
            title="Unsupported PDF encryption",
            remediation=(
                "Open it in a PDF reader and re-save without protection, then upload the copy."
            ),
        )

    if probe.problem is PdfProblem.EMPTY:
        raise CorruptDocumentError(
            f"{where} contains no pages.",
            title="Document has no pages",
            remediation=(
                "The file may have been exported incorrectly. Re-export it from "
                "the source system and upload again."
            ),
        )

    raise CorruptDocumentError(
        f"{where} could not be parsed; it appears truncated or damaged.",
        remediation=(
            "Re-export the document from the source system and upload again. If "
            "it opens in a PDF reader, try re-saving it first."
        ),
    )
