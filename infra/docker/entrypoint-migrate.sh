#!/usr/bin/env bash
# One-shot: apply migrations, then seed demo data when SEED_ENABLED is true.
# Compose gates the api and worker services on this completing successfully.
set -euo pipefail

echo "[migrate] waiting for database..."
python - <<'PY'
import sys, time
import psycopg
from certex.config import get_settings

settings = get_settings()
deadline = time.monotonic() + 180
last = ""
while time.monotonic() < deadline:
    try:
        with psycopg.connect(settings.database_url_sync.replace("+psycopg", ""), connect_timeout=3) as conn:
            conn.execute("SELECT 1")
        print("[migrate] database reachable")
        sys.exit(0)
    except Exception as exc:
        last = type(exc).__name__
        time.sleep(1.5)
print(f"[migrate] database unreachable after 180s (last error: {last})", file=sys.stderr)
sys.exit(1)
PY

echo "[migrate] applying migrations"
cd /app/infra
alembic upgrade head

echo "[migrate] verifying schema matches the models"
alembic check

cd /app
python -m certex.cli seed
python -m certex.cli ensure-bucket

echo "[migrate] done"
