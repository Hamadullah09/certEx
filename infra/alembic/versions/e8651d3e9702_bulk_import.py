"""Bulk import: a CSV of existing records, and the rows it could not file.

``certificate_imports`` is what an operator watches while a worker reads a file that
takes minutes; its counters are updated as the read progresses rather than derived,
because deriving them would mean counting a million rows on every page refresh.
``import_row_errors`` keeps one row per failure, in terms a clerk can act on - rows are
never dropped in silence.

Revision ID: e8651d3e9702
Revises: 7a56f79f0e90
Created: 2026-09-29 21:43:00.312013+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "e8651d3e9702"
down_revision: str | None = "7a56f79f0e90"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "certificate_imports",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("certificate_type_id", sa.UUID(), nullable=False),
        sa.Column("schema_version_id", sa.UUID(), nullable=True),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("storage_key", sa.String(length=512), nullable=False),
        sa.Column("byte_size", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "PENDING",
                "RUNNING",
                "PARTIAL",
                "COMPLETED",
                "FAILED",
                "CANCELLED",
                name="importstatus",
                native_enum=False,
                length=48,
            ),
            nullable=False,
        ),
        sa.Column(
            "duplicate_policy",
            sa.Enum(
                "SKIP",
                "RECORD_AS_DUPLICATE",
                name="importduplicatepolicy",
                native_enum=False,
                length=48,
            ),
            nullable=False,
        ),
        sa.Column("delimiter", sa.String(length=8), nullable=False),
        sa.Column("encoding", sa.String(length=32), nullable=True),
        sa.Column("total_rows", sa.Integer(), server_default="0", nullable=False),
        sa.Column("created_rows", sa.Integer(), server_default="0", nullable=False),
        sa.Column("skipped_rows", sa.Integer(), server_default="0", nullable=False),
        sa.Column("failed_rows", sa.Integer(), server_default="0", nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.UUID(), nullable=True),
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
            "created_rows + skipped_rows + failed_rows <= total_rows",
            name=op.f("ck_certificate_imports_import_counts_within_total"),
        ),
        sa.CheckConstraint(
            "total_rows >= 0",
            name=op.f("ck_certificate_imports_import_total_rows_not_negative"),
        ),
        sa.ForeignKeyConstraint(
            ["certificate_type_id"],
            ["certificate_types.id"],
            name=op.f("fk_certificate_imports_certificate_type_id_certificate_types"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_certificate_imports_created_by_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["schema_version_id"],
            ["schema_versions.id"],
            name=op.f("fk_certificate_imports_schema_version_id_schema_versions"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_certificate_imports_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_certificate_imports")),
    )
    op.create_index(
        op.f("ix_certificate_imports_created_at"),
        "certificate_imports",
        ["created_at"],
        unique=False,
    )
    op.create_index(
        "ix_certificate_imports_status",
        "certificate_imports",
        ["workspace_id", "status"],
        unique=False,
    )
    op.create_index(
        "ix_certificate_imports_workspace_created",
        "certificate_imports",
        ["workspace_id", "created_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_certificate_imports_workspace_id"),
        "certificate_imports",
        ["workspace_id"],
        unique=False,
    )
    op.create_table(
        "import_row_errors",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("import_id", sa.UUID(), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("row_number", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("field_name", sa.String(length=64), nullable=True),
        sa.Column("certificate_number", sa.String(length=120), nullable=True),
        sa.Column("value_excerpt", sa.String(length=200), nullable=True),
        sa.CheckConstraint(
            "row_number >= 1",
            name=op.f("ck_import_row_errors_import_error_row_number_is_positive"),
        ),
        sa.ForeignKeyConstraint(
            ["import_id"],
            ["certificate_imports.id"],
            name=op.f("fk_import_row_errors_import_id_certificate_imports"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_import_row_errors_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_import_row_errors")),
    )
    op.create_index(
        "ix_import_row_errors_import_row",
        "import_row_errors",
        ["import_id", "row_number"],
        unique=False,
    )
    op.create_index(
        op.f("ix_import_row_errors_workspace_id"),
        "import_row_errors",
        ["workspace_id"],
        unique=False,
    )
    # ### end Alembic commands ###


def downgrade() -> None:
    op.drop_index(
        op.f("ix_import_row_errors_workspace_id"), table_name="import_row_errors"
    )
    op.drop_index("ix_import_row_errors_import_row", table_name="import_row_errors")
    op.drop_table("import_row_errors")
    op.drop_index(
        op.f("ix_certificate_imports_workspace_id"), table_name="certificate_imports"
    )
    op.drop_index(
        "ix_certificate_imports_workspace_created", table_name="certificate_imports"
    )
    op.drop_index("ix_certificate_imports_status", table_name="certificate_imports")
    op.drop_index(
        op.f("ix_certificate_imports_created_at"), table_name="certificate_imports"
    )
    op.drop_table("certificate_imports")
    # ### end Alembic commands ###
