"""Shared pytest fixtures.

Environment variables are set **before** ``certex`` is imported anywhere, because
:func:`certex.config.get_settings` is cached and the module-level engine factories
read it on first use.

Database-backed tests target a dedicated ``certex_test`` database and are skipped
- not failed - when no Postgres is reachable, so the pure-logic suite still runs
on a machine with nothing else installed.
"""

from __future__ import annotations

import os
import pathlib
import tempfile
import uuid
from collections.abc import AsyncIterator

# --- environment must be configured before the first certex import -----------
os.environ.setdefault("APP_ENV", "local")
os.environ.setdefault("LOG_FORMAT", "console")
os.environ.setdefault("LOG_LEVEL", "WARNING")
os.environ.setdefault("SECRET_KEY", "test-secret-key-that-is-long-enough-for-hs256-signing")
os.environ.setdefault("SEED_ENABLED", "false")
os.environ.setdefault("RATE_LIMIT_ENABLED", "false")
os.environ.setdefault("CLAMAV_ENABLED", "false")
# bcrypt at cost 12 makes an auth test suite take minutes. 4 is the library
# minimum and exercises exactly the same code path.
os.environ.setdefault("PASSWORD_BCRYPT_ROUNDS", "4")


# pytest builds `tmp_path` under the system temp directory. If a stale
# `pytest-of-<user>` root there has become unreadable - which happens on Windows
# when a previous run was interrupted - every test that asks for a temp directory
# errors out. Giving pytest its own root sidesteps a machine-local problem that
# has nothing to do with the code under test.
os.environ.setdefault(
    "PYTEST_DEBUG_TEMPROOT",
    str(pathlib.Path(tempfile.gettempdir()) / "certex-pytest"),
)
pathlib.Path(os.environ["PYTEST_DEBUG_TEMPROOT"]).mkdir(parents=True, exist_ok=True)


def _load_test_env_overrides() -> None:
    """Read TEST_* keys from the repository-root .env into the environment.

    Only ``TEST_``-prefixed keys are imported. The rest of that file describes the
    Docker network (``postgres``, ``minio`` as hostnames) and would be actively
    wrong for a suite running on the host, so it is deliberately ignored.
    """
    env_file = pathlib.Path(__file__).resolve().parents[2] / ".env"
    if not env_file.is_file():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        key = key.strip()
        if key.startswith("TEST_"):
            os.environ.setdefault(key, value.strip())


_load_test_env_overrides()

_PG_HOST = os.environ.get("TEST_POSTGRES_HOST", "localhost")
_PG_PORT = os.environ.get("TEST_POSTGRES_PORT", "5432")
_PG_USER = os.environ.get("TEST_POSTGRES_USER", "certex")
_PG_PASSWORD = os.environ.get("TEST_POSTGRES_PASSWORD", "certex")
_TEST_DB = os.environ.get("TEST_POSTGRES_DB", "certex_test")
_ADMIN_DB = os.environ.get("TEST_POSTGRES_ADMIN_DB", "postgres")

_BASE_DSN = f"{_PG_USER}:{_PG_PASSWORD}@{_PG_HOST}:{_PG_PORT}"
os.environ.setdefault("DATABASE_URL", f"postgresql+asyncpg://{_BASE_DSN}/{_TEST_DB}")
os.environ.setdefault("DATABASE_URL_SYNC", f"postgresql+psycopg://{_BASE_DSN}/{_TEST_DB}")
os.environ.setdefault("REDIS_URL", os.environ.get("TEST_REDIS_URL", "redis://localhost:6379/9"))
os.environ.setdefault("CELERY_BROKER_URL", "redis://localhost:6379/10")
os.environ.setdefault("CELERY_RESULT_BACKEND", "redis://localhost:6379/11")
os.environ.setdefault(
    "S3_ENDPOINT_URL", os.environ.get("TEST_S3_ENDPOINT_URL", "http://localhost:9000")
)
os.environ.setdefault("S3_BUCKET", "certex-test")

import pytest  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy import text as sqlalchemy_text  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from certex.config import get_settings  # noqa: E402
from certex.core.security import hash_password  # noqa: E402
from certex.db.base import Base  # noqa: E402
from certex.db.models import User, Workspace  # noqa: E402
from certex.enums import UserRole  # noqa: E402
from certex.logging_setup import configure_logging  # noqa: E402

pytest_plugins: list[str] = []

ADMIN_URL = f"postgresql+asyncpg://{_BASE_DSN}/{_ADMIN_DB}"
TEST_URL = f"postgresql+asyncpg://{_BASE_DSN}/{_TEST_DB}"

TEST_PASSWORD = "correct-horse-battery-staple"


def pytest_configure(config: pytest.Config) -> None:
    configure_logging(get_settings(), force=True)


# ---------------------------------------------------------------------------
# Database availability
# ---------------------------------------------------------------------------
def postgres_reachable() -> bool:
    """Synchronous reachability probe.

    Deliberately not async. ``asyncio.run`` sets the thread's current event loop
    to None when it finishes, which destroys the session-scoped loop that every
    later async test and fixture is bound to - the failure surfaces much later as
    "there is no current event loop" in an unrelated test.
    """
    import psycopg

    dsn = f"postgresql://{_BASE_DSN}/{_ADMIN_DB}"
    try:
        with psycopg.connect(dsn, connect_timeout=3) as connection:
            connection.execute("SELECT 1")
        return True
    except Exception:  # noqa: BLE001 - any failure means "not available here"
        return False


@pytest.fixture(scope="session")
def postgres_available() -> bool:
    return postgres_reachable()


@pytest.fixture(scope="session")
def require_postgres(postgres_available: bool) -> None:
    if not postgres_available:
        pytest.skip(
            f"Postgres is not reachable at {_PG_HOST}:{_PG_PORT}. "
            "Start it with `docker compose up -d postgres`.",
            allow_module_level=False,
        )


def create_test_database() -> None:
    """Create the test database if it does not exist. Synchronous, see above."""
    import psycopg

    dsn = f"postgresql://{_BASE_DSN}/{_ADMIN_DB}"
    with psycopg.connect(dsn, connect_timeout=5, autocommit=True) as connection:
        exists = connection.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s", (_TEST_DB,)
        ).fetchone()
        if not exists:
            connection.execute(f'CREATE DATABASE "{_TEST_DB}"')


@pytest.fixture(scope="session")
async def db_engine(require_postgres: None):
    """Session-scoped engine against a freshly created schema.

    The schema is built from ``Base.metadata`` rather than by running Alembic, so a
    failing migration cannot take the whole unit suite with it. A dedicated
    integration test asserts that the migrations and the models agree.
    """
    create_test_database()
    engine = create_async_engine(TEST_URL, pool_pre_ping=True)
    async with engine.begin() as connection:
        # The register's name indexes are trigram indexes, so the extension has to
        # exist before create_all. The migration does the same thing for a real
        # database; doing it here keeps the models and the schema in agreement.
        await connection.execute(sqlalchemy_text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.fixture
async def db_session(db_engine) -> AsyncIterator[AsyncSession]:
    """Function-scoped session on a transaction that is always rolled back.

    Tests therefore share one schema without sharing rows, and no test can leave
    state behind for the next one.
    """
    connection = await db_engine.connect()
    transaction = await connection.begin()
    factory = async_sessionmaker(bind=connection, expire_on_commit=False, autoflush=False)
    session = factory()

    # Nested SAVEPOINT so a commit() inside application code does not end the
    # outer transaction that provides the rollback.
    await connection.begin_nested()

    try:
        yield session
    finally:
        await session.close()
        if transaction.is_active:
            await transaction.rollback()
        await connection.close()


# ---------------------------------------------------------------------------
# Domain fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
async def workspace(db_session: AsyncSession) -> Workspace:
    record = Workspace(name=f"Test Workspace {uuid.uuid4().hex[:8]}", settings_json={})
    db_session.add(record)
    await db_session.flush()
    return record


@pytest.fixture
async def admin_user(db_session: AsyncSession, workspace: Workspace) -> User:
    return await _make_user(db_session, workspace, UserRole.ADMIN)


@pytest.fixture
async def operator_user(db_session: AsyncSession, workspace: Workspace) -> User:
    return await _make_user(db_session, workspace, UserRole.OPERATOR)


@pytest.fixture
async def viewer_user(db_session: AsyncSession, workspace: Workspace) -> User:
    return await _make_user(db_session, workspace, UserRole.VIEWER)


async def _make_user(session: AsyncSession, workspace: Workspace, role: UserRole) -> User:
    user = User(
        workspace_id=workspace.id,
        # example.com is RFC 2606 reserved for documentation. `.test` and `.invalid`
        # are rejected by email-validator as special-use names, which is correct
        # behaviour we do not want to weaken just to suit a fixture.
        email=f"{role.value.lower()}-{uuid.uuid4().hex[:8]}@example.com",
        password_hash=hash_password(TEST_PASSWORD),
        role=role,
        is_active=True,
    )
    session.add(user)
    await session.flush()
    return user


# ---------------------------------------------------------------------------
# HTTP client
# ---------------------------------------------------------------------------
@pytest.fixture
async def api_client(db_session: AsyncSession) -> AsyncIterator[AsyncClient]:
    """HTTP client bound to the app, with the request session overridden.

    Every request inside a test reuses the test's transaction, so handler writes
    are visible to assertions and are rolled back afterwards.
    """
    from certex.db.session import get_async_session
    from certex.main import create_app

    application = create_app(get_settings())

    async def _override() -> AsyncIterator[AsyncSession]:
        yield db_session

    application.dependency_overrides[get_async_session] = _override

    transport = ASGITransport(app=application)
    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
        follow_redirects=False,
    ) as client:
        yield client

    application.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Object storage
# ---------------------------------------------------------------------------
@pytest.fixture(scope="session")
def object_storage():
    """A provisioned test bucket, or a skip when MinIO is not running.

    Integration tests that touch storage skip rather than fail on a machine with
    nothing running, matching how the database fixtures behave.
    """
    from certex.storage.s3 import ObjectStorage

    storage = ObjectStorage(get_settings())
    try:
        storage.ensure_bucket()
    except Exception as exc:  # noqa: BLE001 - any failure means "not available here"
        pytest.skip(
            f"Object storage is not reachable at {get_settings().s3_endpoint_url} "
            f"({type(exc).__name__}). Start it with `docker compose up -d minio`."
        )
    return storage


@pytest.fixture
def fixture_dir(tmp_path: pathlib.Path) -> pathlib.Path:
    """Per-test directory for generated document fixtures."""
    target = tmp_path / "corpus"
    target.mkdir(parents=True, exist_ok=True)
    return target


@pytest.fixture
async def batch(db_session: AsyncSession, workspace: Workspace, operator_user: User):
    """An empty batch owned by the operator fixture."""
    from certex.db.models import Batch
    from certex.enums import BatchStatus

    record = Batch(
        workspace_id=workspace.id,
        name="Test Batch",
        status=BatchStatus.CREATED,
        settings_json={},
        created_by=operator_user.id,
    )
    db_session.add(record)
    await db_session.flush()
    return record


async def authenticate(client: AsyncClient, user: User) -> None:
    """Sign a client in as the given user for the rest of the test.

    Also pins the CSRF header, exactly as the browser client does: cookie-based
    sessions must echo the readable ``certex_csrf`` cookie on every unsafe
    request, and without it the middleware rejects the call - correctly.
    """
    from certex.core.cookies import CSRF_COOKIE
    from certex.core.middleware import CSRF_HEADER

    response = await client.post(
        "/api/v1/auth/login", json={"email": user.email, "password": TEST_PASSWORD}
    )
    assert response.status_code == 200, response.text

    csrf = response.json()["csrf_token"]
    client.cookies.set(CSRF_COOKIE, csrf, path="/")
    client.headers[CSRF_HEADER] = csrf
