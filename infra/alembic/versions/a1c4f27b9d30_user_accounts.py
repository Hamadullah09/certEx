"""User names and forgotten-password requests.

Two columns on ``users``:

``full_name`` - what a person is called. The app showed an email address everywhere a
name belonged, which is both unfriendly and ambiguous once an office has two people
sharing a mailbox.

``password_reset_requested_at`` - when somebody said they had forgotten their password.
A records office has no mail server, so a reset link cannot be sent; the request is
recorded and an administrator sees it in the user list. That is how it is handled in
the room anyway.

Both are nullable. Every account that exists today predates them and is valid without.

Revision ID: a1c4f27b9d30
Revises: 43404da099e1
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a1c4f27b9d30"
down_revision: str | None = "43404da099e1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("full_name", sa.String(length=200), nullable=True))
    op.add_column(
        "users",
        sa.Column("password_reset_requested_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("users", "password_reset_requested_at")
    op.drop_column("users", "full_name")
