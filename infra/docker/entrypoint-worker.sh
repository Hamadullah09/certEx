#!/usr/bin/env bash
# Entrypoint for Celery. WORKER_ROLE selects what this container runs:
#
#   default  general queues (ingest, normalize, text, classify, extract, validate,
#            export, import, maintenance)
#   ocr      only the CPU-bound OCR queue, with its own concurrency ceiling so it
#            cannot starve the fast queues
#   beat     the scheduler (retention sweeps, stalled-batch reconciliation)
set -euo pipefail

ROLE="${WORKER_ROLE:-default}"
CONCURRENCY="${CELERY_WORKER_CONCURRENCY:-4}"
OCR_CONCURRENCY="${CELERY_OCR_CONCURRENCY:-2}"
MAX_TASKS="${CELERY_MAX_TASKS_PER_CHILD:-50}"
LOGLEVEL="${CELERY_LOG_LEVEL:-info}"

echo "[worker] waiting for broker and database..."
python - <<'PY'
import sys, time
import psycopg
import redis
from certex.config import get_settings

settings = get_settings()
deadline = time.monotonic() + 120
db_ok = broker_ok = False
last = ""
while time.monotonic() < deadline and not (db_ok and broker_ok):
    if not db_ok:
        try:
            with psycopg.connect(settings.database_url_sync.replace("+psycopg", ""), connect_timeout=3) as conn:
                conn.execute("SELECT 1")
            db_ok = True
        except Exception as exc:
            last = f"db:{type(exc).__name__}"
    if not broker_ok:
        try:
            redis.from_url(settings.celery_broker_url, socket_connect_timeout=3).ping()
            broker_ok = True
        except Exception as exc:
            last = f"broker:{type(exc).__name__}"
    if not (db_ok and broker_ok):
        time.sleep(1.5)

if db_ok and broker_ok:
    print("[worker] dependencies reachable")
    sys.exit(0)
print(f"[worker] dependencies unreachable after 120s (last error: {last})", file=sys.stderr)
sys.exit(1)
PY

case "${ROLE}" in
  beat)
    echo "[worker] starting celery beat"
    exec celery -A worker.celery_app beat --loglevel "${LOGLEVEL}"
    ;;
  ocr)
    echo "[worker] starting OCR worker (queue=ocr, concurrency=${OCR_CONCURRENCY})"
    exec celery -A worker.celery_app worker \
        --queues ocr \
        --concurrency "${OCR_CONCURRENCY}" \
        --max-tasks-per-child "${MAX_TASKS}" \
        --prefetch-multiplier 1 \
        --loglevel "${LOGLEVEL}" \
        --hostname "ocr@%h"
    ;;
  *)
    echo "[worker] starting default worker (concurrency=${CONCURRENCY})"
    exec celery -A worker.celery_app worker \
        --queues default,ingest,text,classify,extract,validate,export,import,maintenance \
        --concurrency "${CONCURRENCY}" \
        --max-tasks-per-child "${MAX_TASKS}" \
        --prefetch-multiplier 1 \
        --loglevel "${LOGLEVEL}" \
        --hostname "default@%h"
    ;;
esac
