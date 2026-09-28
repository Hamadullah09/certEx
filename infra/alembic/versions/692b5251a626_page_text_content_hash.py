"""Page text content hash: the OCR cache key.

Re-processing a batch, or the same scan uploaded twice, would otherwise spend the
same seconds of CPU per page again. The hash covers the rendered page image and the
OCR recipe, so a change to either misses the cache rather than serving a stale read.

Revision ID: 692b5251a626
Revises: 9a91e557c37e
Created: 2026-09-28 07:05:07.096424+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "692b5251a626"
down_revision: str | None = "9a91e557c37e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "page_texts", sa.Column("content_hash", sa.String(length=64), nullable=True)
    )
    op.create_index(
        op.f("ix_page_texts_content_hash"), "page_texts", ["content_hash"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_page_texts_content_hash"), table_name="page_texts")
    op.drop_column("page_texts", "content_hash")
