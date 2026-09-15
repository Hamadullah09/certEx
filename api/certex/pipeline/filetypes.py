"""Content-based file type detection.

The extension is never trusted. A records office receives files named
``scan.pdf`` that are actually TIFFs, ``.doc`` files that are really ``.docx``,
and occasionally something that is not a document at all. Routing on the
extension would send those down the wrong pipeline and fail confusingly three
stages later.

Detection order:

1. ``python-magic`` (libmagic) when it is importable. This is the primary path
   and what the specification calls for.
2. A signature sniffer built into this module. libmagic needs a native library
   that is present in the Linux images but not on a typical Windows developer
   machine, and the test suite has to run in both places. It reads the same magic
   bytes libmagic would; it is a narrower implementation, not a stub.

Both paths are reconciled against the container format: ``.docx`` and ``.zip``
are both ZIP archives, so a ZIP is opened far enough to tell an Office document
from an archive of scans.
"""

from __future__ import annotations

import enum
import sys
import zipfile
from dataclasses import dataclass
from functools import lru_cache
from io import BytesIO
from typing import Final

from certex.config import get_settings
from certex.core.errors import UnsupportedMediaTypeError
from certex.logging_setup import get_logger, safe_error

__all__ = [
    "DetectedType",
    "FileKind",
    "detect_file_kind",
    "detect_mime",
    "libmagic_available",
    "refine_zip_from_path",
    "require_supported",
    "sniff_mime",
]

logger = get_logger(__name__)

# Enough bytes to cover every signature below plus a ZIP local file header.
SNIFF_BYTES: Final = 8192


class FileKind(str, enum.Enum):
    """What the pipeline should do with a file, independent of its extension."""

    PDF = "pdf"
    DOCX = "docx"
    DOC = "doc"
    """Legacy binary Word. Converted to DOCX before text extraction."""

    IMAGE = "image"
    """Standalone scan. Routed straight to OCR."""

    ZIP = "zip"
    """Archive. Unpacked, then its members are ingested recursively."""

    UNSUPPORTED = "unsupported"


MIME_PDF: Final = "application/pdf"
MIME_DOCX: Final = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
MIME_DOC: Final = "application/msword"
MIME_ZIP: Final = "application/zip"

_IMAGE_MIMES: Final[frozenset[str]] = frozenset(
    {"image/jpeg", "image/png", "image/tiff", "image/bmp", "image/webp"}
)

_MIME_TO_KIND: Final[dict[str, FileKind]] = {
    MIME_PDF: FileKind.PDF,
    MIME_DOCX: FileKind.DOCX,
    MIME_DOC: FileKind.DOC,
    MIME_ZIP: FileKind.ZIP,
    "application/x-zip-compressed": FileKind.ZIP,
    "application/vnd.ms-office": FileKind.DOC,
    "application/x-ole-storage": FileKind.DOC,
    "application/CDFV2": FileKind.DOC,
    **{mime: FileKind.IMAGE for mime in _IMAGE_MIMES},
}

# Extensions shown to the user in the "what can I upload" error message, and used
# only as a tie-breaker inside ZIP archives - never as the primary signal.
SUPPORTED_EXTENSIONS: Final[frozenset[str]] = frozenset(
    {".pdf", ".docx", ".doc", ".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".webp", ".zip"}
)


@dataclass(frozen=True, slots=True)
class DetectedType:
    """Result of sniffing a file's contents."""

    mime: str
    kind: FileKind
    detector: str
    """``libmagic`` or ``signature`` - recorded so a surprising result is traceable."""

    @property
    def is_supported(self) -> bool:
        return self.kind is not FileKind.UNSUPPORTED

    @property
    def needs_ocr_only(self) -> bool:
        return self.kind is FileKind.IMAGE


# ---------------------------------------------------------------------------
# Signature sniffer
# ---------------------------------------------------------------------------
# (offset, magic bytes, mime). Ordered most specific first.
_SIGNATURES: Final[tuple[tuple[int, bytes, str], ...]] = (
    (0, b"%PDF-", MIME_PDF),
    (0, b"\x89PNG\r\n\x1a\n", "image/png"),
    (0, b"\xff\xd8\xff", "image/jpeg"),
    (0, b"II\x2a\x00", "image/tiff"),  # little-endian TIFF
    (0, b"MM\x00\x2a", "image/tiff"),  # big-endian TIFF
    (0, b"BM", "image/bmp"),
    # OLE2 compound document: legacy .doc, .xls, .ppt
    (0, b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", MIME_DOC),
    # ZIP container: .zip and every OOXML format. Refined by _refine_zip.
    (0, b"PK\x03\x04", MIME_ZIP),
    (0, b"PK\x05\x06", MIME_ZIP),  # empty archive
    (0, b"PK\x07\x08", MIME_ZIP),  # spanned archive
)


def sniff_mime(head: bytes) -> str | None:
    """Identify a MIME type from leading bytes. Returns None when unrecognised."""
    for offset, magic, mime in _SIGNATURES:
        if head[offset : offset + len(magic)] == magic:
            return mime

    # RIFF-based WebP carries its marker at offset 8.
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    return None


def _zip_member_names_from_prefix(payload: bytes, *, limit: int = 64) -> set[str]:
    """Read member names out of a ZIP prefix without the central directory.

    A ZIP's index sits at the *end* of the file, but sniffing only ever sees the
    first few kilobytes, so ``ZipFile`` cannot open it. Each member is however
    preceded by a local file header that carries its name, and OOXML writers put
    ``[Content_Types].xml`` and the ``word/`` parts first. Scanning for those
    headers recovers exactly the names needed to tell a Word document from an
    archive of scans.

    Local header layout: signature at +0, filename length at +26 (uint16 LE),
    extra length at +28, filename at +30.
    """
    names: set[str] = set()
    cursor = 0
    while len(names) < limit:
        index = payload.find(b"PK", cursor)
        if index < 0 or index + 30 > len(payload):
            break
        name_length = int.from_bytes(payload[index + 26 : index + 28], "little")
        start = index + 30
        if 0 < name_length <= 512 and start + name_length <= len(payload):
            names.add(payload[start : start + name_length].decode("utf-8", "replace"))
        cursor = index + 4
    return names


def _classify_ooxml(names: set[str]) -> str:
    """Decide what an OPC package is from its part names."""
    if not any(name == "[Content_Types].xml" for name in names):
        return MIME_ZIP
    if any(name.startswith("word/") for name in names):
        return MIME_DOCX
    return MIME_ZIP


def _refine_zip(payload: bytes) -> str:
    """Tell an OOXML document from a plain archive, given only a prefix."""
    try:
        with zipfile.ZipFile(BytesIO(payload)) as archive:
            names = set(archive.namelist())
    except (zipfile.BadZipFile, OSError, ValueError):
        # Expected for anything larger than the sniff window: the central
        # directory is past the end of what we were given.
        names = _zip_member_names_from_prefix(payload)

    return _classify_ooxml(names)


def refine_zip_from_path(path: str) -> str:
    """Authoritative refinement for a complete file on disk.

    Used at ingest, where the upload has already been spooled, so the real
    central directory is available and no guessing is required.
    """
    try:
        with zipfile.ZipFile(path) as archive:
            return _classify_ooxml(set(archive.namelist()))
    except (zipfile.BadZipFile, OSError, ValueError):
        return MIME_ZIP


@lru_cache(maxsize=1)
def libmagic_available() -> bool:
    """True when ``python-magic`` can be used in this process.

    The platform gate is checked *before* the import, deliberately. On Windows
    without the native library, ``import magic`` does not raise - it blocks while
    ctypes searches for a DLL that is not there, and a hung worker is far worse
    than a missing optimisation. A try/except cannot rescue a hang, so the import
    is never attempted unless the platform is known good or an operator has
    explicitly opted in with ``USE_LIBMAGIC=true``.

    On the Linux container images, where libmagic is installed, this returns True
    and libmagic is the primary detector exactly as specified.
    """
    configured = get_settings().use_libmagic
    if configured is False:
        return False

    if configured is None and sys.platform == "win32":
        logger.info("filetypes.libmagic_skipped", reason_code="win32_autodetect")
        return False

    try:
        import magic

        magic.from_buffer(b"%PDF-1.4\n", mime=True)
    except Exception as exc:  # noqa: BLE001 - any failure means "not usable here"
        logger.info("filetypes.libmagic_unavailable", error_type=safe_error(exc))
        return False
    return True


# Types libmagic returns when it recognised nothing useful. Seeing one of these
# is not an answer, so the signature sniffer gets a turn before we give up.
_INCONCLUSIVE_MIMES: Final[frozenset[str]] = frozenset(
    {
        "application/octet-stream",
        "application/x-empty",
        "binary",
        "data",
        "text/plain",
        "inode/x-empty",
    }
)


def detect_mime(head: bytes) -> tuple[str, str]:
    """Return ``(mime, detector)`` for the given leading bytes.

    libmagic runs first, as specified. Where it is inconclusive - which happens on
    short or unusual buffers, and returns ``application/octet-stream`` - the
    signature sniffer is consulted rather than the file being declared
    unsupported. The two are complementary: rejecting a valid PNG because
    libmagic wanted more context would be a worse outcome than either detector
    alone.
    """
    if not head:
        return "application/x-empty", "signature"

    mime: str | None = None
    detector = "signature"

    if libmagic_available():
        try:
            import magic

            candidate = str(magic.from_buffer(head, mime=True))
            if candidate and candidate not in _INCONCLUSIVE_MIMES:
                mime, detector = candidate, "libmagic"
        except Exception as exc:  # noqa: BLE001 - fall through to the sniffer
            logger.warning("filetypes.libmagic_failed", error_type=safe_error(exc))

    if mime is None:
        sniffed = sniff_mime(head)
        if sniffed is not None:
            mime, detector = sniffed, "signature"

    if mime is None:
        return "application/octet-stream", detector

    # libmagic reports every OOXML file and every archive as a ZIP unless it can
    # see the whole file, so both detectors get the same refinement.
    if mime in (MIME_ZIP, "application/x-zip-compressed"):
        mime = _refine_zip(head)

    return mime, detector


def detect_file_kind(
    head: bytes, *, filename: str | None = None, path: str | None = None
) -> DetectedType:
    """Classify a file from its content.

    ``filename`` is used only to disambiguate a generic OLE2 container, where the
    magic bytes are identical for Word, Excel and PowerPoint. Content still
    decides everything else.

    ``path`` is an optional complete copy of the file on disk. When supplied, a
    ZIP-family result is re-checked against the real archive index rather than the
    sniffed prefix, which is what reliably separates a ``.docx`` from an archive
    of scans once the file is larger than the sniff window.
    """
    mime, detector = detect_mime(head)

    if path is not None and mime in (MIME_ZIP, MIME_DOCX, "application/x-zip-compressed"):
        mime = refine_zip_from_path(path)
    kind = _MIME_TO_KIND.get(mime, FileKind.UNSUPPORTED)

    if kind is FileKind.DOC and filename:
        # An OLE2 container that is plainly a spreadsheet or a deck is not a
        # certificate document, and LibreOffice would happily convert it into
        # something the pipeline cannot read.
        lowered = filename.lower()
        if lowered.endswith((".xls", ".ppt", ".msg", ".vsd")):
            kind = FileKind.UNSUPPORTED

    return DetectedType(mime=mime, kind=kind, detector=detector)


def require_supported(detected: DetectedType, *, filename: str | None = None) -> None:
    """Raise :class:`UnsupportedMediaTypeError` for a type we cannot process."""
    if detected.is_supported:
        return
    raise UnsupportedMediaTypeError(
        f"This file is {detected.mime}, which is not a supported document type.",
        remediation=(
            "Upload PDF, DOCX, DOC, JPG, PNG, TIFF or a ZIP containing those. "
            "The type is detected from the file contents, so renaming the "
            "extension will not change this result."
        ),
    )
