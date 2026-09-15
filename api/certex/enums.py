"""Domain enumerations shared by the ORM, the Pydantic schemas and the workers.

Every value is a stable string: these are persisted in the database and emitted in
CSV exports, so renaming one is a migration, not a refactor.
"""

from __future__ import annotations

import enum

__all__ = [
    "AuditAction",
    "BatchStatus",
    "BoundaryMethod",
    "CertificateType",
    "ClassificationMethod",
    "DocumentStatus",
    "ExportFormat",
    "ExtractionMethod",
    "OcrEngine",
    "PageExtractionSource",
    "ReviewStatus",
    "Sex",
    "UnitStatus",
    "UserRole",
    "ValidationFlag",
]


class StrEnum(str, enum.Enum):
    """String enum whose ``str()`` is the bare value (Python 3.11 compatible)."""

    def __str__(self) -> str:
        return str(self.value)


class UserRole(StrEnum):
    ADMIN = "ADMIN"
    """Settings, user management, hard deletes."""

    OPERATOR = "OPERATOR"
    """Upload, review, correct, export."""

    VIEWER = "VIEWER"
    """Read and export only."""

    @property
    def rank(self) -> int:
        return _ROLE_RANK[self]

    def satisfies(self, required: UserRole) -> bool:
        """True when this role is at least as privileged as ``required``."""
        return self.rank >= required.rank


_ROLE_RANK: dict[UserRole, int] = {
    UserRole.VIEWER: 0,
    UserRole.OPERATOR: 1,
    UserRole.ADMIN: 2,
}


class BatchStatus(StrEnum):
    CREATED = "CREATED"
    """Batch row exists; files may still be uploading."""

    UPLOADING = "UPLOADING"
    QUEUED = "QUEUED"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    COMPLETED_WITH_ERRORS = "COMPLETED_WITH_ERRORS"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"

    @property
    def is_terminal(self) -> bool:
        return self in _TERMINAL_BATCH_STATUSES


_TERMINAL_BATCH_STATUSES: frozenset[BatchStatus] = frozenset(
    {
        BatchStatus.COMPLETED,
        BatchStatus.COMPLETED_WITH_ERRORS,
        BatchStatus.FAILED,
        BatchStatus.CANCELLED,
    }
)


class DocumentStatus(StrEnum):
    """Per-document pipeline position. Ordered to match the stage sequence."""

    QUEUED = "QUEUED"
    INGESTED = "INGESTED"
    NORMALIZING = "NORMALIZING"
    SPLITTING = "SPLITTING"
    EXTRACTING_TEXT = "EXTRACTING_TEXT"
    OCR = "OCR"
    CLASSIFYING = "CLASSIFYING"
    EXTRACTING_FIELDS = "EXTRACTING_FIELDS"
    VALIDATING = "VALIDATING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    DUPLICATE = "DUPLICATE"
    """Content hash already seen in this workspace; prior extraction is reused."""

    SKIPPED = "SKIPPED"
    """Container file (e.g. a ZIP) whose children were processed instead."""

    @property
    def is_terminal(self) -> bool:
        return self in _TERMINAL_DOCUMENT_STATUSES


_TERMINAL_DOCUMENT_STATUSES: frozenset[DocumentStatus] = frozenset(
    {
        DocumentStatus.COMPLETED,
        DocumentStatus.FAILED,
        DocumentStatus.DUPLICATE,
        DocumentStatus.SKIPPED,
    }
)


class UnitStatus(StrEnum):
    """Per-certificate-unit pipeline position."""

    PENDING = "PENDING"
    TEXT_READY = "TEXT_READY"
    CLASSIFIED = "CLASSIFIED"
    EXTRACTED = "EXTRACTED"
    VALIDATED = "VALIDATED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class CertificateType(StrEnum):
    BIRTH = "BIRTH"
    MARRIAGE = "MARRIAGE"
    DEATH = "DEATH"
    OTHER = "OTHER"


class ReviewStatus(StrEnum):
    AUTO_APPROVED = "AUTO_APPROVED"
    """Row confidence >= threshold and zero validation flags."""

    NEEDS_REVIEW = "NEEDS_REVIEW"
    """Between the floor and the auto-approve threshold, or carrying any flag."""

    FAILED = "FAILED"
    """Below the floor, or extraction produced nothing at all."""

    MANUALLY_APPROVED = "MANUALLY_APPROVED"
    """A human reviewed and accepted the row."""

    @property
    def is_approved(self) -> bool:
        return self in (ReviewStatus.AUTO_APPROVED, ReviewStatus.MANUALLY_APPROVED)


class ExtractionMethod(StrEnum):
    """Provenance of a single field value. Ordered by merge priority."""

    TEMPLATE = "template"
    RULE = "rule"
    LLM = "llm"
    MANUAL = "manual"
    NONE = "none"
    """No layer produced a value for this field."""

    @property
    def priority(self) -> int:
        """Higher wins the merge. Manual always beats an automated source."""
        return _METHOD_PRIORITY[self]

    @property
    def prior_confidence(self) -> float:
        """Method-level confidence prior fed into the blended field score."""
        return _METHOD_PRIOR[self]


_METHOD_PRIORITY: dict[ExtractionMethod, int] = {
    ExtractionMethod.NONE: 0,
    ExtractionMethod.LLM: 1,
    ExtractionMethod.RULE: 2,
    ExtractionMethod.TEMPLATE: 3,
    ExtractionMethod.MANUAL: 4,
}

_METHOD_PRIOR: dict[ExtractionMethod, float] = {
    ExtractionMethod.NONE: 0.0,
    ExtractionMethod.LLM: 0.80,
    ExtractionMethod.RULE: 0.88,
    ExtractionMethod.TEMPLATE: 0.96,
    ExtractionMethod.MANUAL: 1.0,
}


class BoundaryMethod(StrEnum):
    """How a certificate unit's page range was decided."""

    SINGLE_DOCUMENT = "single_document"
    """Whole file is one certificate."""

    BOOKMARK = "bookmark"
    """PDF outline entries marked the boundaries."""

    CONTENT_HEADER = "content_header"
    """A recognised certificate header started a new unit."""

    SERIAL_NUMBER = "serial_number"
    """A new registration/certificate number appeared."""

    FIXED_STRIDE = "fixed_stride"
    """Fallback: modal page distance between detected headers."""

    UNIFORM_PAGE_COUNT = "uniform_page_count"
    """Structural signal: every certificate occupies the same page count."""

    MANUAL = "manual"
    """A human split or merged units in the review UI."""


class ClassificationMethod(StrEnum):
    KEYWORD = "keyword"
    LLM = "llm"
    TEMPLATE = "template"
    MANUAL = "manual"
    DEFAULT = "default"
    """Nothing scored above zero; fell back to OTHER."""


class PageExtractionSource(StrEnum):
    """Where a page's text came from."""

    NATIVE_PDF = "native_pdf"
    OCR = "ocr"
    DOCX = "docx"
    IMAGE_OCR = "image_ocr"
    NONE = "none"


class OcrEngine(StrEnum):
    TESSERACT = "tesseract"
    PADDLEOCR = "paddleocr"
    NONE = "none"


class Sex(StrEnum):
    """Canonical sex/gender values. The raw printed value is preserved alongside."""

    MALE = "M"
    FEMALE = "F"
    OTHER = "O"
    UNKNOWN = "U"


class ExportFormat(StrEnum):
    CSV = "csv"
    XLSX = "xlsx"
    JSON = "json"


class ValidationFlag(StrEnum):
    """Machine-readable validation failures appended to a row's flags array."""

    # -- completeness --
    MISSING_REQUIRED = "MISSING_REQUIRED"
    NO_FIELDS_EXTRACTED = "NO_FIELDS_EXTRACTED"

    # -- dates --
    DATE_AMBIGUOUS = "DATE_AMBIGUOUS"
    """Could be read as DD/MM or MM/DD and both are valid calendar dates."""

    DATE_UNPARSEABLE = "DATE_UNPARSEABLE"
    DATE_IMPOSSIBLE = "DATE_IMPOSSIBLE"
    DATE_IN_FUTURE = "DATE_IN_FUTURE"

    # -- cross-field consistency --
    DOD_BEFORE_DOB = "DOD_BEFORE_DOB"
    MARRIAGE_BEFORE_BIRTH = "MARRIAGE_BEFORE_BIRTH"
    REGISTRATION_BEFORE_EVENT = "REGISTRATION_BEFORE_EVENT"
    AGE_INCONSISTENT = "AGE_INCONSISTENT"
    ISSUE_BEFORE_REGISTRATION = "ISSUE_BEFORE_REGISTRATION"

    # -- identifiers --
    CNIC_INVALID = "CNIC_INVALID"
    CNIC_CHECKSUM_FAILED = "CNIC_CHECKSUM_FAILED"
    ID_FORMAT_UNKNOWN = "ID_FORMAT_UNKNOWN"
    DUPLICATE_ID_IN_ROW = "DUPLICATE_ID_IN_ROW"

    # -- enums / normalisation --
    SEX_UNRECOGNISED = "SEX_UNRECOGNISED"
    VALUE_TRUNCATED = "VALUE_TRUNCATED"

    # -- extraction quality --
    LOW_OCR_CONFIDENCE = "LOW_OCR_CONFIDENCE"
    VALUE_UNVERIFIED = "VALUE_UNVERIFIED"
    """LLM returned a value that could not be located in the source text."""

    LOW_FIELD_CONFIDENCE = "LOW_FIELD_CONFIDENCE"
    LOW_TYPE_CONFIDENCE = "LOW_TYPE_CONFIDENCE"
    LOW_BOUNDARY_CONFIDENCE = "LOW_BOUNDARY_CONFIDENCE"
    """The split that produced this unit was uncertain; verify the page range."""

    OCR_EMPTY = "OCR_EMPTY"
    TEXT_EXTRACTION_FAILED = "TEXT_EXTRACTION_FAILED"
    LLM_UNAVAILABLE = "LLM_UNAVAILABLE"
    UNKNOWN_CERTIFICATE_TYPE = "UNKNOWN_CERTIFICATE_TYPE"


class AuditAction(StrEnum):
    """Every entry recorded in ``audit_log``."""

    LOGIN_SUCCEEDED = "login.succeeded"
    LOGIN_FAILED = "login.failed"
    LOGOUT = "logout"
    TOKEN_REFRESHED = "token.refreshed"
    TOKEN_REUSE_DETECTED = "token.reuse_detected"

    BATCH_CREATED = "batch.created"
    BATCH_STARTED = "batch.started"
    BATCH_DELETED = "batch.deleted"
    BATCH_VIEWED = "batch.viewed"

    DOCUMENT_UPLOADED = "document.uploaded"
    DOCUMENT_DEDUPLICATED = "document.deduplicated"
    DOCUMENT_REPROCESSED = "document.reprocessed"
    DOCUMENT_PAGE_VIEWED = "document.page_viewed"

    ROWS_VIEWED = "rows.viewed"
    EXTRACTION_CORRECTED = "extraction.corrected"
    EXTRACTION_REPROCESSED = "extraction.reprocessed"
    EXTRACTION_APPROVED = "extraction.approved"

    UNIT_SPLIT = "unit.split"
    UNIT_MERGED = "unit.merged"

    TEMPLATE_CREATED = "template.created"
    TEMPLATE_DELETED = "template.deleted"

    EXPORT_REQUESTED = "export.requested"
    EXPORT_COMPLETED = "export.completed"

    SETTINGS_UPDATED = "settings.updated"
    USER_CREATED = "user.created"
    USER_UPDATED = "user.updated"
    USER_DELETED = "user.deleted"

    RETENTION_PURGED = "retention.purged"
