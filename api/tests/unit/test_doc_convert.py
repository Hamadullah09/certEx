"""Converting legacy binary Word (.doc) through LibreOffice.

No Python library reads a .doc, so the pipeline shells out to ``soffice``. Two things
matter and neither needs LibreOffice installed to test: a deployment without it must
say so in words an operator can act on, and the command line must be exactly what
LibreOffice needs - built from paths this code owns, never from user text.

The conversion is driven through a stub binary on PATH, which is how the real failure
modes (a non-zero exit, a silent run that writes nothing) are reproduced. One test
uses the real LibreOffice when it happens to be installed, marked ``soffice``.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from certex.config import Settings, get_settings
from certex.core.errors import ConversionUnavailableError, CorruptDocumentError
from certex.pipeline.text.doc_convert import convert_doc_to_docx, soffice_binary
from certex.pipeline.text.docx_reader import read_docx_pages
from tests.fixtures.builders import SAMPLES_BY_KEY, build_docx
from tests.fixtures.corpus import LEGACY_DOC

pytestmark = pytest.mark.unit

STUB_NAME = "certex-stub-soffice"
OLE2_MAGIC = bytes.fromhex("d0cf11e0a1b11ae1")
"""The first eight bytes of every binary Word document."""


def settings_with(command: str) -> Settings:
    return get_settings().model_copy(update={"soffice_cmd": command})


def stub_soffice(
    directory: Path,
    *,
    monkeypatch: pytest.MonkeyPatch,
    exit_code: int = 0,
    argv_log: Path | None = None,
    copy_from: Path | None = None,
    copy_to: Path | None = None,
) -> str:
    """Put a fake ``soffice`` on PATH and return the command name.

    The stub records the arguments it was given and, when asked, produces the output
    file a real conversion would produce.
    """
    if os.name == "nt":
        script = directory / f"{STUB_NAME}.cmd"
        body = ["@echo off"]
        if argv_log is not None:
            body.append(f'echo %* > "{argv_log}"')
        if copy_from is not None and copy_to is not None:
            body.append(f'copy /Y "{copy_from}" "{copy_to}" >nul')
        body.append(f"exit /b {exit_code}")
        script.write_text("\r\n".join(body) + "\r\n", encoding="utf-8")
    else:
        script = directory / STUB_NAME
        body = ["#!/bin/sh"]
        if argv_log is not None:
            body.append(f'echo "$@" > "{argv_log}"')
        if copy_from is not None and copy_to is not None:
            body.append(f'cp "{copy_from}" "{copy_to}"')
        body.append(f"exit {exit_code}")
        script.write_text("\n".join(body) + "\n", encoding="utf-8")
        script.chmod(script.stat().st_mode | stat.S_IEXEC)

    monkeypatch.setenv("PATH", f"{directory}{os.pathsep}{os.environ['PATH']}")
    return STUB_NAME


@pytest.fixture
def legacy_doc(tmp_path: Path) -> Path:
    """A stand-in .doc: the converter only checks that the file exists, and copies it."""
    path = tmp_path / "certificate.doc"
    path.write_bytes(OLE2_MAGIC + bytes(512))
    return path


@pytest.fixture
def binaries(tmp_path: Path) -> Path:
    """Directory for the stub binary; stub_soffice prepends it to PATH."""
    path = tmp_path / "bin"
    path.mkdir()
    return path


@pytest.fixture
def workdir(tmp_path: Path) -> Path:
    """The per-document scratch directory the pipeline hands to the converter."""
    path = tmp_path / "work"
    path.mkdir()
    return path


class TestWithoutLibreOffice:
    def test_the_binary_is_reported_missing(self) -> None:
        assert soffice_binary(settings_with("certex-no-such-binary")) is None

    def test_conversion_says_so_instead_of_failing_obscurely(
        self, workdir: Path, legacy_doc: Path
    ) -> None:
        with pytest.raises(ConversionUnavailableError) as raised:
            convert_doc_to_docx(
                legacy_doc, workdir, settings=settings_with("certex-no-such-binary")
            )
        assert raised.value.code.value == "conversion_unavailable"
        assert raised.value.status == 422

    def test_the_operator_is_told_what_to_do(self, workdir: Path, legacy_doc: Path) -> None:
        with pytest.raises(ConversionUnavailableError) as raised:
            convert_doc_to_docx(
                legacy_doc, workdir, settings=settings_with("certex-no-such-binary")
            )
        remediation = str(raised.value.remediation)
        assert ".docx" in remediation and "Word" in remediation


class TestTheCommandLine:
    def test_conversion_returns_the_converted_file(
        self,
        tmp_path: Path,
        legacy_doc: Path,
        binaries: Path,
        workdir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        prepared = build_docx(tmp_path / "prepared.docx")
        command = stub_soffice(
            binaries,
            monkeypatch=monkeypatch,
            copy_from=prepared,
            copy_to=workdir / "converted" / "legacy.docx",
        )

        converted = convert_doc_to_docx(legacy_doc, workdir, settings=settings_with(command))
        assert converted.exists()
        assert converted.suffix == ".docx"
        lines = [line.text for line in read_docx_pages(converted)[0].lines]
        label, value = SAMPLES_BY_KEY["birth_lahore"].lines[0]
        assert any(label in line and value in line for line in lines)

    def test_libreoffice_is_told_the_filter_the_profile_and_the_output_directory(
        self,
        tmp_path: Path,
        legacy_doc: Path,
        binaries: Path,
        workdir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        prepared = build_docx(tmp_path / "prepared.docx")
        argv_log = tmp_path / "argv.txt"
        command = stub_soffice(
            binaries,
            monkeypatch=monkeypatch,
            argv_log=argv_log,
            copy_from=prepared,
            copy_to=workdir / "converted" / "legacy.docx",
        )

        convert_doc_to_docx(legacy_doc, workdir, settings=settings_with(command))

        argv = argv_log.read_text(encoding="utf-8")
        assert "--headless" in argv
        assert "--convert-to" in argv
        assert "MS Word 2007 XML" in argv
        assert "--outdir" in argv
        # Each conversion gets its own profile, or concurrent worker threads fight
        # over the default profile's lock and one of them fails.
        assert "-env:UserInstallation=" in argv
        assert "libreoffice-profile" in argv

    def test_the_input_is_staged_with_a_doc_extension(
        self,
        tmp_path: Path,
        legacy_doc: Path,
        binaries: Path,
        workdir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # LibreOffice picks its import filter from the extension, and a stored
        # object's key has none.
        prepared = build_docx(tmp_path / "prepared.docx")
        argv_log = tmp_path / "argv.txt"
        command = stub_soffice(
            binaries,
            monkeypatch=monkeypatch,
            argv_log=argv_log,
            copy_from=prepared,
            copy_to=workdir / "converted" / "legacy.docx",
        )
        keyless = tmp_path / "0f3c9a1e-no-extension"
        keyless.write_bytes(legacy_doc.read_bytes())

        convert_doc_to_docx(keyless, workdir, settings=settings_with(command))
        assert "legacy.doc" in argv_log.read_text(encoding="utf-8")


class TestFailedConversion:
    def test_a_non_zero_exit_is_reported_as_a_damaged_document(
        self, legacy_doc: Path, binaries: Path, workdir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        command = stub_soffice(binaries, monkeypatch=monkeypatch, exit_code=1)
        with pytest.raises(CorruptDocumentError) as raised:
            convert_doc_to_docx(legacy_doc, workdir, settings=settings_with(command))
        assert raised.value.code.value == "document_corrupt"
        assert "Word" in str(raised.value.remediation)

    def test_a_silent_run_that_produces_nothing_is_a_failure(
        self, legacy_doc: Path, binaries: Path, workdir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # LibreOffice sometimes exits 0 having written no output at all.
        command = stub_soffice(binaries, monkeypatch=monkeypatch, exit_code=0)
        with pytest.raises(CorruptDocumentError):
            convert_doc_to_docx(legacy_doc, workdir, settings=settings_with(command))


@pytest.mark.soffice
class TestWithRealLibreOffice:
    def test_the_checked_in_legacy_document_converts_and_reads(self, workdir: Path) -> None:
        settings = get_settings()
        if soffice_binary(settings) is None:
            pytest.skip("LibreOffice (soffice) is not installed on this machine")
        assert LEGACY_DOC.is_file(), "the checked-in .doc fixture is missing"

        converted = convert_doc_to_docx(LEGACY_DOC, workdir, settings=settings)
        lines = [line.text for page in read_docx_pages(converted) for line in page.lines]
        for label, value in SAMPLES_BY_KEY["birth_lahore"].lines[:5]:
            assert any(label in line and value in line for line in lines), label
