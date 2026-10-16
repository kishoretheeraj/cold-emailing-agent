#!/usr/bin/env bash
# Wrapper: activates the persistent venv, sets env, runs apply_pipeline.py
set -euo pipefail

APP_DIR="/opt/jobagent24"
VENV_DIR="${APP_DIR}/.venv"
LOG_DIR="/var/log/jobagent24"
LOG_FILE="${LOG_DIR}/pipeline.log"

if [[ -f "${APP_DIR}/.env" ]]; then
  set -a
  source "${APP_DIR}/.env"
  set +a
fi

export APPLY_LOG_PATH="${APPLY_LOG_PATH:-${LOG_DIR}/apply.log}"
export APPLY_APPROVED_HOSTS="${APPLY_APPROVED_HOSTS:-}"
export PLAYWRIGHT_BROWSERS_PATH="${PLAYWRIGHT_BROWSERS_PATH:-${APP_DIR}/.playwright-browsers}"
export PYTHONUNBUFFERED=1

if [[ ! -f "${VENV_DIR}/bin/activate" ]]; then
  echo "Venv missing at ${VENV_DIR}. Run ${APP_DIR}/deploy/playwright_setup.sh first." >&2
  exit 1
fi

mkdir -p "${LOG_DIR}" 2>/dev/null || true
source "${VENV_DIR}/bin/activate"
cd "${APP_DIR}"

{
  echo "=== $(date -Is) start: apply_pipeline.py $* ==="
  rc=0
  python "${APP_DIR}/apply_pipeline.py" "$@" || rc=$?
  echo "=== $(date -Is) exit code ${rc} ==="
  exit "${rc}"
} 2>&1 | tee -a "${LOG_FILE}"
exit "${PIPESTATUS[0]}"
