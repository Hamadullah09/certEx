#!/usr/bin/env bash
# Entrypoint for the FastAPI service.
#
# Migrations are NOT run here. A dedicated one-shot `migrate` service owns them so
# that scaling the API to N replicas cannot start N concurrent schema upgrades.
set -euo pipefail

echo "[api] waiting for database..."
python - <<'PY'
import sys, time
import psycopg
from certex.config import get_settings

settings = get_settings()
deadline = time.monotonic() + 120
last = ""
while time.monotonic() < deadline:
    try:
        with psycopg.connect(settings.database_url_sync.replace("+psycopg", ""), connect_timeout=3) as conn:
            conn.execute("SELECT 1")
        print("[api] database reachable")
        sys.exit(0)
    except Exception as exc:
        last = type(exc).__name__
        time.sleep(1.5)
print(f"[api] database unreachable after 120s (last error: {last})", file=sys.stderr)
sys.exit(1)
PY

WORKERS="${GUNICORN_WORKERS:-4}"
TIMEOUT="${GUNICORN_TIMEOUT:-120}"
PORT="${API_PORT:-8000}"

echo "[api] starting gunicorn with ${WORKERS} uvicorn workers on :${PORT}"
exec gunicorn certex.main:app \
    --worker-class uvicorn.workers.UvicornWorker \
    --workers "${WORKERS}" \
    --bind "0.0.0.0:${PORT}" \
    --timeout "${TIMEOUT}" \
    --graceful-timeout 30 \
    --keep-alive 5 \
    --max-requests 2000 \
    --max-requests-jitter 200 \
    --access-logfile - \
    --error-logfile - \
    --forwarded-allow-ips '*'
