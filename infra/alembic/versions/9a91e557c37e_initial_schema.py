"""Initial schema: tenancy, batches, documents, pipeline results, audit.

Revision ID: 9a91e557c37e
Revises: 
Created: 2026-09-15 18:38:19.863242+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '9a91e557c37e'
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('workspaces',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('settings_json', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_workspaces'))
    )
    op.create_index(op.f('ix_workspaces_created_at'), 'workspaces', ['created_at'], unique=False)
    op.create_table('users',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('workspace_id', sa.UUID(), nullable=False),
    sa.Column('email', sa.String(length=320), nullable=False),
    sa.Column('password_hash', sa.String(length=128), nullable=False),
    sa.Column('role', sa.Enum('ADMIN', 'OPERATOR', 'VIEWER', name='userrole', native_enum=False, length=48), nullable=False),
    sa.Column('is_active', sa.Boolean(), server_default='true', nullable=False),
    sa.Column('last_login_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], name=op.f('fk_users_workspace_id_workspaces'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_users')),
    sa.UniqueConstraint('email', name='uq_users_email')
    )
    op.create_index(op.f('ix_users_created_at'), 'users', ['created_at'], unique=False)
    op.create_index(op.f('ix_users_workspace_id'), 'users', ['workspace_id'], unique=False)
    op.create_index('ix_users_workspace_id_role', 'users', ['workspace_id', 'role'], unique=False)
    op.create_table('audit_log',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('workspace_id', sa.UUID(), nullable=True),
    sa.Column('user_id', sa.UUID(), nullable=True),
    sa.Column('action', sa.Enum('login.succeeded', 'login.failed', 'logout', 'token.refreshed', 'token.reuse_detected', 'batch.created', 'batch.started', 'batch.deleted', 'batch.viewed', 'document.uploaded', 'document.deduplicated', 'document.reprocessed', 'document.page_viewed', 'rows.viewed', 'extraction.corrected', 'extraction.reprocessed', 'extraction.approved', 'unit.split', 'unit.merged', 'template.created', 'template.deleted', 'export.requested', 'export.completed', 'settings.updated', 'user.created', 'user.updated', 'user.deleted', 'retention.purged', name='auditaction', native_enum=False, length=64), nullable=False),
    sa.Column('entity_type', sa.String(length=64), nullable=True),
    sa.Column('entity_id', sa.UUID(), nullable=True),
    sa.Column('metadata_jsonb', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('ip_address', postgresql.INET(), nullable=True),
    sa.Column('user_agent', sa.String(length=512), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_audit_log_user_id_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], name=op.f('fk_audit_log_workspace_id_workspaces'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_audit_log'))
    )
    op.create_index('ix_audit_log_action', 'audit_log', ['action'], unique=False)
    op.create_index(op.f('ix_audit_log_created_at'), 'audit_log', ['created_at'], unique=False)
    op.create_index('ix_audit_log_entity_type_entity_id', 'audit_log', ['entity_type', 'entity_id'], unique=False)
    op.create_index('ix_audit_log_workspace_id_created_at', 'audit_log', ['workspace_id', 'created_at'], unique=False)
    op.create_table('batches',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('workspace_id', sa.UUID(), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('status', sa.Enum('CREATED', 'UPLOADING', 'QUEUED', 'PROCESSING', 'COMPLETED', 'COMPLETED_WITH_ERRORS', 'FAILED', 'CANCELLED', name='batchstatus', native_enum=False, length=48), nullable=False),
    sa.Column('file_count', sa.Integer(), nullable=False),
    sa.Column('unit_count', sa.Integer(), nullable=False),
    sa.Column('processed_count', sa.Integer(), nullable=False),
    sa.Column('failed_count', sa.Integer(), nullable=False),
    sa.Column('duplicate_count', sa.Integer(), nullable=False),
    sa.Column('total_bytes', sa.BigInteger(), nullable=False),
    sa.Column('settings_json', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('error_message', sa.Text(), nullable=True),
    sa.Column('created_by', sa.UUID(), nullable=True),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('file_count >= 0', name=op.f('ck_batches_file_count_non_negative')),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_batches_created_by_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], name=op.f('fk_batches_workspace_id_workspaces'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_batches'))
    )
    op.create_index(op.f('ix_batches_created_at'), 'batches', ['created_at'], unique=False)
    op.create_index(op.f('ix_batches_status'), 'batches', ['status'], unique=False)
    op.create_index(op.f('ix_batches_workspace_id'), 'batches', ['workspace_id'], unique=False)
    op.create_index('ix_batches_workspace_id_created_at', 'batches', ['workspace_id', 'created_at'], unique=False)
    op.create_table('refresh_tokens',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('family_id', sa.UUID(), nullable=False),
    sa.Column('token_hash', sa.String(length=64), nullable=False),
    sa.Column('issued_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('consumed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('replaced_by_id', sa.UUID(), nullable=True),
    sa.Column('ip_address', postgresql.INET(), nullable=True),
    sa.ForeignKeyConstraint(['replaced_by_id'], ['refresh_tokens.id'], name=op.f('fk_refresh_tokens_replaced_by_id_refresh_tokens'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_refresh_tokens_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_refresh_tokens')),
    sa.UniqueConstraint('token_hash', name=op.f('uq_refresh_tokens_token_hash'))
    )
    op.create_index('ix_refresh_tokens_expires_at', 'refresh_tokens', ['expires_at'], unique=False)
    op.create_index('ix_refresh_tokens_user_id_family_id', 'refresh_tokens', ['user_id', 'family_id'], unique=False)
    op.create_table('templates',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('workspace_id', sa.UUID(), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('fingerprint', sa.String(length=64), nullable=False),
    sa.Column('certificate_type', sa.Enum('BIRTH', 'MARRIAGE', 'DEATH', 'OTHER', name='certificatetype', native_enum=False, length=48), nullable=False),
    sa.Column('rules_jsonb', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('hit_count', sa.Integer(), nullable=False),
    sa.Column('is_active', sa.Boolean(), server_default='true', nullable=False),
    sa.Column('created_from_extraction_id', sa.UUID(), nullable=True),
    sa.Column('created_by', sa.UUID(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_templates_created_by_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], name=op.f('fk_templates_workspace_id_workspaces'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_templates')),
    sa.UniqueConstraint('workspace_id', 'fingerprint', name='uq_templates_workspace_id_fingerprint')
    )
    op.create_index(op.f('ix_templates_created_at'), 'templates', ['created_at'], unique=False)
    op.create_index('ix_templates_fingerprint', 'templates', ['fingerprint'], unique=False)
    op.create_index('ix_templates_workspace_id_fingerprint', 'templates', ['workspace_id', 'fingerprint'], unique=False)
    op.create_table('documents',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('batch_id', sa.UUID(), nullable=False),
    sa.Column('workspace_id', sa.UUID(), nullable=False),
    sa.Column('original_filename', sa.String(length=512), nullable=False),
    sa.Column('mime_type', sa.String(length=128), nullable=False),
    sa.Column('declared_mime_type', sa.String(length=128), nullable=True),
    sa.Column('byte_size', sa.BigInteger(), nullable=False),
    sa.Column('sha256', sa.String(length=64), nullable=False),
    sa.Column('storage_key', sa.String(length=512), nullable=False),
    sa.Column('page_count', sa.Integer(), nullable=True),
    sa.Column('status', sa.Enum('QUEUED', 'INGESTED', 'NORMALIZING', 'SPLITTING', 'EXTRACTING_TEXT', 'OCR', 'CLASSIFYING', 'EXTRACTING_FIELDS', 'VALIDATING', 'COMPLETED', 'FAILED', 'DUPLICATE', 'SKIPPED', name='documentstatus', native_enum=False, length=48), nullable=False),
    sa.Column('error_code', sa.String(length=64), nullable=True),
    sa.Column('error_message', sa.Text(), nullable=True),
    sa.Column('is_duplicate_of', sa.UUID(), nullable=True),
    sa.Column('parent_document_id', sa.UUID(), nullable=True),
    sa.Column('archive_depth', sa.Integer(), nullable=False),
    sa.Column('archive_member_path', sa.String(length=1024), nullable=True),
    sa.Column('is_encrypted', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('processing_started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('processing_completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('byte_size >= 0', name=op.f('ck_documents_byte_size_non_negative')),
    sa.ForeignKeyConstraint(['batch_id'], ['batches.id'], name=op.f('fk_documents_batch_id_batches'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['is_duplicate_of'], ['documents.id'], name=op.f('fk_documents_is_duplicate_of_documents'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['parent_document_id'], ['documents.id'], name=op.f('fk_documents_parent_document_id_documents'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], name=op.f('fk_documents_workspace_id_workspaces'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_documents'))
    )
    op.create_index(op.f('ix_documents_batch_id'), 'documents', ['batch_id'], unique=False)
    op.create_index('ix_documents_batch_id_status', 'documents', ['batch_id', 'status'], unique=False)
    op.create_index(op.f('ix_documents_created_at'), 'documents', ['created_at'], unique=False)
    op.create_index('ix_documents_sha256', 'documents', ['sha256'], unique=False)
    op.create_index(op.f('ix_documents_status'), 'documents', ['status'], unique=False)
    op.create_index('ix_documents_workspace_id_sha256', 'documents', ['workspace_id', 'sha256'], unique=False)
    op.create_table('exports',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('batch_id', sa.UUID(), nullable=False),
    sa.Column('workspace_id', sa.UUID(), nullable=False),
    sa.Column('format', sa.Enum('csv', 'xlsx', 'json', name='exportformat', native_enum=False, length=48), nullable=False),
    sa.Column('options_jsonb', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('row_count', sa.Integer(), nullable=False),
    sa.Column('column_count', sa.Integer(), nullable=False),
    sa.Column('byte_size', sa.BigInteger(), nullable=False),
    sa.Column('storage_key', sa.String(length=512), nullable=True),
    sa.Column('created_by', sa.UUID(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['batch_id'], ['batches.id'], name=op.f('fk_exports_batch_id_batches'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_exports_created_by_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], name=op.f('fk_exports_workspace_id_workspaces'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_exports'))
    )
    op.create_index('ix_exports_batch_id_created_at', 'exports', ['batch_id', 'created_at'], unique=False)
    op.create_table('certificate_units',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('document_id', sa.UUID(), nullable=False),
    sa.Column('batch_id', sa.UUID(), nullable=False),
    sa.Column('ordinal', sa.Integer(), nullable=False),
    sa.Column('page_start', sa.Integer(), nullable=False),
    sa.Column('page_end', sa.Integer(), nullable=False),
    sa.Column('boundary_method', sa.Enum('single_document', 'bookmark', 'content_header', 'serial_number', 'fixed_stride', 'uniform_page_count', 'manual', name='boundarymethod', native_enum=False, length=48), nullable=False),
    sa.Column('boundary_confidence', sa.Float(), nullable=False),
    sa.Column('certificate_type', sa.Enum('BIRTH', 'MARRIAGE', 'DEATH', 'OTHER', name='certificatetype', native_enum=False, length=48), nullable=False),
    sa.Column('type_confidence', sa.Float(), nullable=False),
    sa.Column('classification_method', sa.Enum('keyword', 'llm', 'template', 'manual', 'default', name='classificationmethod', native_enum=False, length=48), nullable=False),
    sa.Column('status', sa.Enum('PENDING', 'TEXT_READY', 'CLASSIFIED', 'EXTRACTED', 'VALIDATED', 'COMPLETED', 'FAILED', name='unitstatus', native_enum=False, length=48), nullable=False),
    sa.Column('error_code', sa.String(length=64), nullable=True),
    sa.Column('error_message', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('page_end >= page_start', name=op.f('ck_certificate_units_page_range_ordered')),
    sa.ForeignKeyConstraint(['batch_id'], ['batches.id'], name=op.f('fk_certificate_units_batch_id_batches'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['document_id'], ['documents.id'], name=op.f('fk_certificate_units_document_id_documents'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_certificate_units'))
    )
    op.create_index('ix_certificate_units_batch_id_status', 'certificate_units', ['batch_id', 'status'], unique=False)
    op.create_index(op.f('ix_certificate_units_certificate_type'), 'certificate_units', ['certificate_type'], unique=False)
    op.create_index(op.f('ix_certificate_units_created_at'), 'certificate_units', ['created_at'], unique=False)
    op.create_index('ix_certificate_units_document_id', 'certificate_units', ['document_id'], unique=False)
    op.create_index(op.f('ix_certificate_units_status'), 'certificate_units', ['status'], unique=False)
    op.create_table('page_texts',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('document_id', sa.UUID(), nullable=False),
    sa.Column('page_number', sa.Integer(), nullable=False),
    sa.Column('extraction_source', sa.Enum('native_pdf', 'ocr', 'docx', 'image_ocr', 'none', name='pageextractionsource', native_enum=False, length=48), nullable=False),
    sa.Column('raw_text', sa.Text(), nullable=False),
    sa.Column('layout_blocks_jsonb', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('ocr_engine', sa.Enum('tesseract', 'paddleocr', 'none', name='ocrengine', native_enum=False, length=48), nullable=False),
    sa.Column('ocr_mean_confidence', sa.Float(), nullable=True),
    sa.Column('language', sa.String(length=32), nullable=True),
    sa.Column('char_count', sa.Integer(), nullable=False),
    sa.Column('alnum_ratio', sa.Float(), nullable=False),
    sa.Column('dict_hit_rate', sa.Float(), nullable=False),
    sa.Column('needed_ocr', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('width', sa.Float(), nullable=True),
    sa.Column('height', sa.Float(), nullable=True),
    sa.Column('rotation', sa.Float(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['document_id'], ['documents.id'], name=op.f('fk_page_texts_document_id_documents'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_page_texts')),
    sa.UniqueConstraint('document_id', 'page_number', name='uq_page_texts_document_id_page_number')
    )
    op.create_index(op.f('ix_page_texts_created_at'), 'page_texts', ['created_at'], unique=False)
    op.create_index('ix_page_texts_document_id_page_number', 'page_texts', ['document_id', 'page_number'], unique=False)
    op.create_table('upload_sessions',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('batch_id', sa.UUID(), nullable=False),
    sa.Column('workspace_id', sa.UUID(), nullable=False),
    sa.Column('client_file_id', sa.String(length=128), nullable=False),
    sa.Column('original_filename', sa.String(length=512), nullable=False),
    sa.Column('declared_size', sa.BigInteger(), nullable=False),
    sa.Column('declared_mime_type', sa.String(length=128), nullable=True),
    sa.Column('storage_key', sa.String(length=512), nullable=False),
    sa.Column('multipart_upload_id', sa.String(length=256), nullable=True),
    sa.Column('parts_jsonb', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
    sa.Column('received_bytes', sa.BigInteger(), nullable=False),
    sa.Column('is_complete', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('document_id', sa.UUID(), nullable=True),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['batch_id'], ['batches.id'], name=op.f('fk_upload_sessions_batch_id_batches'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['document_id'], ['documents.id'], name=op.f('fk_upload_sessions_document_id_documents'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], name=op.f('fk_upload_sessions_workspace_id_workspaces'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_upload_sessions')),
    sa.UniqueConstraint('batch_id', 'client_file_id', name='uq_upload_sessions_batch_client_file')
    )
    op.create_index('ix_upload_sessions_batch_id', 'upload_sessions', ['batch_id'], unique=False)
    op.create_index(op.f('ix_upload_sessions_created_at'), 'upload_sessions', ['created_at'], unique=False)
    op.create_index('ix_upload_sessions_expires_at', 'upload_sessions', ['expires_at'], unique=False)
    op.create_table('dead_letter_tasks',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('task_name', sa.String(length=128), nullable=False),
    sa.Column('batch_id', sa.UUID(), nullable=True),
    sa.Column('document_id', sa.UUID(), nullable=True),
    sa.Column('unit_id', sa.UUID(), nullable=True),
    sa.Column('args_jsonb', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('error_code', sa.String(length=64), nullable=True),
    sa.Column('error_message', sa.Text(), nullable=False),
    sa.Column('traceback_digest', sa.Text(), nullable=True),
    sa.Column('attempts', sa.Integer(), nullable=False),
    sa.Column('replayed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['batch_id'], ['batches.id'], name=op.f('fk_dead_letter_tasks_batch_id_batches'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['document_id'], ['documents.id'], name=op.f('fk_dead_letter_tasks_document_id_documents'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['unit_id'], ['certificate_units.id'], name=op.f('fk_dead_letter_tasks_unit_id_certificate_units'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_dead_letter_tasks'))
    )
    op.create_index('ix_dead_letter_tasks_batch_id', 'dead_letter_tasks', ['batch_id'], unique=False)
    op.create_index('ix_dead_letter_tasks_created_at', 'dead_letter_tasks', ['created_at'], unique=False)
    op.create_table('extractions',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('unit_id', sa.UUID(), nullable=False),
    sa.Column('batch_id', sa.UUID(), nullable=False),
    sa.Column('workspace_id', sa.UUID(), nullable=False),
    sa.Column('schema_version', sa.String(length=16), nullable=False),
    sa.Column('certificate_type', sa.Enum('BIRTH', 'MARRIAGE', 'DEATH', 'OTHER', name='certificatetype', native_enum=False, length=48), nullable=False),
    sa.Column('fields_jsonb', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('field_confidences_jsonb', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('field_methods_jsonb', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('field_sources_jsonb', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('extra_fields_jsonb', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('flags_jsonb', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
    sa.Column('row_confidence', sa.Float(), nullable=False),
    sa.Column('review_status', sa.Enum('AUTO_APPROVED', 'NEEDS_REVIEW', 'FAILED', 'MANUALLY_APPROVED', name='reviewstatus', native_enum=False, length=48), nullable=False),
    sa.Column('ocr_used', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('ocr_mean_confidence', sa.Float(), nullable=True),
    sa.Column('detected_language', sa.String(length=32), nullable=True),
    sa.Column('template_id', sa.UUID(), nullable=True),
    sa.Column('processed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('reviewed_by', sa.UUID(), nullable=True),
    sa.Column('reviewed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('row_confidence >= 0 AND row_confidence <= 1', name=op.f('ck_extractions_row_confidence_is_probability')),
    sa.ForeignKeyConstraint(['batch_id'], ['batches.id'], name=op.f('fk_extractions_batch_id_batches'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['reviewed_by'], ['users.id'], name=op.f('fk_extractions_reviewed_by_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['template_id'], ['templates.id'], name=op.f('fk_extractions_template_id_templates'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['unit_id'], ['certificate_units.id'], name=op.f('fk_extractions_unit_id_certificate_units'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], name=op.f('fk_extractions_workspace_id_workspaces'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_extractions'))
    )
    op.create_index('ix_extractions_batch_id_certificate_type', 'extractions', ['batch_id', 'certificate_type'], unique=False)
    op.create_index('ix_extractions_batch_id_review_status', 'extractions', ['batch_id', 'review_status'], unique=False)
    op.create_index(op.f('ix_extractions_created_at'), 'extractions', ['created_at'], unique=False)
    op.create_index('ix_extractions_fields_gin', 'extractions', ['fields_jsonb'], unique=False, postgresql_using='gin')
    op.create_index('ix_extractions_flags_gin', 'extractions', ['flags_jsonb'], unique=False, postgresql_using='gin')
    op.create_index('ix_extractions_review_status', 'extractions', ['review_status'], unique=False)
    op.create_index('ix_extractions_unit_id', 'extractions', ['unit_id'], unique=False)
    op.create_index(op.f('ix_extractions_workspace_id'), 'extractions', ['workspace_id'], unique=False)
    op.create_table('field_corrections',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('extraction_id', sa.UUID(), nullable=False),
    sa.Column('field_name', sa.String(length=128), nullable=False),
    sa.Column('old_value', sa.Text(), nullable=True),
    sa.Column('new_value', sa.Text(), nullable=True),
    sa.Column('corrected_by', sa.UUID(), nullable=True),
    sa.Column('corrected_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['corrected_by'], ['users.id'], name=op.f('fk_field_corrections_corrected_by_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['extraction_id'], ['extractions.id'], name=op.f('fk_field_corrections_extraction_id_extractions'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_field_corrections'))
    )
    op.create_index('ix_field_corrections_corrected_at', 'field_corrections', ['corrected_at'], unique=False)
    op.create_index('ix_field_corrections_extraction_id', 'field_corrections', ['extraction_id'], unique=False)


    # Cycle-breaking constraint: templates.created_from_extraction_id ->
    # extractions.id closes a loop with extractions.template_id. It is declared
    # use_alter in the model, which keeps it out of the inline CREATE TABLE, so it
    # is added here once both tables exist. Autogenerate does not emit this pair.
    op.create_foreign_key(
        "fk_templates_created_from_extraction_id_extractions",
        "templates",
        "extractions",
        ["created_from_extraction_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_templates_created_from_extraction_id_extractions",
        "templates",
        type_="foreignkey",
    )
    op.drop_index('ix_field_corrections_extraction_id', table_name='field_corrections')
    op.drop_index('ix_field_corrections_corrected_at', table_name='field_corrections')
    op.drop_table('field_corrections')
    op.drop_index(op.f('ix_extractions_workspace_id'), table_name='extractions')
    op.drop_index('ix_extractions_unit_id', table_name='extractions')
    op.drop_index('ix_extractions_review_status', table_name='extractions')
    op.drop_index('ix_extractions_flags_gin', table_name='extractions', postgresql_using='gin')
    op.drop_index('ix_extractions_fields_gin', table_name='extractions', postgresql_using='gin')
    op.drop_index(op.f('ix_extractions_created_at'), table_name='extractions')
    op.drop_index('ix_extractions_batch_id_review_status', table_name='extractions')
    op.drop_index('ix_extractions_batch_id_certificate_type', table_name='extractions')
    op.drop_table('extractions')
    op.drop_index('ix_dead_letter_tasks_created_at', table_name='dead_letter_tasks')
    op.drop_index('ix_dead_letter_tasks_batch_id', table_name='dead_letter_tasks')
    op.drop_table('dead_letter_tasks')
    op.drop_index('ix_upload_sessions_expires_at', table_name='upload_sessions')
    op.drop_index(op.f('ix_upload_sessions_created_at'), table_name='upload_sessions')
    op.drop_index('ix_upload_sessions_batch_id', table_name='upload_sessions')
    op.drop_table('upload_sessions')
    op.drop_index('ix_page_texts_document_id_page_number', table_name='page_texts')
    op.drop_index(op.f('ix_page_texts_created_at'), table_name='page_texts')
    op.drop_table('page_texts')
    op.drop_index(op.f('ix_certificate_units_status'), table_name='certificate_units')
    op.drop_index('ix_certificate_units_document_id', table_name='certificate_units')
    op.drop_index(op.f('ix_certificate_units_created_at'), table_name='certificate_units')
    op.drop_index(op.f('ix_certificate_units_certificate_type'), table_name='certificate_units')
    op.drop_index('ix_certificate_units_batch_id_status', table_name='certificate_units')
    op.drop_table('certificate_units')
    op.drop_index('ix_exports_batch_id_created_at', table_name='exports')
    op.drop_table('exports')
    op.drop_index('ix_documents_workspace_id_sha256', table_name='documents')
    op.drop_index(op.f('ix_documents_status'), table_name='documents')
    op.drop_index('ix_documents_sha256', table_name='documents')
    op.drop_index(op.f('ix_documents_created_at'), table_name='documents')
    op.drop_index('ix_documents_batch_id_status', table_name='documents')
    op.drop_index(op.f('ix_documents_batch_id'), table_name='documents')
    op.drop_table('documents')
    op.drop_index('ix_templates_workspace_id_fingerprint', table_name='templates')
    op.drop_index('ix_templates_fingerprint', table_name='templates')
    op.drop_index(op.f('ix_templates_created_at'), table_name='templates')
    op.drop_table('templates')
    op.drop_index('ix_refresh_tokens_user_id_family_id', table_name='refresh_tokens')
    op.drop_index('ix_refresh_tokens_expires_at', table_name='refresh_tokens')
    op.drop_table('refresh_tokens')
    op.drop_index('ix_batches_workspace_id_created_at', table_name='batches')
    op.drop_index(op.f('ix_batches_workspace_id'), table_name='batches')
    op.drop_index(op.f('ix_batches_status'), table_name='batches')
    op.drop_index(op.f('ix_batches_created_at'), table_name='batches')
    op.drop_table('batches')
    op.drop_index('ix_audit_log_workspace_id_created_at', table_name='audit_log')
    op.drop_index('ix_audit_log_entity_type_entity_id', table_name='audit_log')
    op.drop_index(op.f('ix_audit_log_created_at'), table_name='audit_log')
    op.drop_index('ix_audit_log_action', table_name='audit_log')
    op.drop_table('audit_log')
    op.drop_index('ix_users_workspace_id_role', table_name='users')
    op.drop_index(op.f('ix_users_workspace_id'), table_name='users')
    op.drop_index(op.f('ix_users_created_at'), table_name='users')
    op.drop_table('users')
    op.drop_index(op.f('ix_workspaces_created_at'), table_name='workspaces')
    op.drop_table('workspaces')
