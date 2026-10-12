#!/usr/bin/env bash
# Idempotent Playwright deployment for the jobagent24 pipeline (Ubuntu, Python 3.10+).
set -euo pipefail

APP_DIR="/opt/jobagent24"
VENV_DIR="${APP_DIR}/.venv"
BROWSERS_DIR="${APP_DIR}/.playwright-browsers"
LOG_DIR="/var/log/jobagent24"
RUN_USER="${SUDO_USER:-$(id -un)}"

PACKAGES=(playwright python-dotenv supabase)

log() { printf '[playwright_setup] %s\n' "$*"; }

if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)'; then
  log "ERROR: Python 3.10+ required"; exit 1
fi
if ! python3 -c 'import venv, ensurepip' 2>/dev/null; then
  log "Installing python3-venv"
  sudo apt-get update -y
  sudo apt-get install -y python3-venv python3-pip
fi

sudo mkdir -p "${APP_DIR}" "${BROWSERS_DIR}" "${LOG_DIR}"
sudo chown -R "${RUN_USER}:${RUN_USER}" "${BROWSERS_DIR}" "${LOG_DIR}"

if [[ ! -x "${VENV_DIR}/bin/python" ]]; then
  log "Creating venv at ${VENV_DIR}"
  sudo mkdir -p "${VENV_DIR}"
  sudo chown "${RUN_USER}:${RUN_USER}" "${VENV_DIR}"
  python3 -m venv "${VENV_DIR}"
else
  log "Venv already exists, reusing"
fi

PY="${VENV_DIR}/bin/python"
"${PY}" -m pip install --quiet --upgrade pip

log "Installing: ${PACKAGES[*]}"
"${PY}" -m pip install --quiet --upgrade "${PACKAGES[@]}"

export PLAYWRIGHT_BROWSERS_PATH="${BROWSERS_DIR}"
log "Installing OS dependencies for chromium (needs sudo)"
sudo env "PLAYWRIGHT_BROWSERS_PATH=${BROWSERS_DIR}" "${PY}" -m playwright install-deps chromium
log "Installing chromium browser"
"${PY}" -m playwright install chromium

log "Verifying install"
"${PY}" - <<'PYEOF'
import os, sys
import playwright
from playwright.sync_api import sync_playwright
for mod in ("dotenv", "supabase"):
    __import__(mod)
with sync_playwright() as p:
    exe = p.chromium.executable_path
    if not os.path.exists(exe):
        sys.exit(f"FAIL: chromium executable missing at {exe}")
    browser = p.chromium.launch(headless=True)
    version = browser.version
    browser.close()
print(f"OK: playwright, chromium {version}")
PYEOF

log "Done. Run the pipeline with ${APP_DIR}/run_pipeline.sh"
