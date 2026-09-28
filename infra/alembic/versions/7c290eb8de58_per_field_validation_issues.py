"""Per-field validation issues: which value to look at, and why.

A row's flags say what is wrong with it; a reviewer needs to know which field raised
each one before they can fix anything. The flags stay flat and indexed for filtering,
and the detail lives here beside them.

Revision ID: 7c290eb8de58
Revises: 692b5251a626
Created: 2026-09-28 11:04:41.143732+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "7c290eb8de58"
down_revision: str | None = "692b5251a626"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "extractions",
        sa.Column(
            "field_issues_jsonb",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("extractions", "field_issues_jsonb")
