"""Upload safety: filename sanitisation, archive inspection, virus scanning.

None of the values produced here are ever used to build a storage key — keys come
from UUIDs. Sanitisation exists so that a filename is safe to *display*, to store
in a database column, and to write into a CSV cell, and so that an archive member
path cannot escape the directory it is extracted into.
"""

from __future__ import annotations

import posixpath
import re
import socket
import unicodedata
import zipfile
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import PurePosixPath, PureWindowsPath
from typing import Final

from certex.config import Settings, get_settings
from certex.core.errors import AppError, ErrorCode
from certex.logging_setup import get_logger, safe_error

__all__ = [
    "ArchiveInspection",
    "ArchiveMember",
    "ArchiveRejectedError",
    "VirusDetectedError",
    "inspect_archive",
    "sanitise_filename",
    "scan_for_viruses",
]

logger = get_logger(__name__)

MAX_FILENAME_LENGTH: Final = 200
_FALLBACK_NAME: Final = "document"

# Control characters, path separators and the characters Windows forbids.
_UNSAFE_CHARS: Final = re.compile(r'[\x00-\x1f\x7f<>:"/\\|?*]')
_COLLAPSE_DOTS: Final = re.compile(r"\.{2,}")
_COLLAPSE_SPACE: Final = re.compile(r"\s+")

# Device names Windows reserves regardless of extension.
_RESERVED_STEMS: Final[frozenset[str]] = frozenset(
    {"con", "prn", "aux", "nul"}
    | {f"com{n}" for n in range(1, 10)}
    | {f"lpt{n}" for n in range(1, 10)}
)


class ArchiveRejectedError(AppError):
    status = 422
    code = ErrorCode.ARCHIVE_SUSPICIOUS
    title = "Archive rejected"
    remediation = (
        "Extract the archive locally and upload the documents directly, or "
        "re-create it without nested archives and without extreme compression."
    )


class VirusDetectedError(AppError):
    status = 422
    code = ErrorCode.VIRUS_DETECTED
    title = "File rejected by virus scan"
    remediation = "Scan the file locally and upload a clean copy."


# ---------------------------------------------------------------------------
# Filenames
# ---------------------------------------------------------------------------
def sanitise_filename(raw: str, *, fallback: str = _FALLBACK_NAME) -> str:
    """Reduce a user-supplied filename to something safe to store and display.

    Handles, in order: Unicode normalisation, directory components (both POSIX and
    Windows, because uploads arrive from both), traversal segments, control and
    reserved characters, Windows device names, leading dashes and dots, and
    length. Never returns an empty string.

    Non-Latin scripts are preserved: a certificate named in Urdu keeps its name.
    The goal is safety, not ASCII.
    """
    if not raw:
        return fallback

    # NFC so that visually identical names compare equal, and so a combining
    # sequence cannot be used to smuggle a separator past the filter.
    text = unicodedata.normalize("NFC", raw).strip()

    # Take the final path component under both separator conventions. A browser
    # folder upload sends "batch/2024/scan.pdf"; a Windows client may send a
    # full "C:\Users\...\scan.pdf".
    text = PureWindowsPath(PurePosixPath(text).name).name

    text = _UNSAFE_CHARS.sub("_", text)
    text = _COLLAPSE_DOTS.sub(".", text)
    text = _COLLAPSE_SPACE.sub(" ", text).strip()

    # A leading dash reads as an option flag to any command line this name is
    # ever interpolated into; a leading dot hides the file.
    text = text.lstrip("-. ")

    if not text:
        return fallback

    stem, dot, extension = text.rpartition(".")
    if not dot:
        stem, extension = text, ""

    if stem.lower() in _RESERVED_STEMS:
        stem = f"{stem}_file"

    # Truncate the stem, never the extension: the extension is short and its loss
    # would be more confusing than a shortened name.
    extension = extension[:20]
    budget = MAX_FILENAME_LENGTH - (len(extension) + 1 if extension else 0)
    stem = stem[:budget].rstrip(" .") or fallback

    result = f"{stem}.{extension}" if extension else stem
    return result or fallback


# ---------------------------------------------------------------------------
# Archives
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class ArchiveMember:
    """One file inside an archive that passed inspection."""

    name: str
    """Sanitised display name."""

    path: str
    """Normalised, traversal-free path within the archive."""

    compressed_size: int
    uncompressed_size: int

    entry_name: str = ""
    """The raw central-directory name, so a consumer can open exactly this entry."""


@dataclass(frozen=True, slots=True)
class ArchiveInspection:
    """Verdict on an archive, produced without extracting anything."""

    members: list[ArchiveMember] = field(default_factory=list)
    total_uncompressed: int = 0
    total_compressed: int = 0
    skipped: list[str] = field(default_factory=list)
    """Reasons members were skipped - directories, traversal attempts, nesting."""

    @property
    def ratio(self) -> float:
        if self.total_compressed <= 0:
            return 0.0
        return self.total_uncompressed / self.total_compressed


def _is_traversal(name: str) -> bool:
    """True when an archive member path tries to escape the extraction root."""
    normalised = name.replace("\\", "/")
    if normalised.startswith("/") or normalised.startswith("~"):
        return True
    # A Windows drive letter is an absolute path too.
    if len(normalised) > 1 and normalised[1] == ":":
        return True
    resolved = posixpath.normpath(normalised)
    return resolved.startswith("../") or resolved == ".." or "/../" in f"/{resolved}"


def inspect_archive(
    payload: bytes | str,
    *,
    depth: int,
    settings: Settings | None = None,
) -> ArchiveInspection:
    """Inspect a ZIP without extracting it, rejecting bombs and traversal.

    Reads only the central directory, so a 10 GB declared expansion costs nothing
    to detect. Three independent limits apply, because any one alone is evadable:

    * **depth** - a nested archive beyond the configured limit is refused.
    * **total uncompressed size** - catches a large flat expansion.
    * **compression ratio** - catches the classic bomb, which is small on disk and
      enormous when expanded.
    """
    active = settings or get_settings()

    if depth > active.max_zip_depth:
        raise ArchiveRejectedError(
            f"Archive nesting exceeds the limit of {active.max_zip_depth}.",
            title="Archive nested too deeply",
            remediation=(
                f"Archives may be nested at most {active.max_zip_depth} deep. "
                "Flatten the archive and upload again."
            ),
        )

    # Accepts a path (a file already on disk) or bytes (an in-memory upload).
    source: str | BytesIO = payload if isinstance(payload, str) else BytesIO(payload)
    try:
        with zipfile.ZipFile(source) as archive:
            infos = archive.infolist()
    except zipfile.BadZipFile as exc:
        raise ArchiveRejectedError(
            "The archive is corrupt or truncated and could not be read.",
            title="Archive could not be read",
            remediation="Re-create the archive and upload it again.",
        ) from exc

    members: list[ArchiveMember] = []
    skipped: list[str] = []
    total_uncompressed = 0
    total_compressed = 0

    for info in infos:
        if info.is_dir():
            continue
        if _is_traversal(info.filename):
            skipped.append("path_traversal")
            logger.warning("archive.member_rejected", reason_code="path_traversal")
            continue

        total_uncompressed += info.file_size
        total_compressed += max(info.compress_size, 1)

        if total_uncompressed > active.max_zip_uncompressed_bytes:
            raise ArchiveRejectedError(
                "The archive expands to more than the permitted total size.",
                title="Archive too large when expanded",
                remediation=(
                    "Split the archive into smaller parts, or upload the documents directly."
                ),
            )

        safe_path = posixpath.normpath(info.filename.replace("\\", "/")).lstrip("./")
        members.append(
            ArchiveMember(
                name=sanitise_filename(posixpath.basename(safe_path)),
                path=safe_path,
                compressed_size=info.compress_size,
                uncompressed_size=info.file_size,
                entry_name=info.filename,
            )
        )

    inspection = ArchiveInspection(
        members=members,
        total_uncompressed=total_uncompressed,
        total_compressed=total_compressed,
        skipped=skipped,
    )

    # Ratio is only meaningful once there is enough data for it to mean anything;
    # a tiny well-compressed text file trivially exceeds any ratio.
    if total_uncompressed > 1_000_000 and inspection.ratio > active.max_zip_ratio:
        raise ArchiveRejectedError(
            f"The archive expands {inspection.ratio:.0f}x, which exceeds the "
            f"permitted ratio of {active.max_zip_ratio}x.",
            title="Archive looks like a decompression bomb",
        )

    logger.info(
        "archive.inspected",
        count=len(members),
        skipped_count=len(skipped),
        byte_size=total_uncompressed,
    )
    return inspection


# ---------------------------------------------------------------------------
# Virus scanning
# ---------------------------------------------------------------------------
_CLAMAV_CHUNK: Final = 8192


def scan_for_viruses(payload: bytes, *, settings: Settings | None = None) -> str | None:
    """Scan bytes with ClamAV over its INSTREAM protocol.

    Returns the signature name when infected, ``None`` when clean or when
    scanning is disabled or unreachable. An unreachable scanner logs a warning and
    lets the upload through: the specification treats ClamAV as optional, with
    strict MIME sniffing and archive limits as the enforced controls.
    """
    active = settings or get_settings()
    if not active.clamav_enabled:
        return None

    try:
        with socket.create_connection(
            (active.clamav_host, active.clamav_port),
            timeout=active.clamav_timeout_seconds,
        ) as sock:
            sock.settimeout(active.clamav_timeout_seconds)
            sock.sendall(b"zINSTREAM\x00")

            for start in range(0, len(payload), _CLAMAV_CHUNK):
                chunk = payload[start : start + _CLAMAV_CHUNK]
                sock.sendall(len(chunk).to_bytes(4, "big") + chunk)
            sock.sendall((0).to_bytes(4, "big"))

            response = b""
            while b"\x00" not in response and len(response) < 4096:
                received = sock.recv(4096)
                if not received:
                    break
                response += received
    except OSError as exc:
        logger.warning("clamav.unreachable", error_type=safe_error(exc))
        return None

    text = response.rstrip(b"\x00").decode("utf-8", "replace").strip()
    if text.endswith("OK"):
        return None
    if "FOUND" in text:
        # "stream: Eicar-Test-Signature FOUND"
        signature = text.split(":", 1)[-1].replace("FOUND", "").strip()
        logger.warning("clamav.infected", reason_code=signature[:80])
        return signature or "unknown"

    logger.warning("clamav.unexpected_response", reason_code=text[:80])
    return None
