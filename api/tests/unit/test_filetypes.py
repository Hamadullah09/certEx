"""Content-based type detection.

The extension is never trusted, so every case here feeds *bytes* and asserts on
what comes back. A few also assert the opposite direction: that a misleading
extension does not change the verdict.
"""

from __future__ import annotations

import zipfile
from io import BytesIO
from pathlib import Path

import pytest

from certex.core.errors import UnsupportedMediaTypeError
from certex.pipeline.filetypes import (
    MIME_DOCX,
    MIME_PDF,
    MIME_ZIP,
    FileKind,
    detect_file_kind,
    detect_mime,
    require_supported,
    sniff_mime,
)

pytestmark = pytest.mark.unit

PDF_HEAD = b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n1 0 obj\n<< /Type /Catalog >>"
PNG_HEAD = bytes.fromhex("89504e470d0a1a0a") + b"\x00\x00\x00\rIHDR" + b"\x00" * 32
JPEG_HEAD = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00" + b"\x00" * 32
TIFF_LE_HEAD = b"II\x2a\x00" + b"\x08\x00\x00\x00" + b"\x00" * 32
TIFF_BE_HEAD = b"MM\x00\x2a" + b"\x00\x00\x00\x08" + b"\x00" * 32
BMP_HEAD = b"BM" + b"\x00" * 40
OLE2_HEAD = bytes.fromhex("d0cf11e0a1b11ae1") + b"\x00" * 64
WEBP_HEAD = b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP" + b"\x00" * 32


class TestSniffMime:
    @pytest.mark.parametrize(
        ("head", "expected"),
        [
            (PDF_HEAD, MIME_PDF),
            (PNG_HEAD, "image/png"),
            (JPEG_HEAD, "image/jpeg"),
            (TIFF_LE_HEAD, "image/tiff"),
            (TIFF_BE_HEAD, "image/tiff"),
            (BMP_HEAD, "image/bmp"),
            (WEBP_HEAD, "image/webp"),
            (OLE2_HEAD, "application/msword"),
        ],
    )
    def test_recognises_signatures(self, head: bytes, expected: str) -> None:
        assert sniff_mime(head) == expected

    def test_returns_none_for_unknown(self) -> None:
        assert sniff_mime(b"this is just prose, no magic bytes here") is None

    def test_empty_input(self) -> None:
        assert sniff_mime(b"") is None


class TestDetectFileKind:
    @pytest.mark.parametrize(
        ("head", "kind"),
        [
            (PDF_HEAD, FileKind.PDF),
            (PNG_HEAD, FileKind.IMAGE),
            (JPEG_HEAD, FileKind.IMAGE),
            (TIFF_LE_HEAD, FileKind.IMAGE),
            (OLE2_HEAD, FileKind.DOC),
        ],
    )
    def test_maps_to_pipeline_kind(self, head: bytes, kind: FileKind) -> None:
        assert detect_file_kind(head).kind is kind

    def test_executable_is_unsupported(self) -> None:
        detected = detect_file_kind(b"MZ\x90\x00" + b"\x00" * 64)
        assert detected.kind is FileKind.UNSUPPORTED
        assert not detected.is_supported

    def test_plain_text_is_unsupported(self) -> None:
        assert detect_file_kind(b"Dear sir, please find attached" * 8).kind is FileKind.UNSUPPORTED

    def test_extension_does_not_override_content(self) -> None:
        """A PNG renamed to .pdf is still a PNG, and must go to the OCR path."""
        detected = detect_file_kind(PNG_HEAD, filename="definitely-a-certificate.pdf")
        assert detected.kind is FileKind.IMAGE

    def test_misleading_extension_on_unsupported_content(self) -> None:
        detected = detect_file_kind(b"MZ\x90\x00" + b"\x00" * 64, filename="scan.pdf")
        assert detected.kind is FileKind.UNSUPPORTED

    @pytest.mark.parametrize("name", ["accounts.xls", "deck.ppt", "mail.msg", "diagram.vsd"])
    def test_non_word_ole2_containers_are_rejected(self, name: str) -> None:
        """Word, Excel and PowerPoint share a container; only Word is a certificate."""
        assert detect_file_kind(OLE2_HEAD, filename=name).kind is FileKind.UNSUPPORTED

    def test_ole2_without_a_filename_is_treated_as_word(self) -> None:
        assert detect_file_kind(OLE2_HEAD).kind is FileKind.DOC

    def test_empty_file(self) -> None:
        detected = detect_file_kind(b"")
        assert detected.kind is FileKind.UNSUPPORTED
        assert detected.mime == "application/x-empty"

    def test_detector_is_recorded(self) -> None:
        assert detect_file_kind(PDF_HEAD).detector in {"libmagic", "signature"}


class TestZipRefinement:
    """`.docx` and `.zip` are both ZIP containers; content has to separate them."""

    def _docx_bytes(self) -> bytes:
        buffer = BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("[Content_Types].xml", "<Types/>")
            archive.writestr("word/document.xml", "<document/>")
            archive.writestr("_rels/.rels", "<Relationships/>")
        return buffer.getvalue()

    def _xlsx_bytes(self) -> bytes:
        buffer = BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("[Content_Types].xml", "<Types/>")
            archive.writestr("xl/workbook.xml", "<workbook/>")
        return buffer.getvalue()

    def _plain_zip_bytes(self) -> bytes:
        buffer = BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("scan-001.pdf", b"%PDF-1.4")
            archive.writestr("scan-002.pdf", b"%PDF-1.4")
        return buffer.getvalue()

    def test_docx_detected_as_word_document(self) -> None:
        detected = detect_file_kind(self._docx_bytes(), filename="cert.docx")
        assert detected.kind is FileKind.DOCX
        assert detected.mime == MIME_DOCX

    def test_plain_archive_detected_as_zip(self) -> None:
        detected = detect_file_kind(self._plain_zip_bytes(), filename="batch.zip")
        assert detected.kind is FileKind.ZIP
        assert detected.mime == MIME_ZIP

    def test_spreadsheet_ooxml_is_not_a_word_document(self) -> None:
        """An xlsx has the OPC marker but no word/ part, so it is not DOCX."""
        detected = detect_file_kind(self._xlsx_bytes(), filename="data.xlsx")
        assert detected.kind is not FileKind.DOCX

    def test_truncated_archive_falls_back_to_zip(self) -> None:
        """Only a prefix is ever sniffed, so an unreadable index is normal."""
        mime, _ = detect_mime(self._docx_bytes()[:40])
        assert mime in {MIME_ZIP, MIME_DOCX}


class TestRequireSupported:
    def test_passes_supported(self) -> None:
        require_supported(detect_file_kind(PDF_HEAD))

    def test_raises_with_actionable_guidance(self) -> None:
        with pytest.raises(UnsupportedMediaTypeError) as caught:
            require_supported(detect_file_kind(b"MZ\x90\x00" + b"\x00" * 64))

        error = caught.value
        assert error.status == 415
        assert error.code.value == "unsupported_media_type"
        # The message must pre-empt the obvious wrong fix.
        assert "renaming the extension" in (error.remediation or "")


class TestRealGeneratedFiles:
    """Detection against files this project actually produces."""

    def test_generated_pdf(self, tmp_path: Path) -> None:
        from tests.fixtures.builders import build_text_pdf

        path = build_text_pdf(tmp_path / "birth.pdf")
        assert detect_file_kind(path.read_bytes()[:8192]).kind is FileKind.PDF

    def test_generated_docx(self, tmp_path: Path) -> None:
        from tests.fixtures.builders import build_docx

        path = build_docx(tmp_path / "cert.docx")
        detected = detect_file_kind(path.read_bytes()[:8192], filename="cert.docx")
        assert detected.kind is FileKind.DOCX

    def test_generated_zip(self, tmp_path: Path) -> None:
        from tests.fixtures.builders import build_text_pdf, build_zip

        pdf = build_text_pdf(tmp_path / "a.pdf")
        path = build_zip(tmp_path / "b.zip", {"a.pdf": pdf.read_bytes()})
        assert detect_file_kind(path.read_bytes()[:8192]).kind is FileKind.ZIP

    def test_large_docx_is_not_mistaken_for_an_archive(self, tmp_path: Path) -> None:
        """Regression: a real DOCX exceeds the sniff window.

        A ZIP's central directory sits at the end of the file, so on anything
        larger than the sniffed prefix ``ZipFile`` cannot read the index at all.
        Detection has to fall back to local file headers, or every Word
        certificate would be routed as a generic archive.
        """
        from tests.fixtures.builders import build_docx

        path = build_docx(tmp_path / "big.docx")
        assert path.stat().st_size > 8192, "fixture no longer exercises the prefix path"

        prefix_only = detect_file_kind(path.read_bytes()[:8192], filename="big.docx")
        assert prefix_only.kind is FileKind.DOCX

        with_path = detect_file_kind(path.read_bytes()[:8192], filename="big.docx", path=str(path))
        assert with_path.kind is FileKind.DOCX

    def test_large_archive_of_scans_stays_an_archive(self, tmp_path: Path) -> None:
        from tests.fixtures.builders import build_text_pdf, build_zip

        pdf = build_text_pdf(tmp_path / "a.pdf").read_bytes()
        path = build_zip(tmp_path / "many.zip", {f"scan-{i:03d}.pdf": pdf for i in range(20)})
        assert path.stat().st_size > 8192

        assert detect_file_kind(path.read_bytes()[:8192]).kind is FileKind.ZIP
        assert detect_file_kind(b"", path=str(path)).kind is not FileKind.DOCX
