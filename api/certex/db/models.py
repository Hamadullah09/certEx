"""SQLAlchemy 2.0 ORM models.

Two deliberate denormalisations, both for safety rather than convenience:

``documents.workspace_id``
    Content-hash deduplication is scoped to a workspace, and the dedup lookup runs
    once per uploaded file. Carrying the workspace on the row turns that into a
    single indexed probe instead of a join back through ``batches``.

``extractions.workspace_id`` / ``extractions.batch_id``
    The results grid filters and sorts thousands of rows per request. Without
    these, every row query would join ``extractions -> certificate_units ->
    documents -> batches`` purely to prove tenancy. Both columns are written once
    at row creation and never updated, and the workspace guard in
    :mod:`certex.db.scoping` relies on them.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from certex.db.base import (
    Base,
    JSONDict,
    JSONList,
    TimestampMixin,
    enum_column,
    utcnow,
    uuid_pk,
)
from certex.enums import (
    AuditAction,
    BatchStatus,
    BoundaryMethod,
    CertificateType,
    ClassificationMethod,
    DocumentStatus,
    ExportFormat,
    OcrEngine,
    PageExtractionSource,
    ReviewStatus,
    UnitStatus,
    UserRole,
)

if TYPE_CHECKING:  # pragma: no cover
    pass

__all__ = [
    "AuditLog",
    "Batch",
    "CertificateUnit",
    "DeadLetterTask",
    "Document",
    "Export",
    "Extraction",
    "FieldCorrection",
    "PageText",
    "RefreshToken",
    "Template",
    "UploadSession",
    "User",
    "Workspace",
]


# ---------------------------------------------------------------------------
# Tenancy
# ---------------------------------------------------------------------------
class Workspace(Base, TimestampMixin):
    __tablename__ = "workspaces"

    id: Mapped[uuid.UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    settings_json: Mapped[JSONDict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )

    users: Mapped[list[User]] = relationship(
        back_populates="workspace", cascade="all, delete-orphan"
    )
    batches: Mapped[list[Batch]] = relationship(
        back_populates="workspace", cascade="all, delete-orphan"
    )
    templates: Mapped[list[Template]] = relationship(
        back_populates="workspace", cascade="all, delete-orphan"
    )


class User(Base, TimestampMixin):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("email", name="uq_users_email"),
        Index("ix_users_workspace_id_role", "workspace_id", "role"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    role: Mapped[UserRole] = mapped_column(
        enum_column(UserRole), nullable=False, default=UserRole.OPERATOR
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    last_login_at: Mapped[dt.datetime | None] = mapped_column(nullable=True)

    workspace: Mapped[Workspace] = relationship(back_populates="users")
    refresh_tokens: Mapped[list[RefreshToken]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class RefreshToken(Base):
    """One issued refresh token.

    Rotation model: presenting a token consumes it and issues a successor in the
    same ``family_id``. Presenting an already-consumed token means the cookie was
    stolen and replayed, so the whole family is revoked.
    """

    __tablename__ = "refresh_tokens"
    __table_args__ = (
        Index("ix_refresh_tokens_user_id_family_id", "user_id", "family_id"),
        Index("ix_refresh_tokens_expires_at", "expires_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    family_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    """SHA-256 of the opaque token. The token itself is never stored."""

    issued_at: Mapped[dt.datetime] = mapped_column(
        nullable=False, default=utcnow, server_default=func.now()
    )
    expires_at: Mapped[dt.datetime] = mapped_column(nullable=False)
    consumed_at: Mapped[dt.datetime | None] = mapped_column(nullable=True)
    revoked_at: Mapped[dt.datetime | None] = mapped_column(nullable=True)
    replaced_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("refresh_tokens.id", ondelete="SET NULL"), nullable=True
    )
    ip_address: Mapped[str | None] = mapped_column(INET, nullable=True)

    user: Mapped[User] = relationship(back_populates="refresh_tokens")

    @property
    def is_usable(self) -> bool:
        now = dt.datetime.now(dt.UTC)
        return self.consumed_at is None and self.revoked_at is None and self.expires_at > now


# ---------------------------------------------------------------------------
# Batches and documents
# ---------------------------------------------------------------------------
class Batch(Base, TimestampMixin):
    __tablename__ = "batches"
    __table_args__ = (
        Index("ix_batches_workspace_id_created_at", "workspace_id", "created_at"),
        CheckConstraint("file_count >= 0", name="file_count_non_negative"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[BatchStatus] = mapped_column(
        enum_column(BatchStatus), nullable=False, default=BatchStatus.CREATED, index=True
    )

    file_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    unit_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    processed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    duplicate_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)

    settings_json: Mapped[JSONDict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    """Per-batch overrides: expected types, OCR languages and confidence thresholds."""

    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    started_at: Mapped[dt.datetime | None] = mapped_column(nullable=True)
    completed_at: Mapped[dt.datetime | None] = mapped_column(nullable=True)

    workspace: Mapped[Workspace] = relationship(back_populates="batches")
    documents: Mapped[list[Document]] = relationship(
        back_populates="batch", cascade="all, delete-orphan"
    )


class Document(Base, TimestampMixin):
    __tablename__ = "documents"
    __table_args__ = (
        # Dedup probe: "have I already ingested these bytes in this workspace?"
        Index("ix_documents_workspace_id_sha256", "workspace_id", "sha256"),
        Index("ix_documents_batch_id_status", "batch_id", "status"),
        Index("ix_documents_sha256", "sha256"),
        CheckConstraint("byte_size >= 0", name="byte_size_non_negative"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    batch_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("batches.id", ondelete="CASCADE"), nullable=False, index=True
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )

    original_filename: Mapped[str] = mapped_column(String(512), nullable=False)
    """Sanitised display name. Never used to build a storage key."""

    mime_type: Mapped[str] = mapped_column(String(128), nullable=False)
    """Sniffed from content, not from the filename extension."""

    declared_mime_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    byte_size: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(512), nullable=False)
    """UUID-derived object key. Never contains user-supplied text."""

    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[DocumentStatus] = mapped_column(
        enum_column(DocumentStatus), nullable=False, default=DocumentStatus.QUEUED, index=True
    )
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    is_duplicate_of: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("documents.id", ondelete="SET NULL"), nullable=True
    )
    parent_document_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=True
    )
    """Set when this document was unpacked from an archive or converted from .doc."""

    archive_depth: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    archive_member_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    is_encrypted: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    processing_started_at: Mapped[dt.datetime | None] = mapped_column(nullable=True)
    processing_completed_at: Mapped[dt.datetime | None] = mapped_column(nullable=True)

    batch: Mapped[Batch] = relationship(back_populates="documents")
    units: Mapped[list[CertificateUnit]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )
    pages: Mapped[list[PageText]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )


class PageText(Base, TimestampMixin):
    """One row per physical page, holding both the text and its layout geometry.

    Raw OCR output is never discarded: re-extraction, debugging and the review
    pane's bounding-box highlighting all read from here.
    """

    __tablename__ = "page_texts"
    __table_args__ = (
        UniqueConstraint(
            "document_id", "page_number", name="uq_page_texts_document_id_page_number"
        ),
        Index("ix_page_texts_document_id_page_number", "document_id", "page_number"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    page_number: Mapped[int] = mapped_column(Integer, nullable=False)
    """1-based."""

    extraction_source: Mapped[PageExtractionSource] = mapped_column(
        enum_column(PageExtractionSource), nullable=False, default=PageExtractionSource.NONE
    )
    raw_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    layout_blocks_jsonb: Mapped[JSONDict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    """Serialised :class:`certex.schemas.layout.PageLayout` - words, lines, blocks, tables."""

    ocr_engine: Mapped[OcrEngine] = mapped_column(
        enum_column(OcrEngine), nullable=False, default=OcrEngine.NONE
    )
    ocr_mean_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    """Mean per-word confidence on a 0-100 scale, or NULL for native text."""

    language: Mapped[str | None] = mapped_column(String(32), nullable=True)

    # Quality signals that drove the native-vs-OCR decision.
    char_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    alnum_ratio: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    dict_hit_rate: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    needed_ocr: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )

    # Page geometry, needed to map stored bboxes onto a rendered review image.
    width: Mapped[float | None] = mapped_column(Float, nullable=True)
    height: Mapped[float | None] = mapped_column(Float, nullable=True)
    rotation: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    """Deskew angle applied before OCR, in degrees."""

    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    """SHA-256 of the rendered page image plus the OCR recipe, keying the OCR cache.

    Re-processing a document, or the same scan uploaded twice, hits the cache instead
    of spending seconds of CPU per page again."""

    document: Mapped[Document] = relationship(back_populates="pages")


class CertificateUnit(Base, TimestampMixin):
    """One logical certificate: a page range inside a source document."""

    __tablename__ = "certificate_units"
    __table_args__ = (
        Index("ix_certificate_units_document_id", "document_id"),
        Index("ix_certificate_units_batch_id_status", "batch_id", "status"),
        CheckConstraint("page_end >= page_start", name="page_range_ordered"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    batch_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("batches.id", ondelete="CASCADE"), nullable=False
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    """Position of this unit within its document, 0-based. Drives stable row order."""

    page_start: Mapped[int] = mapped_column(Integer, nullable=False)
    page_end: Mapped[int] = mapped_column(Integer, nullable=False)

    boundary_method: Mapped[BoundaryMethod] = mapped_column(
        enum_column(BoundaryMethod), nullable=False, default=BoundaryMethod.SINGLE_DOCUMENT
    )
    boundary_confidence: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)

    certificate_type: Mapped[CertificateType] = mapped_column(
        enum_column(CertificateType), nullable=False, default=CertificateType.OTHER, index=True
    )
    type_confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    classification_method: Mapped[ClassificationMethod] = mapped_column(
        enum_column(ClassificationMethod), nullable=False, default=ClassificationMethod.DEFAULT
    )

    status: Mapped[UnitStatus] = mapped_column(
        enum_column(UnitStatus), nullable=False, default=UnitStatus.PENDING, index=True
    )
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    document: Mapped[Document] = relationship(back_populates="units")
    extractions: Mapped[list[Extraction]] = relationship(
        back_populates="unit", cascade="all, delete-orphan"
    )

    @property
    def page_count(self) -> int:
        return self.page_end - self.page_start + 1


# ---------------------------------------------------------------------------
# Extraction results
# ---------------------------------------------------------------------------
class Extraction(Base, TimestampMixin):
    """The extracted field set for one certificate unit - one CSV row."""

    __tablename__ = "extractions"
    __table_args__ = (
        Index("ix_extractions_unit_id", "unit_id"),
        Index("ix_extractions_review_status", "review_status"),
        Index("ix_extractions_batch_id_review_status", "batch_id", "review_status"),
        Index("ix_extractions_batch_id_certificate_type", "batch_id", "certificate_type"),
        # GIN indexes so flag filters and field searches stay indexed rather than
        # degrading to a sequential scan once a workspace holds millions of rows.
        Index("ix_extractions_flags_gin", "flags_jsonb", postgresql_using="gin"),
        Index("ix_extractions_fields_gin", "fields_jsonb", postgresql_using="gin"),
        CheckConstraint(
            "row_confidence >= 0 AND row_confidence <= 1", name="row_confidence_is_probability"
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    unit_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("certificate_units.id", ondelete="CASCADE"), nullable=False
    )
    batch_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("batches.id", ondelete="CASCADE"), nullable=False
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )

    schema_version: Mapped[str] = mapped_column(String(16), nullable=False, default="1.0")
    certificate_type: Mapped[CertificateType] = mapped_column(
        enum_column(CertificateType), nullable=False, default=CertificateType.OTHER
    )

    fields_jsonb: Mapped[JSONDict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    """field name -> normalised value (string) or null."""

    field_confidences_jsonb: Mapped[JSONDict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    """field name -> float in [0, 1]."""

    field_methods_jsonb: Mapped[JSONDict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    """field name -> :class:`certex.enums.ExtractionMethod` value."""

    field_sources_jsonb: Mapped[JSONDict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    """field name -> provenance: verbatim snippet, page number, bounding box."""

    extra_fields_jsonb: Mapped[JSONDict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    """Fields found in the document that are not part of the type's schema."""

    flags_jsonb: Mapped[JSONList] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    """List of :class:`certex.enums.ValidationFlag` values.

    Flat and indexed, because the results grid filters on it: "show me every row with a
    date problem" has to stay a single indexed query over millions of rows."""

    field_issues_jsonb: Mapped[JSONList] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    """Per-field detail behind the flags: ``{field, flag, detail}``.

    A flag alone tells a reviewer that something is wrong; this tells them which value
    to look at and why, which is what they act on."""

    row_confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    review_status: Mapped[ReviewStatus] = mapped_column(
        enum_column(ReviewStatus), nullable=False, default=ReviewStatus.NEEDS_REVIEW
    )

    ocr_used: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    ocr_mean_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    detected_language: Mapped[str | None] = mapped_column(String(32), nullable=True)
    template_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("templates.id", ondelete="SET NULL"), nullable=True
    )

    processed_at: Mapped[dt.datetime | None] = mapped_column(nullable=True)
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    reviewed_at: Mapped[dt.datetime | None] = mapped_column(nullable=True)

    unit: Mapped[CertificateUnit] = relationship(back_populates="extractions")
    corrections: Mapped[list[FieldCorrection]] = relationship(
        back_populates="extraction", cascade="all, delete-orphan"
    )


class FieldCorrection(Base):
    """Audit trail of every human edit to an extracted field."""

    __tablename__ = "field_corrections"
    __table_args__ = (
        Index("ix_field_corrections_extraction_id", "extraction_id"),
        Index("ix_field_corrections_corrected_at", "corrected_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    extraction_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("extractions.id", ondelete="CASCADE"), nullable=False
    )
    field_name: Mapped[str] = mapped_column(String(128), nullable=False)
    old_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    new_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    corrected_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    corrected_at: Mapped[dt.datetime] = mapped_column(
        nullable=False, default=utcnow, server_default=func.now()
    )

    extraction: Mapped[Extraction] = relationship(back_populates="corrections")


class Template(Base, TimestampMixin):
    """A learned positional extraction template, keyed by layout fingerprint."""

    __tablename__ = "templates"
    __table_args__ = (
        Index("ix_templates_fingerprint", "fingerprint"),
        Index("ix_templates_workspace_id_fingerprint", "workspace_id", "fingerprint"),
        UniqueConstraint(
            "workspace_id", "fingerprint", name="uq_templates_workspace_id_fingerprint"
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    """Stable hash of issuing authority, form number and layout anchors."""

    certificate_type: Mapped[CertificateType] = mapped_column(
        enum_column(CertificateType), nullable=False
    )
    rules_jsonb: Mapped[JSONDict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    """Serialised :class:`certex.schemas.template.TemplateRules`."""

    hit_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    created_from_extraction_id: Mapped[uuid.UUID | None] = mapped_column(
        # ``use_alter`` breaks the templates <-> extractions cycle: a template is
        # learned FROM an extraction, and an extraction records the template that
        # produced it. Emitting this one constraint as a post-create ALTER lets
        # both DDL generation and Alembic autogenerate order the tables.
        ForeignKey(
            "extractions.id",
            ondelete="SET NULL",
            use_alter=True,
            name="fk_templates_created_from_extraction_id_extractions",
        ),
        nullable=True,
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    workspace: Mapped[Workspace] = relationship(back_populates="templates")


class Export(Base):
    __tablename__ = "exports"
    __table_args__ = (Index("ix_exports_batch_id_created_at", "batch_id", "created_at"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    batch_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("batches.id", ondelete="CASCADE"), nullable=False
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    format: Mapped[ExportFormat] = mapped_column(enum_column(ExportFormat), nullable=False)
    options_jsonb: Mapped[JSONDict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    row_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    column_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    byte_size: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    storage_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    """Set only when the export was persisted; streamed exports leave it NULL."""

    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[dt.datetime] = mapped_column(
        nullable=False, default=utcnow, server_default=func.now()
    )


class AuditLog(Base):
    """Append-only record of who did what, from where."""

    __tablename__ = "audit_log"
    __table_args__ = (
        Index("ix_audit_log_workspace_id_created_at", "workspace_id", "created_at"),
        Index("ix_audit_log_entity_type_entity_id", "entity_type", "entity_id"),
        Index("ix_audit_log_action", "action"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    workspace_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workspaces.id", ondelete="SET NULL"), nullable=True
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    action: Mapped[AuditAction] = mapped_column(enum_column(AuditAction, length=64), nullable=False)
    entity_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    metadata_jsonb: Mapped[JSONDict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    """Counts, ids and enum labels only. Field values must never be written here."""

    ip_address: Mapped[str | None] = mapped_column(INET, nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        nullable=False,
        default=utcnow,
        server_default=func.now(),
        index=True,
    )


# ---------------------------------------------------------------------------
# Upload + reliability support tables
# ---------------------------------------------------------------------------
class UploadSession(Base, TimestampMixin):
    """State for one resumable, chunked file upload.

    Survives a closed browser: the client re-announces the file and resumes from
    ``received_bytes`` using the recorded S3 multipart upload id.
    """

    __tablename__ = "upload_sessions"
    __table_args__ = (
        Index("ix_upload_sessions_batch_id", "batch_id"),
        Index("ix_upload_sessions_expires_at", "expires_at"),
        UniqueConstraint("batch_id", "client_file_id", name="uq_upload_sessions_batch_client_file"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    batch_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("batches.id", ondelete="CASCADE"), nullable=False
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    client_file_id: Mapped[str] = mapped_column(String(128), nullable=False)
    """Client-generated stable id so a resumed upload finds its session."""

    original_filename: Mapped[str] = mapped_column(String(512), nullable=False)
    declared_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    declared_mime_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    storage_key: Mapped[str] = mapped_column(String(512), nullable=False)
    multipart_upload_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    parts_jsonb: Mapped[JSONList] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    """[{part_number, etag, size}] for completing the multipart upload."""

    received_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    is_complete: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    document_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("documents.id", ondelete="SET NULL"), nullable=True
    )
    expires_at: Mapped[dt.datetime] = mapped_column(nullable=False)


class DeadLetterTask(Base):
    """A task that exhausted its retries. Inspectable and replayable from the UI."""

    __tablename__ = "dead_letter_tasks"
    __table_args__ = (
        Index("ix_dead_letter_tasks_batch_id", "batch_id"),
        Index("ix_dead_letter_tasks_created_at", "created_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    task_name: Mapped[str] = mapped_column(String(128), nullable=False)
    batch_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("batches.id", ondelete="CASCADE"), nullable=True
    )
    document_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=True
    )
    unit_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("certificate_units.id", ondelete="CASCADE"), nullable=True
    )
    args_jsonb: Mapped[JSONDict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    """Task kwargs - ids only, never document content."""

    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str] = mapped_column(Text, nullable=False, default="")
    traceback_digest: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    replayed_at: Mapped[dt.datetime | None] = mapped_column(nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        nullable=False, default=utcnow, server_default=func.now()
    )
