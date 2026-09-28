"""Legacy binary Word (.doc) to .docx, through LibreOffice headless.

python-docx reads only the Open XML format, so a .doc is converted first and then
read like any other .docx. The conversion needs the ``soffice`` binary. It ships in
the worker image; a deployment without it reports a clear, specific error for
.doc files rather than failing obscurely, and every other format keeps working.

Each conversion runs with its own throwaway LibreOffice profile directory. Two
conversions sharing the default profile would contend for its lock, and one of
them would fail - with several worker threads that happens constantly.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from certex.config import Settings
from certex.core.errors import ConversionUnavailableError, CorruptDocumentError
from certex.logging_setup import get_logger

__all__ = ["convert_doc_to_docx", "soffice_binary"]

logger = get_logger(__name__)

_TIMEOUT_SECONDS = 180


def soffice_binary(settings: Settings) -> str | None:
    """Absolute path of the LibreOffice binary, or None when it is not installed."""
    return shutil.which(settings.soffice_cmd)


def convert_doc_to_docx(source: Path, workdir: Path, *, settings: Settings) -> Path:
    """Convert ``source`` (a .doc) into a .docx inside ``workdir`` and return its path."""
    binary = soffice_binary(settings)
    if binary is None:
        raise ConversionUnavailableError(
            "This is a legacy Word (.doc) file, and this deployment has no LibreOffice "
            "to convert it."
        )

    # LibreOffice picks its import filter from the extension, so the input must end
    # in .doc whatever name the stored object had.
    staged = workdir / "legacy.doc"
    shutil.copyfile(source, staged)
    profile = workdir / "libreoffice-profile"
    profile.mkdir(exist_ok=True)
    output_dir = workdir / "converted"
    output_dir.mkdir(exist_ok=True)

    command = [
        binary,
        f"-env:UserInstallation={profile.resolve().as_uri()}",
        "--headless",
        "--norestore",
        "--nolockcheck",
        "--convert-to",
        "docx:MS Word 2007 XML",
        "--outdir",
        str(output_dir),
        str(staged),
    ]
    try:
        # The binary comes from configuration and every argument is a path this
        # function built; no user-supplied text reaches the command line.
        completed = subprocess.run(  # noqa: S603
            command,
            capture_output=True,
            timeout=_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise CorruptDocumentError(
            "Converting the legacy Word file took too long and was stopped.",
            remediation="Open it in Word, save it as .docx or PDF, and upload that copy.",
        ) from exc

    converted = output_dir / "legacy.docx"
    if completed.returncode != 0 or not converted.exists():
        logger.warning("doc_convert.failed", status_code=completed.returncode)
        raise CorruptDocumentError(
            "The legacy Word file could not be converted; it may be damaged.",
            remediation="Open it in Word, save it as .docx or PDF, and upload that copy.",
        )
    return converted
