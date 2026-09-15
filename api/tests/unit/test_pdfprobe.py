"""PDF structural probing.

Every case is a real generated PDF, because the failures being detected -
encryption, truncation, an empty page tree - are properties of the file format,
not of a mock.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from certex.core.errors import CorruptDocumentError, EncryptedDocumentError
from certex.pipeline.pdfprobe import PdfProblem, probe_pdf, raise_for_probe
from tests.fixtures.builders import (
    build_corrupt_pdf,
    build_encrypted_pdf,
    build_multi_certificate_pdf,
    build_text_pdf,
    build_zero_page_pdf,
)

pytestmark = pytest.mark.unit


class TestReadablePdfs:
    def test_single_page_certificate(self, tmp_path: Path) -> None:
        probe = probe_pdf(build_text_pdf(tmp_path / "a.pdf"))
        assert probe.is_readable
        assert probe.page_count == 1
        assert probe.problem is PdfProblem.NONE
        assert not probe.is_encrypted

    @pytest.mark.parametrize("copies", [1, 5, 50])
    def test_page_count_is_accurate(self, tmp_path: Path, copies: int) -> None:
        path = build_multi_certificate_pdf(tmp_path / "m.pdf", copies=copies)
        assert probe_pdf(path).page_count == copies

    def test_accepts_a_file_handle(self, tmp_path: Path) -> None:
        path = build_text_pdf(tmp_path / "a.pdf")
        with path.open("rb") as handle:
            assert probe_pdf(handle).page_count == 1

    def test_raise_for_probe_is_silent_when_readable(self, tmp_path: Path) -> None:
        raise_for_probe(probe_pdf(build_text_pdf(tmp_path / "a.pdf")))


class TestEncrypted:
    def test_detected_without_a_password(self, tmp_path: Path) -> None:
        probe = probe_pdf(build_encrypted_pdf(tmp_path / "e.pdf", password="letmein"))
        assert probe.is_encrypted
        assert probe.problem is PdfProblem.ENCRYPTED
        assert not probe.is_readable

    def test_opens_with_the_right_password(self, tmp_path: Path) -> None:
        path = build_encrypted_pdf(tmp_path / "e.pdf", password="letmein")
        probe = probe_pdf(path, password="letmein")
        assert probe.is_readable
        assert probe.page_count == 1
        # Still reported as encrypted - the batch should record that fact.
        assert probe.is_encrypted

    def test_wrong_password_is_rejected(self, tmp_path: Path) -> None:
        path = build_encrypted_pdf(tmp_path / "e.pdf", password="letmein")
        assert probe_pdf(path, password="wrong").problem is PdfProblem.ENCRYPTED

    def test_error_tells_the_user_what_to_do(self, tmp_path: Path) -> None:
        probe = probe_pdf(build_encrypted_pdf(tmp_path / "e.pdf"))
        with pytest.raises(EncryptedDocumentError) as caught:
            raise_for_probe(probe)

        error = caught.value
        assert error.status == 422
        assert error.code.value == "document_encrypted"
        assert "password" in (error.remediation or "").lower()


class TestUnreadable:
    def test_truncated_file(self, tmp_path: Path) -> None:
        probe = probe_pdf(build_corrupt_pdf(tmp_path / "c.pdf"))
        assert probe.problem is PdfProblem.CORRUPT
        assert not probe.is_readable

    def test_truncated_file_raises_with_guidance(self, tmp_path: Path) -> None:
        with pytest.raises(CorruptDocumentError) as caught:
            raise_for_probe(probe_pdf(build_corrupt_pdf(tmp_path / "c.pdf")))
        assert "Re-export" in (caught.value.remediation or "")

    def test_zero_page_document(self, tmp_path: Path) -> None:
        probe = probe_pdf(build_zero_page_pdf(tmp_path / "z.pdf"))
        assert probe.problem is PdfProblem.EMPTY
        assert probe.page_count == 0

    def test_zero_page_document_raises_its_own_error(self, tmp_path: Path) -> None:
        with pytest.raises(CorruptDocumentError) as caught:
            raise_for_probe(probe_pdf(build_zero_page_pdf(tmp_path / "z.pdf")))
        assert caught.value.title == "Document has no pages"

    def test_empty_file(self, tmp_path: Path) -> None:
        path = tmp_path / "empty.pdf"
        path.write_bytes(b"")
        assert probe_pdf(path).problem in {PdfProblem.EMPTY, PdfProblem.CORRUPT}

    def test_not_a_pdf_at_all(self, tmp_path: Path) -> None:
        path = tmp_path / "nope.pdf"
        path.write_bytes(b"this is plain text pretending to be a document" * 40)
        assert not probe_pdf(path).is_readable

    def test_probe_never_raises(self, tmp_path: Path) -> None:
        """The probe reports; only raise_for_probe decides to fail the request."""
        for payload in (b"", b"%PDF", b"\x00\x01\x02", b"%PDF-1.4\ntruncated"):
            path = tmp_path / "x.pdf"
            path.write_bytes(payload)
            assert probe_pdf(path) is not None
