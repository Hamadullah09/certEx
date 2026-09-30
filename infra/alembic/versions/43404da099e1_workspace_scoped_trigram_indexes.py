"""Put the workspace inside the trigram indexes.

Every name search is scoped to one office, and a trigram index on the name alone cannot
satisfy that scope: the planner uses the workspace B-tree instead and applies the
similarity as a filter, computing it for every entry the office holds. btree_gin lets
``workspace_id`` live in the GIN index beside the trigram column, which gives Postgres
something it can use as the register grows.

Revision ID: 43404da099e1
Revises: 68f025c80b74
Created: 2026-09-30 08:16:49.852029+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "43404da099e1"
down_revision: str | None = "68f025c80b74"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Needed before a uuid column can sit in a GIN index.
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gin")

    op.drop_index(
        "ix_certificate_names_key_trgm",
        table_name="certificate_names",
        postgresql_using="gin",
    )
    op.create_index(
        "ix_certificate_names_key_trgm",
        "certificate_names",
        ["workspace_id", "value_key"],
        unique=False,
        postgresql_using="gin",
        postgresql_ops={"workspace_id": "uuid_ops", "value_key": "gin_trgm_ops"},
    )
    op.drop_index(
        "ix_certificates_name_key_trgm",
        table_name="certificates",
        postgresql_using="gin",
    )
    op.create_index(
        "ix_certificates_name_key_trgm",
        "certificates",
        ["workspace_id", "primary_name_key"],
        unique=False,
        postgresql_using="gin",
        postgresql_ops={"workspace_id": "uuid_ops", "primary_name_key": "gin_trgm_ops"},
    )


def downgrade() -> None:
    # btree_gin is left in place: dropping an extension is a database-level decision
    # that could break anything else using it, and an unused extension costs nothing.
    op.drop_index(
        "ix_certificates_name_key_trgm",
        table_name="certificates",
        postgresql_using="gin",
        postgresql_ops={"workspace_id": "uuid_ops", "primary_name_key": "gin_trgm_ops"},
    )
    op.create_index(
        "ix_certificates_name_key_trgm",
        "certificates",
        ["primary_name_key"],
        unique=False,
        postgresql_using="gin",
    )
    op.drop_index(
        "ix_certificate_names_key_trgm",
        table_name="certificate_names",
        postgresql_using="gin",
        postgresql_ops={"workspace_id": "uuid_ops", "value_key": "gin_trgm_ops"},
    )
    op.create_index(
        "ix_certificate_names_key_trgm",
        "certificate_names",
        ["value_key"],
        unique=False,
        postgresql_using="gin",
    )
