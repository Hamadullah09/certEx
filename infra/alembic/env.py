"""Alembic environment.

Reads the connection URL from :mod:`certex.config` rather than ``alembic.ini`` so
migrations cannot drift onto a different database than the application uses, and
so no credential lives in a committed file.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from certex.config import get_settings
from certex.db.base import Base

# Importing the models module registers every table on Base.metadata. Without this
# import, autogenerate would believe the schema is empty and emit drop statements.
from certex.db import models as _models  # noqa: F401

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

settings = get_settings()

# alembic.ini deliberately carries no sqlalchemy.url, so ordinary CLI use resolves
# the target from configuration and cannot drift onto a different database than
# the application talks to. A caller that sets the option explicitly - the test
# suite pointing at a scratch database, or `alembic -x` for a one-off - overrides
# it, which has to be a deliberate act rather than an accident.
_explicit_url = config.get_main_option("sqlalchemy.url", None)
DATABASE_URL = _explicit_url or settings.database_url_sync
config.set_main_option("sqlalchemy.url", DATABASE_URL)

target_metadata = Base.metadata


def include_object(
    obj: object,
    name: str | None,
    type_: str,
    reflected: bool,
    compare_to: object,
) -> bool:
    """Keep autogenerate focused on application tables."""
    if type_ == "table" and name in {"alembic_version"}:
        return False
    return True


def run_migrations_offline() -> None:
    """Emit SQL to stdout without connecting - used to review a migration."""
    context.configure(
        url=DATABASE_URL,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
            include_object=include_object,
            # One transaction per migration: a failure rolls back cleanly instead of
            # leaving the schema half-applied.
            transaction_per_migration=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
