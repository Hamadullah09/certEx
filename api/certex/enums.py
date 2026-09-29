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
    "CertificateSource",
    "CertificateStatus",
    "CertificateType",
    "ClassificationMethod",
    "DocumentLinkKind",
    "DocumentStatus",
    "DuplicateStatus",
    "ExportFormat",
    "ExtractionMethod",
    "FieldRole",
    "ImportDuplicatePolicy",
    "ImportStatus",
    "OcrEngine",
    "PageExtractionSource",
    "ReviewStatus",
    "RevisionAction",
    "SchemaVersionStatus",
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

    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    """Processing finished but rows are waiting on a person."""

    ARCHIVED = "ARCHIVED"
    """Closed and hidden from the working list; records stay searchable."""

    @property
    def is_terminal(self) -> bool:
        return self in _TERMINAL_BATCH_STATUSES


_TERMINAL_BATCH_STATUSES: frozenset[BatchStatus] = frozenset(
    {
        BatchStatus.COMPLETED,
        BatchStatus.COMPLETED_WITH_ERRORS,
        BatchStatus.FAILED,
        BatchStatus.CANCELLED,
        BatchStatus.ARCHIVED,
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


class FieldRole(StrEnum):
    """What a field *means*, as opposed to what it is called.

    A dynamic schema lets an operator name their fields anything - ``name``,
    ``child_name``, ``نام`` - which leaves generic code with no way to know that
    one of them is the person the certificate is about. A role says so.

    Roles are what let one implementation serve every certificate type:

    * the identifier drives certificate-number indexing and duplicate detection;
    * the name and parent roles drive the denormalised search columns;
    * the date and age roles drive the cross-field checks (a death cannot precede
      a birth) without those rules naming a single hardcoded field.

    A field with no role is still extracted, exported and searched by key - it
    simply takes no part in the semantic rules.
    """

    NONE = "none"

    IDENTIFIER = "identifier"
    """The certificate number. Exactly one per schema, indexed and duplicate-checked."""

    SECONDARY_REFERENCE = "secondary_reference"
    """A registration or entry number, where it differs from the identifier."""

    SUBJECT_NAME = "subject_name"
    """The person the certificate is about: the child, the deceased."""

    PARTY_NAME = "party_name"
    """One of two equal parties, as on a marriage certificate."""

    FATHER_NAME = "father_name"
    MOTHER_NAME = "mother_name"
    SPOUSE_NAME = "spouse_name"

    BIRTH_DATE = "birth_date"
    DEATH_DATE = "death_date"
    MARRIAGE_DATE = "marriage_date"
    EVENT_DATE = "event_date"
    """The date the certificate records, when it is none of the above."""

    REGISTRATION_DATE = "registration_date"
    ISSUE_DATE = "issue_date"

    AGE = "age"
    SEX = "sex"
    ADDRESS = "address"
    PLACE = "place"
    ISSUING_AUTHORITY = "issuing_authority"

    @property
    def is_name(self) -> bool:
        return self in _NAME_ROLES

    @property
    def is_date(self) -> bool:
        return self in _DATE_ROLES


_NAME_ROLES: frozenset[FieldRole] = frozenset(
    {
        FieldRole.SUBJECT_NAME,
        FieldRole.PARTY_NAME,
        FieldRole.FATHER_NAME,
        FieldRole.MOTHER_NAME,
        FieldRole.SPOUSE_NAME,
    }
)

_DATE_ROLES: frozenset[FieldRole] = frozenset(
    {
        FieldRole.BIRTH_DATE,
        FieldRole.DEATH_DATE,
        FieldRole.MARRIAGE_DATE,
        FieldRole.EVENT_DATE,
        FieldRole.REGISTRATION_DATE,
        FieldRole.ISSUE_DATE,
    }
)


class SchemaVersionStatus(StrEnum):
    """A schema version's lifecycle.

    Published versions are immutable. Certificates reference the exact version
    they were read under, so editing one in place would silently rewrite the
    meaning of records already in the registry.
    """

    DRAFT = "DRAFT"
    PUBLISHED = "PUBLISHED"
    ARCHIVED = "ARCHIVED"
    """Superseded. Still readable, no longer offered for new batches."""

    @property
    def is_editable(self) -> bool:
        return self is SchemaVersionStatus.DRAFT


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
    DOCUMENT_REMOVED = "document.removed"
    DOCUMENT_DEDUPLICATED = "document.deduplicated"
    DOCUMENT_REPROCESSED = "document.reprocessed"
    DOCUMENT_PAGE_VIEWED = "document.page_viewed"

    ROWS_VIEWED = "rows.viewed"
    EXTRACTION_CORRECTED = "extraction.corrected"
    EXTRACTION_REPROCESSED = "extraction.reprocessed"
    EXTRACTION_APPROVED = "extraction.approved"

    CERTIFICATE_CREATED = "certificate.created"
    CERTIFICATE_UPDATED = "certificate.updated"
    CERTIFICATE_VIEWED = "certificate.viewed"
    CERTIFICATE_VOIDED = "certificate.voided"
    CERTIFICATE_DUPLICATE_FLAGGED = "certificate.duplicate_flagged"
    CERTIFICATE_DUPLICATE_RESOLVED = "certificate.duplicate_resolved"
    CERTIFICATE_DOCUMENT_LINKED = "certificate.document_linked"
    CERTIFICATE_DOCUMENT_VIEWED = "certificate.document_viewed"

    UNIT_SPLIT = "unit.split"
    UNIT_MERGED = "unit.merged"

    TEMPLATE_CREATED = "template.created"
    TEMPLATE_DELETED = "template.deleted"

    IMPORT_STARTED = "import.started"
    IMPORT_COMPLETED = "import.completed"
    IMPORT_CANCELLED = "import.cancelled"

    EXPORT_REQUESTED = "export.requested"
    EXPORT_COMPLETED = "export.completed"

    SETTINGS_UPDATED = "settings.updated"
    USER_CREATED = "user.created"
    USER_UPDATED = "user.updated"
    USER_DELETED = "user.deleted"

    RETENTION_PURGED = "retention.purged"


class CertificateStatus(StrEnum):
    """Whether a registry entry is the one to rely on."""

    ACTIVE = "ACTIVE"
    """The current entry for this certificate."""

    SUPERSEDED = "SUPERSEDED"
    """Replaced by another entry, which it points at. Kept, never deleted: the
    register is a historical record, and an entry that once existed has to remain
    findable for anyone holding a copy of it."""

    VOID = "VOID"
    """Cancelled by the issuing office. Still readable, never used as an answer."""


class DuplicateStatus(StrEnum):
    """What has been decided about two entries carrying the same number.

    Nothing is merged automatically. A repeated certificate number is a question
    for a person - the same number is reused across offices and years, and two
    genuinely different people can hold certificates that a machine cannot tell
    apart - so detection only raises the question.
    """

    NONE = "NONE"
    """No other entry looks like this one."""

    SUSPECTED = "SUSPECTED"
    """Detected automatically, not yet looked at by anyone."""

    CONFIRMED = "CONFIRMED"
    """A person decided these are the same certificate."""

    DISTINCT = "DISTINCT"
    """A person decided these are different certificates that happen to collide.
    Recorded so the same pair is not raised again."""


class CertificateSource(StrEnum):
    """Where a registry entry came from. Decides how its provenance reads."""

    EXTRACTION = "EXTRACTION"
    """Read from an uploaded document by the pipeline."""

    IMPORT = "IMPORT"
    """Loaded from a CSV of existing records."""

    MANUAL = "MANUAL"
    """Typed in by an operator."""


class DocumentLinkKind(StrEnum):
    """Why a document is attached to a certificate."""

    PRIMARY = "PRIMARY"
    """The certificate itself: the page range this entry was read from."""

    SUPPORTING = "SUPPORTING"
    """An attachment - an affidavit, an identity document, a correction slip."""

    SUPERSEDED = "SUPERSEDED"
    """An earlier scan of the same certificate, kept for the record."""


class ImportStatus(StrEnum):
    """Where a CSV import has got to."""

    PENDING = "PENDING"
    """Uploaded and queued. No row has been read yet."""

    RUNNING = "RUNNING"
    PARTIAL = "PARTIAL"
    """Finished, with rows that could not be filed. The errors say which and why."""

    COMPLETED = "COMPLETED"
    """Every row was filed."""

    FAILED = "FAILED"
    """The file itself could not be used - wrong columns, unreadable encoding."""

    CANCELLED = "CANCELLED"

    @property
    def is_finished(self) -> bool:
        return self in _FINISHED_IMPORTS


_FINISHED_IMPORTS = frozenset(
    {
        ImportStatus.PARTIAL,
        ImportStatus.COMPLETED,
        ImportStatus.FAILED,
        ImportStatus.CANCELLED,
    }
)


class ImportDuplicatePolicy(StrEnum):
    """What an import does with a certificate number the register already holds.

    There is deliberately no option to overwrite. An import is a bulk operation
    nobody watches row by row, and silently replacing a record with another that
    happens to share its number is how a register loses an entry with no trace of
    what it used to say.
    """

    SKIP = "SKIP"
    """Leave the existing entry alone and report the row as an error."""

    RECORD_AS_DUPLICATE = "RECORD_AS_DUPLICATE"
    """Record the row as a second entry, linked to the first and sent to review."""


class RevisionAction(StrEnum):
    """What happened to a register entry, one row of its history.

    The register is a historical record: an entry that once said something has to
    keep saying it, in a form somebody can read back, because a person may be
    holding a copy of what it used to say.
    """

    CREATED = "CREATED"
    CORRECTED = "CORRECTED"
    """A value was changed by a person, who is named and whose reason is kept."""

    APPROVED = "APPROVED"
    """A reviewer accepted the entry as it stands."""

    VOIDED = "VOIDED"
    """Cancelled by the issuing office. Still readable, never used as an answer."""

    SUPERSEDED = "SUPERSEDED"
    """Replaced by another entry, which this one points at."""

    DUPLICATE_RESOLVED = "DUPLICATE_RESOLVED"
    """A person decided whether two entries were one certificate or two."""

    DOCUMENT_ATTACHED = "DOCUMENT_ATTACHED"
    DOCUMENT_REPLACED = "DOCUMENT_REPLACED"
    """A better scan of the same certificate arrived. The old one is kept."""
