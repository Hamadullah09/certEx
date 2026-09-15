"""Operational CLI.

    python -m certex.cli seed
    python -m certex.cli ensure-bucket
    python -m certex.cli create-user --email a@b.c --password ... --role ADMIN
    python -m certex.cli check

Every command is idempotent so container entrypoints can run them on each boot.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from sqlalchemy import select

from certex.config import Settings, get_settings
from certex.core.security import hash_password
from certex.db.models import User, Workspace
from certex.db.session import session_scope
from certex.enums import UserRole
from certex.logging_setup import configure_logging, get_logger, safe_error
from certex.storage.s3 import get_object_storage

logger = get_logger(__name__)


def _seed(settings: Settings) -> int:
    """Create the demo workspace and its three role accounts.

    Refuses to run when ``APP_ENV=production`` - the config validator already
    rejects that combination, but a second guard here means an operator who edits
    the entrypoint cannot accidentally plant known credentials in production.
    """
    if not settings.seed_enabled:
        print("[seed] SEED_ENABLED is false; nothing to do")
        return 0
    if settings.is_production:
        print("[seed] refusing to seed demo accounts in production", file=sys.stderr)
        return 1

    accounts = (
        (settings.seed_admin_email, settings.seed_admin_password, UserRole.ADMIN),
        (settings.seed_operator_email, settings.seed_operator_password, UserRole.OPERATOR),
        (settings.seed_viewer_email, settings.seed_viewer_password, UserRole.VIEWER),
    )

    with session_scope() as session:
        workspace = session.scalar(
            select(Workspace).where(Workspace.name == settings.seed_workspace_name)
        )
        if workspace is None:
            workspace = Workspace(
                name=settings.seed_workspace_name,
                settings_json={
                    "confidence_auto_approve": settings.confidence_auto_approve,
                    "confidence_review_floor": settings.confidence_review_floor,
                    "ocr_languages": settings.ocr_languages,
                    "llm_enabled": settings.llm_enabled,
                },
            )
            session.add(workspace)
            session.flush()
            print(f"[seed] created workspace {workspace.name!r}")
        else:
            print(f"[seed] workspace {workspace.name!r} already exists")

        created = 0
        for email, password, role in accounts:
            normalised = email.strip().lower()
            existing = session.scalar(select(User).where(User.email == normalised))
            if existing is not None:
                continue
            session.add(
                User(
                    workspace_id=workspace.id,
                    email=normalised,
                    password_hash=hash_password(password.get_secret_value()),
                    role=role,
                    is_active=True,
                )
            )
            created += 1
            print(f"[seed] created {role.value.lower()} account {normalised}")

        if created == 0:
            print("[seed] all demo accounts already present")
    return 0


def _ensure_bucket(settings: Settings) -> int:
    storage = get_object_storage()
    try:
        created = storage.ensure_bucket()
    except Exception as exc:  # noqa: BLE001 - CLI boundary, report and exit non-zero
        print(f"[storage] {safe_error(exc)}", file=sys.stderr)
        return 1

    print(f"[storage] bucket {storage.bucket!r} {'created' if created else 'ready'}")

    if not storage.verify_encryption_supported():
        print(
            f"[storage] WARNING: S3_SSE={settings.s3_sse.value} was rejected by the "
            "backend. Objects will NOT be encrypted at rest. For MinIO, set "
            "MINIO_KMS_SECRET_KEY; for AWS, check the bucket policy and KMS grants.",
            file=sys.stderr,
        )
        return 1
    if settings.s3_sse.value != "none":
        print(f"[storage] server-side encryption verified ({settings.s3_sse.value})")
    return 0


def _create_user(settings: Settings, email: str, password: str, role: UserRole) -> int:
    normalised = email.strip().lower()
    with session_scope() as session:
        if session.scalar(select(User).where(User.email == normalised)) is not None:
            print(f"[user] {normalised} already exists", file=sys.stderr)
            return 1
        workspace = session.scalar(select(Workspace).order_by(Workspace.created_at))
        if workspace is None:
            workspace = Workspace(name=settings.seed_workspace_name)
            session.add(workspace)
            session.flush()
        session.add(
            User(
                workspace_id=workspace.id,
                email=normalised,
                password_hash=hash_password(password),
                role=role,
                is_active=True,
            )
        )
    print(f"[user] created {normalised} as {role.value} in workspace {workspace.name!r}")
    return 0


def _check(settings: Settings) -> int:
    """Verify the process can reach every dependency it needs. Used in CI."""
    failures: list[str] = []

    try:
        with session_scope() as session:
            session.execute(select(1))
        print("[check] database ok")
    except Exception as exc:  # noqa: BLE001
        failures.append(f"database: {safe_error(exc)}")

    try:
        get_object_storage().ping()
        print("[check] object storage ok")
    except Exception as exc:  # noqa: BLE001
        failures.append(f"storage: {safe_error(exc)}")

    try:
        import redis

        redis.from_url(  # type: ignore[no-untyped-call]
            settings.celery_broker_url, socket_connect_timeout=3
        ).ping()
        print("[check] broker ok")
    except Exception as exc:  # noqa: BLE001
        failures.append(f"broker: {safe_error(exc)}")

    for failure in failures:
        print(f"[check] FAIL {failure}", file=sys.stderr)
    return 1 if failures else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="certex", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("seed", help="Create the demo workspace and role accounts.")
    sub.add_parser("ensure-bucket", help="Create the storage bucket and verify encryption.")
    sub.add_parser("check", help="Verify database, storage and broker connectivity.")

    create = sub.add_parser("create-user", help="Create a user account.")
    create.add_argument("--email", required=True)
    create.add_argument("--password", required=True)
    create.add_argument(
        "--role",
        required=True,
        choices=[role.value for role in UserRole],
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    settings = get_settings()
    configure_logging(settings)
    args = build_parser().parse_args(argv)

    if args.command == "seed":
        return _seed(settings)
    if args.command == "ensure-bucket":
        return _ensure_bucket(settings)
    if args.command == "check":
        return _check(settings)
    if args.command == "create-user":
        return _create_user(settings, args.email, args.password, UserRole(args.role))
    return 1  # pragma: no cover - argparse enforces the choices


if __name__ == "__main__":
    raise SystemExit(main())
