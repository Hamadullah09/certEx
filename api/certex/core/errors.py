"""Application errors and their RFC 7807 representation.

Every failure the user can provoke has:

* a stable machine code (``code``) the frontend can branch on,
* a human title and detail, and
* a ``remediation`` sentence saying what to do next.

Section 12 of the specification requires the last one: an error that only says
"processing failed" is not actionable for someone holding 1,000 scans.
"""

from __future__ import annotations

import enum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "AppError",
    "BadRequestError",
    "BatchNotReadyError",
    "ConflictError",
    "ConversionUnavailableError",
    "CorruptDocumentError",
    "EncryptedDocumentError",
    "ErrorCode",
    "FieldError",
    "ForbiddenError",
    "NotFoundError",
    "OCRUnavailableError",
    "PayloadTooLargeError",
    "ProblemDetail",
    "RateLimitedError",
    "StorageUnavailableError",
    "UnauthorizedError",
    "UnsupportedMediaTypeError",
    "UploadIncompleteError",
    "ValidationFailedError",
    "remediation_for",
]

_PROBLEM_BASE_URI = "https://certextract.invalid/problems"


class ErrorCode(str, enum.Enum):
    """Stable machine-readable error identifiers."""

    # -- auth --
    INVALID_CREDENTIALS = "invalid_credentials"
    NOT_AUTHENTICATED = "not_authenticated"
    TOKEN_EXPIRED = "token_expired"
    TOKEN_INVALID = "token_invalid"
    TOKEN_REUSED = "token_reused"
    INSUFFICIENT_ROLE = "insufficient_role"
    ACCOUNT_DISABLED = "account_disabled"

    # -- generic --
    NOT_FOUND = "not_found"
    CONFLICT = "conflict"
    BAD_REQUEST = "bad_request"
    VALIDATION_FAILED = "validation_failed"
    RATE_LIMITED = "rate_limited"
    INTERNAL_ERROR = "internal_error"

    # -- upload / documents --
    FILE_TOO_LARGE = "file_too_large"
    BATCH_TOO_LARGE = "batch_too_large"
    TOO_MANY_FILES = "too_many_files"
    UNSUPPORTED_MEDIA_TYPE = "unsupported_media_type"
    DOCUMENT_ENCRYPTED = "document_encrypted"
    DOCUMENT_CORRUPT = "document_corrupt"
    DOCUMENT_EMPTY = "document_empty"
    ARCHIVE_TOO_DEEP = "archive_too_deep"
    ARCHIVE_SUSPICIOUS = "archive_suspicious"
    VIRUS_DETECTED = "virus_detected"
    UPLOAD_INCOMPLETE = "upload_incomplete"
    CHECKSUM_MISMATCH = "checksum_mismatch"

    # -- pipeline --
    NO_TEXT_EXTRACTED = "no_text_extracted"
    OCR_UNAVAILABLE = "ocr_unavailable"
    CONVERSION_UNAVAILABLE = "conversion_unavailable"
    BATCH_NOT_READY = "batch_not_ready"
    PROCESSING_TIMEOUT = "processing_timeout"

    # -- infrastructure --
    STORAGE_UNAVAILABLE = "storage_unavailable"
    DATABASE_UNAVAILABLE = "database_unavailable"


class FieldError(BaseModel):
    """One field-level validation failure inside a problem document."""

    model_config = ConfigDict(frozen=True)

    field: str = Field(description="Dotted path to the offending input field.")
    message: str
    code: str | None = None


class ProblemDetail(BaseModel):
    """RFC 7807 problem document, extended with ``code`` and ``remediation``."""

    model_config = ConfigDict(
        frozen=True,
        json_schema_extra={
            "example": {
                "type": f"{_PROBLEM_BASE_URI}/document_encrypted",
                "title": "Document is password protected",
                "status": 422,
                "detail": "This PDF is encrypted and cannot be read without its password.",
                "code": "document_encrypted",
                "remediation": (
                    "Re-upload the file with its password supplied, or remove the "
                    "protection before uploading."
                ),
                "instance": "/api/v1/batches/2f1a.../files",
            }
        },
    )

    type: str
    title: str
    status: int
    code: ErrorCode
    detail: str | None = None
    remediation: str | None = Field(
        default=None, description="What the caller should do next to resolve this."
    )
    instance: str | None = Field(default=None, description="Request path that produced the error.")
    errors: list[FieldError] | None = None
    retry_after_seconds: int | None = None
    request_id: str | None = None


class AppError(Exception):
    """Base class for every deliberate failure the API can return."""

    status: int = 500
    code: ErrorCode = ErrorCode.INTERNAL_ERROR
    title: str = "Internal server error"
    remediation: str | None = None

    def __init__(
        self,
        detail: str | None = None,
        *,
        title: str | None = None,
        remediation: str | None = None,
        errors: list[FieldError] | None = None,
        retry_after_seconds: int | None = None,
    ) -> None:
        self.detail = detail
        if title is not None:
            self.title = title
        if remediation is not None:
            self.remediation = remediation
        self.errors = errors
        self.retry_after_seconds = retry_after_seconds
        super().__init__(detail or self.title)

    def to_problem(
        self, *, instance: str | None = None, request_id: str | None = None
    ) -> ProblemDetail:
        return ProblemDetail(
            type=f"{_PROBLEM_BASE_URI}/{self.code.value}",
            title=self.title,
            status=self.status,
            code=self.code,
            detail=self.detail,
            remediation=self.remediation,
            instance=instance,
            errors=self.errors,
            retry_after_seconds=self.retry_after_seconds,
            request_id=request_id,
        )

    @classmethod
    def of(cls, detail: str) -> Self:
        return cls(detail)


# ---------------------------------------------------------------------------
# 4xx
# ---------------------------------------------------------------------------
class BadRequestError(AppError):
    status = 400
    code = ErrorCode.BAD_REQUEST
    title = "Malformed request"


class UnauthorizedError(AppError):
    status = 401
    code = ErrorCode.NOT_AUTHENTICATED
    title = "Authentication required"
    remediation = "Sign in again to continue."


class ForbiddenError(AppError):
    status = 403
    code = ErrorCode.INSUFFICIENT_ROLE
    title = "Not permitted"
    remediation = "Ask a workspace administrator to grant you the required role."


class NotFoundError(AppError):
    status = 404
    code = ErrorCode.NOT_FOUND
    title = "Resource not found"
    remediation = "Check the identifier, or refresh the list to see what still exists."


class ConflictError(AppError):
    status = 409
    code = ErrorCode.CONFLICT
    title = "Conflicting state"


class UploadIncompleteError(AppError):
    status = 409
    code = ErrorCode.UPLOAD_INCOMPLETE
    title = "Upload is incomplete"
    remediation = "Send the remaining chunks, then complete the upload."


class BatchNotReadyError(AppError):
    status = 409
    code = ErrorCode.BATCH_NOT_READY
    title = "Batch is not ready to process"
    remediation = "Upload at least one file and let every upload finish, then start again."


class PayloadTooLargeError(AppError):
    status = 413
    code = ErrorCode.FILE_TOO_LARGE
    title = "File too large"
    remediation = "Split the file, or raise MAX_FILE_BYTES for this deployment."


class UnsupportedMediaTypeError(AppError):
    status = 415
    code = ErrorCode.UNSUPPORTED_MEDIA_TYPE
    title = "Unsupported file type"
    remediation = (
        "Upload PDF, DOCX, DOC, JPG, PNG, TIFF or a ZIP containing those. "
        "The type is detected from file content, so renaming the extension will not help."
    )


class ValidationFailedError(AppError):
    status = 422
    code = ErrorCode.VALIDATION_FAILED
    title = "Request failed validation"
    remediation = "Correct the highlighted fields and try again."


class EncryptedDocumentError(AppError):
    status = 422
    code = ErrorCode.DOCUMENT_ENCRYPTED
    title = "Document is password protected"
    remediation = (
        "Supply the document password with the upload, or remove the protection before uploading."
    )


class CorruptDocumentError(AppError):
    status = 422
    code = ErrorCode.DOCUMENT_CORRUPT
    title = "Document could not be read"
    remediation = (
        "The file appears truncated or malformed. Re-export it from the source "
        "system and upload again."
    )


class RateLimitedError(AppError):
    status = 429
    code = ErrorCode.RATE_LIMITED
    title = "Too many requests"
    remediation = "Wait for the period given in Retry-After, then retry."


# ---------------------------------------------------------------------------
# 5xx
# ---------------------------------------------------------------------------
class StorageUnavailableError(AppError):
    status = 503
    code = ErrorCode.STORAGE_UNAVAILABLE
    title = "Document storage unavailable"
    remediation = (
        "Uploaded work is preserved. Retry in a moment; if it persists, check the "
        "object storage service."
    )


class OCRUnavailableError(AppError):
    status = 503
    code = ErrorCode.OCR_UNAVAILABLE
    title = "OCR engine unavailable"
    remediation = (
        "The tesseract binary could not be run. Scanned pages cannot be read until "
        "it is installed in the worker image."
    )


class ProcessingTimeoutError(AppError):
    status = 504
    code = ErrorCode.PROCESSING_TIMEOUT
    title = "Reading the document took too long"
    remediation = (
        "The page was stopped after the time limit. Re-scan it at a lower resolution, "
        "or split a very large file into smaller ones, and upload again."
    )


class ConversionUnavailableError(AppError):
    status = 422
    code = ErrorCode.CONVERSION_UNAVAILABLE
    title = "Legacy Word document cannot be converted"
    remediation = (
        "Open the file in Word and save it as .docx or PDF, then upload that copy. "
        "Converting .doc needs LibreOffice, which this deployment does not have."
    )


def remediation_for(code: str | None) -> str | None:
    """The default "what to do next" sentence for a stored error code.

    Documents persist only their ``error_code`` and message; the remediation is
    looked up from the error class that owns the code, so the advice shown for an
    old failure improves when the class's wording does. Error classes defined in
    other modules register themselves simply by subclassing :class:`AppError`.
    """
    if not code:
        return None
    pending: list[type[AppError]] = [AppError]
    while pending:
        error_class = pending.pop()
        pending.extend(error_class.__subclasses__())
        if error_class.code.value == code and error_class.remediation:
            return error_class.remediation
    return None
