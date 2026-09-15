#!/usr/bin/env bash
# Role-aware health check. The worker image runs three different things, and
# "can you answer a ping" is only meaningful for two of them.
set -euo pipefail

ROLE="${WORKER_ROLE:-default}"

if [ "${ROLE}" = "beat" ]; then
  # beat schedules but never consumes, so inspect ping has nobody to answer for
  # it. Proving it can still reach the broker is the useful signal: a beat that
  # cannot publish is a beat that silently stops running retention sweeps.
  exec python - <<'PY'
import sys

import redis

from certex.config import get_settings

try:
    redis.from_url(get_settings().celery_broker_url, socket_connect_timeout=3).ping()
except Exception as exc:  # noqa: BLE001 - health check reports, never raises
    print(f"beat cannot reach the broker: {type(exc).__name__}", file=sys.stderr)
    sys.exit(1)
PY
fi

# Workers register as "<role>@<hostname>"; ping that exact node so a reply from
# some other worker on the same broker cannot mask this one being wedged.
exec celery -A worker.celery_app inspect ping --timeout 10 \
    --destination "${ROLE}@$(hostname)"
