"""Migrations must describe exactly what the models declare.

Drift between ``Base.metadata`` and the migration chain is the classic way a
deployment fails at 2am: tests pass against ``create_all`` while production runs
``alembic upgrade head`` and gets a different schema. These tests run the real
migration chain against a scratch database and diff the result.
"""

from __future__ import annotations

import pathlib
from typing import ClassVar

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import Engine, create_engine, text

from certex.db.base import Base

pytestmark = [pytest.mark.integration]

MIGRATION_DB = "certex_migrations_test"


def _repo_root() -> pathlib.Path:
    return pathlib.Path(__file__).resolve().parents[3]


def _sync_url(engine: Engine) -> str:
    """Render the URL with credentials intact - ``str(url)`` masks the password."""
    return engine.url.render_as_string(hide_password=False)


def _alembic_config(sync_url: str) -> Config:
    config = Config(str(_repo_root() / "infra" / "alembic.ini"))
    config.set_main_option("script_location", str(_repo_root() / "infra" / "alembic"))
    config.set_main_option("sqlalchemy.url", sync_url)
    # The autogenerate formatter is a developer convenience; running it inside the
    # test would shell out for no benefit.
    config.set_section_option("post_write_hooks", "hooks", "")
    return config


@pytest.fixture(scope="module")
def migration_engine(request: pytest.FixtureRequest) -> Engine:
    """A scratch database that only the migration tests touch."""
    from tests.conftest import _ADMIN_DB, _BASE_DSN, postgres_reachable

    if not postgres_reachable():
        pytest.skip("Postgres is not reachable; start it with `docker compose up -d postgres`.")

    admin_url = f"postgresql+psycopg://{_BASE_DSN}/{_ADMIN_DB}"
    target_url = f"postgresql+psycopg://{_BASE_DSN}/{MIGRATION_DB}"

    admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(text(f'DROP DATABASE IF EXISTS "{MIGRATION_DB}" WITH (FORCE)'))
        connection.execute(text(f'CREATE DATABASE "{MIGRATION_DB}"'))
    admin.dispose()

    engine = create_engine(target_url)

    def _teardown() -> None:
        engine.dispose()
        cleanup = create_engine(admin_url, isolation_level="AUTOCOMMIT")
        with cleanup.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{MIGRATION_DB}" WITH (FORCE)'))
        cleanup.dispose()

    request.addfinalizer(_teardown)
    return engine


class TestMigrationChain:
    def test_upgrade_head_produces_the_declared_schema(self, migration_engine: Engine) -> None:
        """`alembic upgrade head` must leave zero difference from the models."""
        config = _alembic_config(_sync_url(migration_engine))
        command.upgrade(config, "head")

        with migration_engine.connect() as connection:
            context = MigrationContext.configure(
                connection,
                opts={"compare_type": True, "compare_server_default": True},
            )
            diff = compare_metadata(context, Base.metadata)

        # alembic_version is Alembic's own bookkeeping table and is not modelled.
        meaningful = [
            entry
            for entry in diff
            if not (
                isinstance(entry, tuple)
                and len(entry) > 1
                and getattr(entry[1], "name", None) == "alembic_version"
            )
        ]
        assert meaningful == [], f"models and migrations disagree: {meaningful}"

    def test_every_model_table_exists(self, migration_engine: Engine) -> None:
        config = _alembic_config(_sync_url(migration_engine))
        command.upgrade(config, "head")

        with migration_engine.connect() as connection:
            rows = connection.execute(
                text(
                    "SELECT tablename FROM pg_tables "
                    "WHERE schemaname = 'public' AND tablename <> 'alembic_version'"
                )
            )
            present = {row[0] for row in rows}

        assert set(Base.metadata.tables) == present

    def test_downgrade_removes_everything(self, migration_engine: Engine) -> None:
        """A reversible chain is what makes a failed release recoverable."""
        config = _alembic_config(_sync_url(migration_engine))
        command.upgrade(config, "head")
        command.downgrade(config, "base")

        with migration_engine.connect() as connection:
            rows = connection.execute(
                text(
                    "SELECT tablename FROM pg_tables "
                    "WHERE schemaname = 'public' AND tablename <> 'alembic_version'"
                )
            )
            assert {row[0] for row in rows} == set()

    def test_upgrade_is_repeatable_after_downgrade(self, migration_engine: Engine) -> None:
        config = _alembic_config(_sync_url(migration_engine))
        command.upgrade(config, "head")
        command.downgrade(config, "base")
        command.upgrade(config, "head")

        with migration_engine.connect() as connection:
            version = connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
        assert version


class TestIndexes:
    """The indexes the specification calls out by name must actually exist."""

    REQUIRED: ClassVar[set[tuple[str, str]]] = {
        ("documents", "ix_documents_sha256"),
        ("documents", "ix_documents_workspace_id_sha256"),
        ("certificate_units", "ix_certificate_units_document_id"),
        ("extractions", "ix_extractions_unit_id"),
        ("extractions", "ix_extractions_review_status"),
        ("templates", "ix_templates_fingerprint"),
    }

    def test_required_indexes_present(self, migration_engine: Engine) -> None:
        config = _alembic_config(_sync_url(migration_engine))
        command.upgrade(config, "head")

        with migration_engine.connect() as connection:
            rows = connection.execute(
                text("SELECT tablename, indexname FROM pg_indexes WHERE schemaname = 'public'")
            )
            present = {(row[0], row[1]) for row in rows}

        missing = self.REQUIRED - present
        assert not missing, f"missing indexes: {sorted(missing)}"

    def test_jsonb_columns_use_gin(self, migration_engine: Engine) -> None:
        """Flag and field queries must stay indexed rather than seq-scanning."""
        config = _alembic_config(_sync_url(migration_engine))
        command.upgrade(config, "head")

        with migration_engine.connect() as connection:
            rows = connection.execute(
                text(
                    "SELECT indexname, indexdef FROM pg_indexes "
                    "WHERE schemaname = 'public' AND tablename = 'extractions'"
                )
            )
            gin = {row[0] for row in rows if "USING gin" in row[1]}

        assert "ix_extractions_flags_gin" in gin
        assert "ix_extractions_fields_gin" in gin
