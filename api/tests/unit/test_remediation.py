"""Stored error codes resolve to the "what to do next" sentence of their error class."""

from __future__ import annotations

import pytest

import certex.pipeline.safety  # noqa: F401 - registers the archive and virus error classes
from certex.core.errors import (
    ConversionUnavailableError,
    CorruptDocumentError,
    EncryptedDocumentError,
    remediation_for,
)
from certex.schemas.batches import DocumentSummary

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "error_class",
    [EncryptedDocumentError, CorruptDocumentError, ConversionUnavailableError],
)
def test_known_codes_resolve_to_their_class_remediation(error_class: type) -> None:
    assert remediation_for(error_class.code.value) == error_class.remediation


def test_error_classes_from_other_modules_are_found() -> None:
    assert remediation_for("archive_suspicious")


@pytest.mark.parametrize("code", [None, "", "not_a_real_code"])
def test_unknown_or_missing_codes_resolve_to_none(code: str | None) -> None:
    assert remediation_for(code) is None


def test_document_summary_derives_remediation_from_its_code() -> None:
    summary = DocumentSummary.model_validate(
        {
            "id": "00000000-0000-4000-8000-000000000001",
            "original_filename": "scan.pdf",
            "mime_type": "application/pdf",
            "byte_size": 10,
            "sha256": "",
            "status": "FAILED",
            "error_code": "document_encrypted",
            "error_message": "This PDF is password protected and could not be opened.",
            "created_at": "2026-09-16T00:00:00Z",
        }
    )
    assert summary.remediation == EncryptedDocumentError.remediation
