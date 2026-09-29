"""Which entry replaced this one, as distinct from which it might repeat.

``duplicate_of_id`` was carrying both meanings: a suspicion raised by detection, and a
decision a person made. They are different facts and a clerk follows only one of them,
so the decision gets its own column and the suspicion is cleared once it is answered.

Existing rows are migrated: an entry already marked superseded has its replacement in
``duplicate_of_id``, which moves across.

Revision ID: 68f025c80b74
Revises: c7b74f67937a
Created: 2026-09-29 23:02:00.046520+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "68f025c80b74"
down_revision: str | None = "c7b74f67937a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "certificates", sa.Column("superseded_by_id", sa.UUID(), nullable=True)
    )
    op.create_foreign_key(
        op.f("fk_certificates_superseded_by_id_certificates"),
        "certificates",
        "certificates",
        ["superseded_by_id"],
        ["id"],
        ondelete="SET NULL",
    )
    # Autogenerate does not compare check constraints, so this one is written by hand
    # to match the model; without it the test schema and a migrated database differ.
    op.create_check_constraint(
        "superseded_by_is_another_row",
        "certificates",
        "superseded_by_id IS NULL OR superseded_by_id <> id",
    )

    # An entry already marked superseded pointed at its replacement through the
    # duplicate column. Move it, and clear the suspicion, which has been answered.
    op.execute(
        """
        UPDATE certificates
           SET superseded_by_id = duplicate_of_id,
               duplicate_of_id = NULL
         WHERE status = 'SUPERSEDED'
           AND duplicate_of_id IS NOT NULL
        """
    )


def downgrade() -> None:
    # Put the replacement back where the old schema kept it, so a downgraded database
    # still knows which entry replaced which.
    op.execute(
        """
        UPDATE certificates
           SET duplicate_of_id = superseded_by_id
         WHERE superseded_by_id IS NOT NULL
           AND duplicate_of_id IS NULL
        """
    )
    op.drop_constraint("ck_certificates_superseded_by_is_another_row", "certificates")
    op.drop_constraint(
        op.f("fk_certificates_superseded_by_id_certificates"),
        "certificates",
        type_="foreignkey",
    )
    op.drop_column("certificates", "superseded_by_id")
