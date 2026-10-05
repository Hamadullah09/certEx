"""Certificate types and dynamic schemas: fields defined by operators, not code.

Adds the registry's type/schema/version/field tables and pins each batch to the
exact schema version it was read under. Every new batch column is nullable, so
batches created before this migration keep working against the built-in schema
for their classified type.

Revision ID: dd100515f6bf
Revises: 7c290eb8de58
Created: 2026-09-29 15:11:59.493200+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'dd100515f6bf'
down_revision: str | None = '7c290eb8de58'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('certificate_types',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('workspace_id', sa.UUID(), nullable=False),
    sa.Column('key', sa.String(length=64), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('classifier_key', sa.Enum('BIRTH', 'MARRIAGE', 'DEATH', 'OTHER', name='certificatetype', native_enum=False, length=48), nullable=True),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.Column('is_active', sa.Boolean(), server_default='true', nullable=False),
    sa.Column('created_by', sa.UUID(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_certificate_types_created_by_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], name=op.f('fk_certificate_types_workspace_id_workspaces'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_certificate_types')),
    sa.UniqueConstraint('workspace_id', 'key', name='uq_certificate_types_workspace_id_key')
    )
    op.create_index(op.f('ix_certificate_types_created_at'), 'certificate_types', ['created_at'], unique=False)
    op.create_index(op.f('ix_certificate_types_workspace_id'), 'certificate_types', ['workspace_id'], unique=False)
    op.create_index('ix_certificate_types_workspace_id_position', 'certificate_types', ['workspace_id', 'position'], unique=False)
    op.create_table('certificate_schemas',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('workspace_id', sa.UUID(), nullable=False),
    sa.Column('certificate_type_id', sa.UUID(), nullable=False),
    sa.Column('name', sa.String(length=160), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('is_default', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('created_by', sa.UUID(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['certificate_type_id'], ['certificate_types.id'], name=op.f('fk_certificate_schemas_certificate_type_id_certificate_types'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_certificate_schemas_created_by_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], name=op.f('fk_certificate_schemas_workspace_id_workspaces'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_certificate_schemas')),
    sa.UniqueConstraint('certificate_type_id', 'name', name='uq_certificate_schemas_type_id_name')
    )
    op.create_index(op.f('ix_certificate_schemas_certificate_type_id'), 'certificate_schemas', ['certificate_type_id'], unique=False)
    op.create_index(op.f('ix_certificate_schemas_created_at'), 'certificate_schemas', ['created_at'], unique=False)
    op.create_index('ix_certificate_schemas_workspace_id', 'certificate_schemas', ['workspace_id'], unique=False)
    op.create_table('schema_versions',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('schema_id', sa.UUID(), nullable=False),
    sa.Column('workspace_id', sa.UUID(), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('status', sa.Enum('DRAFT', 'PUBLISHED', 'ARCHIVED', name='schemaversionstatus', native_enum=False, length=48), nullable=False),
    sa.Column('notes', sa.Text(), nullable=True),
    sa.Column('published_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_by', sa.UUID(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('version >= 1', name=op.f('ck_schema_versions_version_is_positive')),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_schema_versions_created_by_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['schema_id'], ['certificate_schemas.id'], name=op.f('fk_schema_versions_schema_id_certificate_schemas'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], name=op.f('fk_schema_versions_workspace_id_workspaces'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_schema_versions')),
    sa.UniqueConstraint('schema_id', 'version', name='uq_schema_versions_schema_id_version')
    )
    op.create_index(op.f('ix_schema_versions_created_at'), 'schema_versions', ['created_at'], unique=False)
    op.create_index('ix_schema_versions_schema_id_status', 'schema_versions', ['schema_id', 'status'], unique=False)
    op.create_index(op.f('ix_schema_versions_workspace_id'), 'schema_versions', ['workspace_id'], unique=False)
    op.create_table('schema_fields',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('schema_version_id', sa.UUID(), nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=128), nullable=False),
    sa.Column('label', sa.String(length=200), nullable=False),
    sa.Column('kind', sa.String(length=32), nullable=False),
    sa.Column('role', sa.Enum('none', 'identifier', 'secondary_reference', 'subject_name', 'party_name', 'father_name', 'mother_name', 'spouse_name', 'birth_date', 'death_date', 'marriage_date', 'event_date', 'registration_date', 'issue_date', 'age', 'sex', 'address', 'place', 'issuing_authority', name='fieldrole', native_enum=False, length=48), nullable=False),
    sa.Column('required', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('searchable', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('is_unique', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('labels_en', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
    sa.Column('labels_ur', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
    sa.Column('validation_jsonb', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.ForeignKeyConstraint(['schema_version_id'], ['schema_versions.id'], name=op.f('fk_schema_fields_schema_version_id_schema_versions'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_schema_fields')),
    sa.UniqueConstraint('schema_version_id', 'name', name='uq_schema_fields_version_id_name')
    )
    op.create_index('ix_schema_fields_schema_version_id_position', 'schema_fields', ['schema_version_id', 'position'], unique=False)
    op.add_column('batches', sa.Column('certificate_type_id', sa.UUID(), nullable=True))
    op.add_column('batches', sa.Column('schema_version_id', sa.UUID(), nullable=True))
    op.add_column('batches', sa.Column('description', sa.Text(), nullable=True))
    op.add_column('batches', sa.Column('year', sa.Integer(), nullable=True))
    op.add_column('batches', sa.Column('registration_office', sa.String(length=200), nullable=True))
    # Backfilled through a temporary default: an office upgrading an existing
    # install already has batches, and NOT NULL with nothing to put in them fails.
    # The default is dropped again so the column still matches the model, which
    # sets it in Python.
    op.add_column(
        'batches',
        sa.Column('needs_review_count', sa.Integer(), nullable=False, server_default='0'),
    )
    op.alter_column('batches', 'needs_review_count', server_default=None)
    op.create_index(op.f('ix_batches_certificate_type_id'), 'batches', ['certificate_type_id'], unique=False)
    op.create_index(op.f('ix_batches_schema_version_id'), 'batches', ['schema_version_id'], unique=False)
    op.create_foreign_key(op.f('fk_batches_certificate_type_id_certificate_types'), 'batches', 'certificate_types', ['certificate_type_id'], ['id'], ondelete='RESTRICT')
    op.create_foreign_key(op.f('fk_batches_schema_version_id_schema_versions'), 'batches', 'schema_versions', ['schema_version_id'], ['id'], ondelete='RESTRICT')


def downgrade() -> None:
    op.drop_constraint(op.f('fk_batches_schema_version_id_schema_versions'), 'batches', type_='foreignkey')
    op.drop_constraint(op.f('fk_batches_certificate_type_id_certificate_types'), 'batches', type_='foreignkey')
    op.drop_index(op.f('ix_batches_schema_version_id'), table_name='batches')
    op.drop_index(op.f('ix_batches_certificate_type_id'), table_name='batches')
    op.drop_column('batches', 'needs_review_count')
    op.drop_column('batches', 'registration_office')
    op.drop_column('batches', 'year')
    op.drop_column('batches', 'description')
    op.drop_column('batches', 'schema_version_id')
    op.drop_column('batches', 'certificate_type_id')
    op.drop_index('ix_schema_fields_schema_version_id_position', table_name='schema_fields')
    op.drop_table('schema_fields')
    op.drop_index(op.f('ix_schema_versions_workspace_id'), table_name='schema_versions')
    op.drop_index('ix_schema_versions_schema_id_status', table_name='schema_versions')
    op.drop_index(op.f('ix_schema_versions_created_at'), table_name='schema_versions')
    op.drop_table('schema_versions')
    op.drop_index('ix_certificate_schemas_workspace_id', table_name='certificate_schemas')
    op.drop_index(op.f('ix_certificate_schemas_created_at'), table_name='certificate_schemas')
    op.drop_index(op.f('ix_certificate_schemas_certificate_type_id'), table_name='certificate_schemas')
    op.drop_table('certificate_schemas')
    op.drop_index('ix_certificate_types_workspace_id_position', table_name='certificate_types')
    op.drop_index(op.f('ix_certificate_types_workspace_id'), table_name='certificate_types')
    op.drop_index(op.f('ix_certificate_types_created_at'), table_name='certificate_types')
    op.drop_table('certificate_types')
