#!/usr/bin/env bash
# Every quality gate the project enforces, in one command.
#
#   ./scripts/check.sh          backend + frontend
#   ./scripts/check.sh backend
#   ./scripts/check.sh frontend
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET="${1:-all}"
FAILED=0

if [ -x "${ROOT}/api/.venv/Scripts/python.exe" ]; then
  PY="${ROOT}/api/.venv/Scripts/python.exe"      # Windows
elif [ -x "${ROOT}/api/.venv/bin/python" ]; then
  PY="${ROOT}/api/.venv/bin/python"              # Linux / macOS
else
  PY="python"
fi

step() {
  echo ""
  echo "--- $1 ---"
  shift
  if "$@"; then
    echo "    ok"
  else
    echo "    FAILED"
    FAILED=1
  fi
}

if [ "${TARGET}" = "all" ] || [ "${TARGET}" = "backend" ]; then
  cd "${ROOT}/api"
  step "ruff lint"      "${PY}" -m ruff check certex tests
  step "ruff format"    "${PY}" -m ruff format --check certex tests
  step "mypy (strict)"  "${PY}" -m mypy certex
  # The load tests build hundreds of thousands of rows and are run deliberately:
  #   pytest tests/load -m load
  # See tests/load/__init__.py for the larger sizes.
  step "pytest"         "${PY}" -m pytest -q -m "not load"
fi

if [ "${TARGET}" = "all" ] || [ "${TARGET}" = "frontend" ]; then
  cd "${ROOT}/web"
  if [ -d node_modules ]; then
    step "tsc"          npx --no-install tsc --noEmit
    step "eslint"       npx --no-install next lint --max-warnings 0
  else
    echo ""
    echo "--- frontend ---"
    echo "    skipped: run 'npm install' in web/ first"
  fi
fi

echo ""
if [ "${FAILED}" -eq 0 ]; then
  echo "All checks passed."
else
  echo "One or more checks FAILED."
fi
exit "${FAILED}"
