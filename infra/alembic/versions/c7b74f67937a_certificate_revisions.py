"""A register entry's history.

The register is a historical record: somebody may be holding a certificate issued
from what an entry used to say, so a correction cannot overwrite it. Each revision
keeps the whole value set as it stood afterwards, which field moved, who changed it
and why - a diff chain would answer "what did this say in March" only by replaying
itself, and an entry is a few hundred bytes.

Revision ID: c7b74f67937a
Revises: e8651d3e9702
Created: 2026-09-29 22:35:52.680090+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c7b74f67937a"
down_revision: str | None = "e8651d3e9702"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "certificate_revisions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("certificate_id", sa.UUID(), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("record_version", sa.Integer(), nullable=False),
        sa.Column(
            "action",
            sa.Enum(
                "CREATED",
                "CORRECTED",
                "APPROVED",
                "VOIDED",
                "SUPERSEDED",
                "DUPLICATE_RESOLVED",
                "DOCUMENT_ATTACHED",
                "DOCUMENT_REPLACED",
                name="revisionaction",
                native_enum=False,
                length=48,
            ),
            nullable=False,
        ),
        sa.Column(
            "values_jsonb",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        sa.Column(
            "changed_fields",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("actor_id", sa.UUID(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "record_version >= 1",
            name=op.f("ck_certificate_revisions_revision_version_is_positive"),
        ),
        sa.ForeignKeyConstraint(
            ["actor_id"],
            ["users.id"],
            name=op.f("fk_certificate_revisions_actor_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["certificate_id"],
            ["certificates.id"],
            name=op.f("fk_certificate_revisions_certificate_id_certificates"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_certificate_revisions_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_certificate_revisions")),
        sa.UniqueConstraint(
            "certificate_id", "record_version", name="uq_certificate_revisions_version"
        ),
    )
    op.create_index(
        "ix_certificate_revisions_certificate",
        "certificate_revisions",
        ["certificate_id", "record_version"],
        unique=False,
    )
    op.create_index(
        op.f("ix_certificate_revisions_created_at"),
        "certificate_revisions",
        ["created_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_certificate_revisions_workspace_id"),
        "certificate_revisions",
        ["workspace_id"],
        unique=False,
    )
    # ### end Alembic commands ###


def downgrade() -> None:
    op.drop_index(
        op.f("ix_certificate_revisions_workspace_id"),
        table_name="certificate_revisions",
    )
    op.drop_index(
        op.f("ix_certificate_revisions_created_at"), table_name="certificate_revisions"
    )
    op.drop_index(
        "ix_certificate_revisions_certificate", table_name="certificate_revisions"
    )
    op.drop_table("certificate_revisions")
    # ### end Alembic commands ###
