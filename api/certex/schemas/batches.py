"""Batch, document and upload schemas."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from certex.core.errors import remediation_for
from certex.enums import BatchStatus, CertificateType, DocumentStatus
from certex.pipeline.safety import sanitise_filename

__all__ = [
    "BatchCreateRequest",
    "BatchDetail",
    "BatchSettings",
    "BatchSummary",
    "DocumentSummary",
    "UploadCompleteRequest",
    "UploadInit",
    "UploadSessionState",
    "UploadedFile",
]

Probability = Annotated[float, Field(ge=0.0, le=1.0)]


class BatchSettings(BaseModel):
    """Per-batch processing options, chosen on the upload screen.

    Every field is optional; an unset value falls back to the workspace setting,
    and then to the deployment default. Storing only what the user actually chose
    means a later change to a workspace default still reaches existing batches.
    """

    model_config = ConfigDict(extra="forbid")

    expected_types: list[CertificateType] | None = Field(
        default=None,
        description=(
            "Certificate types expected in this batch. Narrows the classifier and "
            "flags anything outside the set. Null means accept any type."
        ),
    )
    ocr_languages: str | None = Field(
        default=None,
        description="Tesseract language packs, joined with '+', e.g. 'eng+urd'.",
        max_length=64,
    )
    confidence_auto_approve: Probability | None = None
    confidence_review_floor: Probability | None = None

    @field_validator("ocr_languages")
    @classmethod
    def _validate_languages(cls, value: str | None) -> str | None:
        if value is None:
            return None
        parts = [part.strip() for part in value.split("+") if part.strip()]
        if not parts:
            raise ValueError("Specify at least one OCR language, e.g. 'eng' or 'eng+urd'.")
        for part in parts:
            if not part.isalpha() or not 2 <= len(part) <= 8:
                raise ValueError(f"'{part}' is not a valid Tesseract language code.")
        return "+".join(parts)

    @model_validator(mode="after")
    def _thresholds_ordered(self) -> BatchSettings:
        floor, ceiling = self.confidence_review_floor, self.confidence_auto_approve
        if floor is not None and ceiling is not None and floor > ceiling:
            raise ValueError("The review floor must be at or below the auto-approve threshold.")
        return self


class BatchCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    settings: BatchSettings = Field(default_factory=BatchSettings)

    @field_validator("name")
    @classmethod
    def _clean_name(cls, value: str) -> str:
        cleaned = " ".join(value.split())
        if not cleaned:
            raise ValueError("Give the batch a name.")
        return cleaned


class BatchSummary(BaseModel):
    """Row in the batches dashboard."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    status: BatchStatus
    file_count: int
    unit_count: int
    processed_count: int
    failed_count: int
    duplicate_count: int
    total_bytes: int
    created_at: dt.datetime
    started_at: dt.datetime | None = None
    completed_at: dt.datetime | None = None
    created_by: uuid.UUID | None = None

    @property
    def progress_percent(self) -> float:
        if self.file_count <= 0:
            return 0.0
        done = self.processed_count + self.failed_count + self.duplicate_count
        return round(min(done / self.file_count, 1.0) * 100, 1)


class DocumentSummary(BaseModel):
    """One uploaded file and where the pipeline has taken it."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    original_filename: str
    mime_type: str
    byte_size: int
    sha256: str
    page_count: int | None = None
    status: DocumentStatus
    error_code: str | None = None
    error_message: str | None = None
    remediation: str | None = Field(
        default=None, description="What to do about a failure, derived from its error code."
    )
    is_duplicate_of: uuid.UUID | None = None
    parent_document_id: uuid.UUID | None = None
    archive_member_path: str | None = None
    is_encrypted: bool = False
    processing_started_at: dt.datetime | None = None
    processing_completed_at: dt.datetime | None = None
    created_at: dt.datetime
    updated_at: dt.datetime | None = None

    @model_validator(mode="after")
    def _derive_remediation(self) -> DocumentSummary:
        if self.remediation is None and self.error_code:
            self.remediation = remediation_for(self.error_code)
        return self


class BatchDetail(BatchSummary):
    """Batch plus the settings it was created with and aggregate counts."""

    model_config = ConfigDict(from_attributes=True)

    settings: BatchSettings = Field(default_factory=BatchSettings)
    error_message: str | None = None


class UploadedFile(BaseModel):
    """Result of accepting one file."""

    model_config = ConfigDict(frozen=True)

    document_id: uuid.UUID
    original_filename: str
    byte_size: int
    sha256: str
    mime_type: str
    status: DocumentStatus
    is_duplicate: bool = Field(
        default=False,
        description=(
            "These exact bytes were already ingested in this workspace. The prior "
            "extraction is reused and the file is not processed again."
        ),
    )
    duplicate_of: uuid.UUID | None = None
    extracted_from_archive: bool = False
    children: list[UploadedFile] = Field(
        default_factory=list,
        description="Documents unpacked from an uploaded archive.",
    )
    error_code: str | None = Field(
        default=None, description="Why the file was rejected, when status is FAILED."
    )
    error_message: str | None = None
    remediation: str | None = Field(
        default=None, description="What to do about the rejection, in one sentence."
    )


class UploadInit(BaseModel):
    """Announce a file before sending its chunks, so an upload can resume."""

    model_config = ConfigDict(extra="forbid")

    client_file_id: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9_.:-]+$",
        description=(
            "Stable, client-generated id for this file. Re-announcing the same id "
            "resumes the existing upload rather than starting a new one."
        ),
    )
    filename: str = Field(min_length=1, max_length=512)
    size: int = Field(ge=1, description="Total byte length the client will send.")
    content_type: str | None = Field(default=None, max_length=128)

    @field_validator("filename")
    @classmethod
    def _sanitise(cls, value: str) -> str:
        return sanitise_filename(value)


class UploadSessionState(BaseModel):
    """Where a resumable upload currently stands.

    The client resumes by sending the chunk that starts at ``received_bytes``.
    Every chunk except the last must be exactly ``chunk_size`` bytes.
    """

    model_config = ConfigDict(frozen=True)

    upload_id: uuid.UUID
    client_file_id: str
    filename: str
    declared_size: int
    received_bytes: int
    chunk_size: int
    is_complete: bool
    document_id: uuid.UUID | None = None
    expires_at: dt.datetime


class UploadCompleteRequest(BaseModel):
    """Finish a resumable upload once every byte has arrived."""

    model_config = ConfigDict(extra="forbid")

    password: str | None = Field(
        default=None,
        max_length=256,
        description=("Password for an encrypted PDF. Used once to open the file and never stored."),
    )
