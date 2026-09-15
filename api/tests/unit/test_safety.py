"""Filename sanitisation and archive-bomb defence."""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from certex.config import Settings
from certex.pipeline.safety import (
    MAX_FILENAME_LENGTH,
    ArchiveRejectedError,
    inspect_archive,
    sanitise_filename,
)

pytestmark = pytest.mark.unit


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {"secret_key": "s" * 48, "_env_file": None}
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


class TestSanitiseFilename:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("birth certificate.pdf", "birth certificate.pdf"),
            ("Certificate_2019-04.pdf", "Certificate_2019-04.pdf"),
            # Leading/trailing whitespace goes, runs collapse, and the stem is
            # right-stripped of " ." so the extension sits flush.
            ("  spaced  out .pdf ", "spaced out.pdf"),
        ],
    )
    def test_ordinary_names_survive(self, raw: str, expected: str) -> None:
        assert sanitise_filename(raw) == expected

    @pytest.mark.parametrize(
        "raw",
        [
            "../../etc/passwd",
            "../../../windows/win.ini",
            "..\\..\\windows\\win.ini",
            "/absolute/path/scan.pdf",
            "C:\\Windows\\System32\\cmd.exe",
            "batch/2024/scan.pdf",
        ],
    )
    def test_directory_components_are_stripped(self, raw: str) -> None:
        result = sanitise_filename(raw)
        assert "/" not in result
        assert "\\" not in result
        assert not result.startswith("..")

    def test_null_and_control_bytes_removed(self) -> None:
        result = sanitise_filename("a\x00b\x1fc\td.pdf")
        assert "\x00" not in result
        assert "\x1f" not in result
        assert "\t" not in result

    @pytest.mark.parametrize("stem", ["CON", "con", "PRN", "NUL", "COM1", "LPT9"])
    def test_windows_device_names_are_defused(self, stem: str) -> None:
        """A file literally named CON.pdf is unopenable on Windows."""
        result = sanitise_filename(f"{stem}.pdf")
        assert result.lower() != f"{stem.lower()}.pdf"
        assert result.lower().startswith(f"{stem.lower()}_file")

    def test_leading_dash_removed(self) -> None:
        """A leading dash reads as a flag to any command line it reaches."""
        assert not sanitise_filename("-rf important.pdf").startswith("-")

    def test_leading_dot_removed(self) -> None:
        assert not sanitise_filename(".hidden.pdf").startswith(".")

    @pytest.mark.parametrize("raw", ["", "   ", "...", "///", "..", "."])
    def test_degenerate_input_yields_a_usable_name(self, raw: str) -> None:
        assert sanitise_filename(raw) == "document"

    def test_length_is_capped(self) -> None:
        result = sanitise_filename("x" * 500 + ".pdf")
        assert len(result) <= MAX_FILENAME_LENGTH

    def test_extension_survives_truncation(self) -> None:
        """Truncating the extension instead of the stem would be more confusing."""
        assert sanitise_filename("y" * 500 + ".docx").endswith(".docx")

    def test_non_latin_names_are_preserved(self) -> None:
        """A certificate named in Urdu keeps its name; this is safety, not ASCII."""
        urdu = "\u0634\u0646\u0627\u062e\u062a\u06cc \u06a9\u0627\u0631\u0688.pdf"
        assert sanitise_filename(urdu) == urdu

    def test_unicode_is_normalised_to_nfc(self) -> None:
        decomposed = "e\u0301clair.pdf"
        assert sanitise_filename(decomposed) == "\u00e9clair.pdf"

    def test_result_is_never_empty(self) -> None:
        for raw in ("", ".", "..", "/", "\\", "   ", "\x00"):
            assert sanitise_filename(raw)


class TestInspectArchive:
    def test_lists_members(self, tmp_path: Path) -> None:
        archive = tmp_path / "ok.zip"
        with zipfile.ZipFile(archive, "w") as handle:
            handle.writestr("a/one.pdf", b"%PDF-1.4 one")
            handle.writestr("b/two.pdf", b"%PDF-1.4 two")
            handle.writestr("c/", b"")

        result = inspect_archive(str(archive), depth=0, settings=_settings())
        assert {member.name for member in result.members} == {"one.pdf", "two.pdf"}
        assert result.total_uncompressed > 0

    def test_traversal_members_are_skipped_not_extracted(self, tmp_path: Path) -> None:
        archive = tmp_path / "evil.zip"
        with zipfile.ZipFile(archive, "w") as handle:
            handle.writestr("../../etc/passwd", b"root:x:0:0")
            handle.writestr("safe.pdf", b"%PDF-1.4")

        result = inspect_archive(str(archive), depth=0, settings=_settings())
        assert [member.name for member in result.members] == ["safe.pdf"]
        assert "path_traversal" in result.skipped

    @pytest.mark.parametrize(
        "name",
        ["/etc/shadow", "C:/Windows/win.ini", "~/.ssh/id_rsa", "a/../../b.pdf"],
    )
    def test_absolute_and_escaping_paths_rejected(self, tmp_path: Path, name: str) -> None:
        archive = tmp_path / "evil.zip"
        with zipfile.ZipFile(archive, "w") as handle:
            handle.writestr(name, b"payload")

        result = inspect_archive(str(archive), depth=0, settings=_settings())
        assert result.members == []

    def test_depth_limit_enforced(self, tmp_path: Path) -> None:
        archive = tmp_path / "nested.zip"
        with zipfile.ZipFile(archive, "w") as handle:
            handle.writestr("x.pdf", b"%PDF-1.4")

        settings = _settings(max_zip_depth=2)
        assert inspect_archive(str(archive), depth=2, settings=settings)
        with pytest.raises(ArchiveRejectedError, match="nesting"):
            inspect_archive(str(archive), depth=3, settings=settings)

    def test_total_size_limit_enforced(self, tmp_path: Path) -> None:
        archive = tmp_path / "big.zip"
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as handle:
            handle.writestr("big.txt", b"0" * (8 * 1024 * 1024))

        settings = _settings(max_zip_uncompressed_bytes=1024 * 1024)
        with pytest.raises(ArchiveRejectedError, match="expands to more"):
            inspect_archive(str(archive), depth=0, settings=settings)

    def test_compression_ratio_limit_catches_a_bomb(self, tmp_path: Path) -> None:
        """A bomb is small on disk and enormous when expanded."""
        archive = tmp_path / "bomb.zip"
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as handle:
            handle.writestr("bomb.txt", b"0" * (32 * 1024 * 1024))

        assert archive.stat().st_size < 200 * 1024, "fixture is not actually a bomb"

        with pytest.raises(ArchiveRejectedError, match=r"expands \d+x") as caught:
            inspect_archive(str(archive), depth=0, settings=_settings(max_zip_ratio=50))

        # The operator-facing title names the problem; the detail quantifies it.
        assert caught.value.title == "Archive looks like a decompression bomb"
        assert caught.value.remediation

    def test_small_well_compressed_file_is_not_a_bomb(self, tmp_path: Path) -> None:
        """A tiny text file trivially exceeds any ratio; that is not an attack."""
        archive = tmp_path / "small.zip"
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as handle:
            handle.writestr("notes.txt", b"a" * 4096)

        result = inspect_archive(str(archive), depth=0, settings=_settings(max_zip_ratio=5))
        assert len(result.members) == 1

    def test_corrupt_archive_reports_clearly(self, tmp_path: Path) -> None:
        archive = tmp_path / "broken.zip"
        archive.write_bytes(b"PK\x03\x04 truncated nonsense")

        with pytest.raises(ArchiveRejectedError, match="corrupt or truncated"):
            inspect_archive(str(archive), depth=0, settings=_settings())

    def test_member_names_are_sanitised(self, tmp_path: Path) -> None:
        archive = tmp_path / "names.zip"
        with zipfile.ZipFile(archive, "w") as handle:
            handle.writestr("deep/folder/-weird  name.pdf", b"%PDF-1.4")

        result = inspect_archive(str(archive), depth=0, settings=_settings())
        assert result.members[0].name == "weird name.pdf"
        assert "/" not in result.members[0].name
