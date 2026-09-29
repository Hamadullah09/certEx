"""The certificate register: entries, the names and dates they are found by,
and the documents they were read from.

Adds four tables. ``certificates`` is the system of record - one row per
certificate the office holds, with the searched columns filled by field role and
the whole row kept in ``values_jsonb`` so a schema an operator invents needs no
migration. ``certificate_names`` and ``certificate_dates`` hold the roles that
repeat, which is what keeps both parties to a marriage findable.
``certificate_documents`` links an entry to its scan by relationship, never by
filename.

Revision ID: 7a56f79f0e90
Revises: dd100515f6bf
Created: 2026-09-29 20:08:05.907848+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "7a56f79f0e90"
down_revision: str | None = "dd100515f6bf"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # The register finds a name by similarity, not by prefix: a differently
    # transliterated or slightly misspelled name still has to reach its record.
    # That needs trigram indexes, which need the extension, so it comes first.
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    op.create_table(
        "certificates",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("certificate_type_id", sa.UUID(), nullable=False),
        sa.Column("schema_version_id", sa.UUID(), nullable=True),
        sa.Column("certificate_number", sa.String(length=120), nullable=False),
        sa.Column("certificate_number_key", sa.String(length=200), nullable=False),
        sa.Column("registration_number", sa.String(length=120), nullable=True),
        sa.Column("registration_number_key", sa.String(length=200), nullable=True),
        sa.Column("primary_name", sa.String(length=300), nullable=True),
        sa.Column("primary_name_key", sa.String(length=200), nullable=True),
        sa.Column("secondary_name", sa.String(length=300), nullable=True),
        sa.Column("secondary_name_key", sa.String(length=200), nullable=True),
        sa.Column("event_date", sa.Date(), nullable=True),
        sa.Column(
            "event_date_role",
            sa.Enum(
                "none",
                "identifier",
                "secondary_reference",
                "subject_name",
                "party_name",
                "father_name",
                "mother_name",
                "spouse_name",
                "birth_date",
                "death_date",
                "marriage_date",
                "event_date",
                "registration_date",
                "issue_date",
                "age",
                "sex",
                "address",
                "place",
                "issuing_authority",
                name="fieldrole",
                native_enum=False,
                length=48,
            ),
            nullable=True,
        ),
        sa.Column("registration_date", sa.Date(), nullable=True),
        sa.Column("issue_date", sa.Date(), nullable=True),
        sa.Column("issuing_authority", sa.String(length=300), nullable=True),
        sa.Column(
            "values_jsonb",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        sa.Column(
            "confidences_jsonb",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        sa.Column(
            "provenance_jsonb",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        sa.Column("row_confidence", sa.Float(), server_default="1.0", nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "ACTIVE",
                "SUPERSEDED",
                "VOID",
                name="certificatestatus",
                native_enum=False,
                length=48,
            ),
            server_default="ACTIVE",
            nullable=False,
        ),
        sa.Column("needs_review", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("record_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column(
            "duplicate_status",
            sa.Enum(
                "NONE",
                "SUSPECTED",
                "CONFIRMED",
                "DISTINCT",
                name="duplicatestatus",
                native_enum=False,
                length=48,
            ),
            server_default="NONE",
            nullable=False,
        ),
        sa.Column("duplicate_of_id", sa.UUID(), nullable=True),
        sa.Column(
            "source",
            sa.Enum(
                "EXTRACTION",
                "IMPORT",
                "MANUAL",
                name="certificatesource",
                native_enum=False,
                length=48,
            ),
            nullable=False,
        ),
        sa.Column("source_extraction_id", sa.UUID(), nullable=True),
        sa.Column("source_batch_id", sa.UUID(), nullable=True),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column("updated_by", sa.UUID(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "certificate_number_key <> ''",
            name=op.f("ck_certificates_certificate_number_is_present"),
        ),
        sa.CheckConstraint(
            "duplicate_of_id IS NULL OR duplicate_of_id <> id",
            name=op.f("ck_certificates_duplicate_of_is_another_row"),
        ),
        sa.CheckConstraint(
            "record_version >= 1",
            name=op.f("ck_certificates_record_version_is_positive"),
        ),
        sa.CheckConstraint(
            "row_confidence >= 0 AND row_confidence <= 1",
            name=op.f("ck_certificates_certificate_confidence_range"),
        ),
        sa.ForeignKeyConstraint(
            ["certificate_type_id"],
            ["certificate_types.id"],
            name=op.f("fk_certificates_certificate_type_id_certificate_types"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_certificates_created_by_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["duplicate_of_id"],
            ["certificates.id"],
            name=op.f("fk_certificates_duplicate_of_id_certificates"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["schema_version_id"],
            ["schema_versions.id"],
            name=op.f("fk_certificates_schema_version_id_schema_versions"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_batch_id"],
            ["batches.id"],
            name=op.f("fk_certificates_source_batch_id_batches"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["source_extraction_id"],
            ["extractions.id"],
            name=op.f("fk_certificates_source_extraction_id_extractions"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by"],
            ["users.id"],
            name=op.f("fk_certificates_updated_by_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_certificates_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_certificates")),
    )
    op.create_index(
        op.f("ix_certificates_certificate_type_id"),
        "certificates",
        ["certificate_type_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_certificates_created_at"), "certificates", ["created_at"], unique=False
    )
    op.create_index(
        "ix_certificates_created_at_id",
        "certificates",
        ["workspace_id", "created_at", "id"],
        unique=False,
    )
    op.create_index(
        "ix_certificates_duplicates",
        "certificates",
        ["workspace_id", "duplicate_status"],
        unique=False,
        postgresql_where=sa.text("duplicate_status <> 'NONE'"),
    )
    op.create_index(
        "ix_certificates_name_key",
        "certificates",
        ["workspace_id", "primary_name_key"],
        unique=False,
    )
    op.create_index(
        "ix_certificates_name_key_trgm",
        "certificates",
        ["primary_name_key"],
        unique=False,
        postgresql_using="gin",
        postgresql_ops={"primary_name_key": "gin_trgm_ops"},
    )
    op.create_index(
        "ix_certificates_number_key",
        "certificates",
        ["workspace_id", "certificate_number_key"],
        unique=False,
    )
    op.create_index(
        "ix_certificates_registration_key",
        "certificates",
        ["workspace_id", "registration_number_key"],
        unique=False,
        postgresql_where=sa.text("registration_number_key IS NOT NULL"),
    )
    op.create_index(
        op.f("ix_certificates_source_batch_id"),
        "certificates",
        ["source_batch_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_certificates_status"), "certificates", ["status"], unique=False
    )
    op.create_index(
        "ix_certificates_type_event",
        "certificates",
        ["workspace_id", "certificate_type_id", "event_date"],
        unique=False,
    )
    op.create_index(
        "ix_certificates_values_gin",
        "certificates",
        ["values_jsonb"],
        unique=False,
        postgresql_using="gin",
    )
    op.create_index(
        op.f("ix_certificates_workspace_id"),
        "certificates",
        ["workspace_id"],
        unique=False,
    )
    op.create_table(
        "certificate_dates",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("certificate_id", sa.UUID(), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column(
            "role",
            sa.Enum(
                "none",
                "identifier",
                "secondary_reference",
                "subject_name",
                "party_name",
                "father_name",
                "mother_name",
                "spouse_name",
                "birth_date",
                "death_date",
                "marriage_date",
                "event_date",
                "registration_date",
                "issue_date",
                "age",
                "sex",
                "address",
                "place",
                "issuing_authority",
                name="fieldrole",
                native_enum=False,
                length=48,
            ),
            nullable=False,
        ),
        sa.Column("field_name", sa.String(length=64), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("value", sa.Date(), nullable=False),
        sa.ForeignKeyConstraint(
            ["certificate_id"],
            ["certificates.id"],
            name=op.f("fk_certificate_dates_certificate_id_certificates"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_certificate_dates_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_certificate_dates")),
        sa.UniqueConstraint(
            "certificate_id",
            "field_name",
            name="uq_certificate_dates_certificate_id_field",
        ),
    )
    op.create_index(
        op.f("ix_certificate_dates_certificate_id"),
        "certificate_dates",
        ["certificate_id"],
        unique=False,
    )
    op.create_index(
        "ix_certificate_dates_role_value",
        "certificate_dates",
        ["workspace_id", "role", "value"],
        unique=False,
    )
    op.create_table(
        "certificate_documents",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("certificate_id", sa.UUID(), nullable=False),
        sa.Column("document_id", sa.UUID(), nullable=False),
        sa.Column("unit_id", sa.UUID(), nullable=True),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column(
            "kind",
            sa.Enum(
                "PRIMARY",
                "SUPPORTING",
                "SUPERSEDED",
                name="documentlinkkind",
                native_enum=False,
                length=48,
            ),
            nullable=False,
        ),
        sa.Column("page_start", sa.Integer(), nullable=True),
        sa.Column("page_end", sa.Integer(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("linked_by", sa.UUID(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "page_end IS NULL OR page_start IS NULL OR page_end >= page_start",
            name=op.f("ck_certificate_documents_certificate_document_pages_ordered"),
        ),
        sa.ForeignKeyConstraint(
            ["certificate_id"],
            ["certificates.id"],
            name=op.f("fk_certificate_documents_certificate_id_certificates"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name=op.f("fk_certificate_documents_document_id_documents"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["linked_by"],
            ["users.id"],
            name=op.f("fk_certificate_documents_linked_by_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["unit_id"],
            ["certificate_units.id"],
            name=op.f("fk_certificate_documents_unit_id_certificate_units"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_certificate_documents_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_certificate_documents")),
    )
    op.create_index(
        op.f("ix_certificate_documents_certificate_id"),
        "certificate_documents",
        ["certificate_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_certificate_documents_created_at"),
        "certificate_documents",
        ["created_at"],
        unique=False,
    )
    op.create_index(
        "ix_certificate_documents_document_id",
        "certificate_documents",
        ["document_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_certificate_documents_workspace_id"),
        "certificate_documents",
        ["workspace_id"],
        unique=False,
    )
    op.create_index(
        "uq_certificate_documents_unit",
        "certificate_documents",
        ["certificate_id", "document_id", "unit_id"],
        unique=True,
        postgresql_where=sa.text("unit_id IS NOT NULL"),
    )
    op.create_index(
        "uq_certificate_documents_whole",
        "certificate_documents",
        ["certificate_id", "document_id"],
        unique=True,
        postgresql_where=sa.text("unit_id IS NULL"),
    )
    op.create_table(
        "certificate_names",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("certificate_id", sa.UUID(), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column(
            "role",
            sa.Enum(
                "none",
                "identifier",
                "secondary_reference",
                "subject_name",
                "party_name",
                "father_name",
                "mother_name",
                "spouse_name",
                "birth_date",
                "death_date",
                "marriage_date",
                "event_date",
                "registration_date",
                "issue_date",
                "age",
                "sex",
                "address",
                "place",
                "issuing_authority",
                name="fieldrole",
                native_enum=False,
                length=48,
            ),
            nullable=False,
        ),
        sa.Column("field_name", sa.String(length=64), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("value", sa.String(length=300), nullable=False),
        sa.Column("value_key", sa.String(length=200), nullable=False),
        sa.CheckConstraint(
            "value_key <> ''",
            name=op.f("ck_certificate_names_certificate_name_key_is_present"),
        ),
        sa.ForeignKeyConstraint(
            ["certificate_id"],
            ["certificates.id"],
            name=op.f("fk_certificate_names_certificate_id_certificates"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_certificate_names_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_certificate_names")),
        sa.UniqueConstraint(
            "certificate_id",
            "field_name",
            name="uq_certificate_names_certificate_id_field",
        ),
    )
    op.create_index(
        op.f("ix_certificate_names_certificate_id"),
        "certificate_names",
        ["certificate_id"],
        unique=False,
    )
    op.create_index(
        "ix_certificate_names_key",
        "certificate_names",
        ["workspace_id", "value_key"],
        unique=False,
    )
    op.create_index(
        "ix_certificate_names_key_trgm",
        "certificate_names",
        ["value_key"],
        unique=False,
        postgresql_using="gin",
        postgresql_ops={"value_key": "gin_trgm_ops"},
    )
    op.create_index(
        "ix_certificate_names_role_key",
        "certificate_names",
        ["workspace_id", "role", "value_key"],
        unique=False,
    )
    # ### end Alembic commands ###


def downgrade() -> None:
    # pg_trgm is left in place deliberately. Dropping an extension is a database
    # level decision that could break anything else using it, and an unused
    # extension costs nothing.
    op.drop_index("ix_certificate_names_role_key", table_name="certificate_names")
    op.drop_index(
        "ix_certificate_names_key_trgm",
        table_name="certificate_names",
        postgresql_using="gin",
        postgresql_ops={"value_key": "gin_trgm_ops"},
    )
    op.drop_index("ix_certificate_names_key", table_name="certificate_names")
    op.drop_index(
        op.f("ix_certificate_names_certificate_id"), table_name="certificate_names"
    )
    op.drop_table("certificate_names")
    op.drop_index(
        "uq_certificate_documents_whole",
        table_name="certificate_documents",
        postgresql_where=sa.text("unit_id IS NULL"),
    )
    op.drop_index(
        "uq_certificate_documents_unit",
        table_name="certificate_documents",
        postgresql_where=sa.text("unit_id IS NOT NULL"),
    )
    op.drop_index(
        op.f("ix_certificate_documents_workspace_id"),
        table_name="certificate_documents",
    )
    op.drop_index(
        "ix_certificate_documents_document_id", table_name="certificate_documents"
    )
    op.drop_index(
        op.f("ix_certificate_documents_created_at"), table_name="certificate_documents"
    )
    op.drop_index(
        op.f("ix_certificate_documents_certificate_id"),
        table_name="certificate_documents",
    )
    op.drop_table("certificate_documents")
    op.drop_index("ix_certificate_dates_role_value", table_name="certificate_dates")
    op.drop_index(
        op.f("ix_certificate_dates_certificate_id"), table_name="certificate_dates"
    )
    op.drop_table("certificate_dates")
    op.drop_index(op.f("ix_certificates_workspace_id"), table_name="certificates")
    op.drop_index(
        "ix_certificates_values_gin", table_name="certificates", postgresql_using="gin"
    )
    op.drop_index("ix_certificates_type_event", table_name="certificates")
    op.drop_index(op.f("ix_certificates_status"), table_name="certificates")
    op.drop_index(op.f("ix_certificates_source_batch_id"), table_name="certificates")
    op.drop_index(
        "ix_certificates_registration_key",
        table_name="certificates",
        postgresql_where=sa.text("registration_number_key IS NOT NULL"),
    )
    op.drop_index("ix_certificates_number_key", table_name="certificates")
    op.drop_index(
        "ix_certificates_name_key_trgm",
        table_name="certificates",
        postgresql_using="gin",
        postgresql_ops={"primary_name_key": "gin_trgm_ops"},
    )
    op.drop_index("ix_certificates_name_key", table_name="certificates")
    op.drop_index(
        "ix_certificates_duplicates",
        table_name="certificates",
        postgresql_where=sa.text("duplicate_status <> 'NONE'"),
    )
    op.drop_index("ix_certificates_created_at_id", table_name="certificates")
    op.drop_index(op.f("ix_certificates_created_at"), table_name="certificates")
    op.drop_index(
        op.f("ix_certificates_certificate_type_id"), table_name="certificates"
    )
    op.drop_table("certificates")
    # ### end Alembic commands ###
