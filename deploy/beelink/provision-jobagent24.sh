#!/usr/bin/env bash
# Provision the namespaced 24/7 stack beside the old one. Idempotent. Never touches the old
# install tree, its env dir, its state dir, or any old unit.
#
#   sudo JOBAGENT24_REPO_URL=<git url> bash provision-jobagent24.sh <signed-tag> [--enable-timers]
#
# Requires provision-debian.sh to have run first (jobagent user, Chrome, Xvfb/x11vnc/noVNC, LibreOffice,
# Calibri, the signer file). Secrets are never written: env files are created from valueless templates.
# Timers stay disabled unless --enable-timers is passed, because the old and new stacks both source,
# score and prepare; run them side by side only deliberately (RUNBOOK-24-7.md).

set -euo pipefail

TAG="${1:?usage: provision-jobagent24.sh <signed-tag> [--enable-timers]}"
ENABLE_TIMERS=0
[ "${2:-}" = "--enable-timers" ] && ENABLE_TIMERS=1
REPO_URL="${JOBAGENT24_REPO_URL:?set JOBAGENT24_REPO_URL to the 24/7 repo git URL}"
APP=/opt/jobagent24
STATE=/var/lib/jobagent24
ETC=/etc/jobagent24
OLD_ETC=/etc/job-agent
UNITS=(discovery screening resume apply monitor api)
TIMERS=(discovery screening resume apply)

log() { echo "==> $*"; }
have_systemd() { [ -d /run/systemd/system ]; }

[ "$(id -u)" -eq 0 ] || { echo "run with sudo" >&2; exit 1; }
id jobagent >/dev/null 2>&1 || { echo "no jobagent user: run provision-debian.sh first" >&2; exit 1; }
command -v google-chrome >/dev/null || { echo "no google-chrome: run provision-debian.sh first" >&2; exit 1; }
[ -f "$OLD_ETC/allowed_signers" ] || { echo "missing $OLD_ETC/allowed_signers: run provision-debian.sh first" >&2; exit 1; }
for group in sudo docker; do
    if id -nG jobagent | tr ' ' '\n' | grep -qx "$group"; then
        echo "jobagent must not be in group $group" >&2; exit 1
    fi
done

# ── Directories ────────────────────────────────────────────────────────────────
# profiles/2 is reserved for a persistent Chrome profile on display :2 (chrome-profile@.service
# convention, RUNBOOK-24-7.md section 5); sessions/ holds Playwright storage_state per tenant.
install -d -o jobagent -g jobagent -m 0750 "$APP" "$STATE" "$STATE/profiles" "$STATE/profiles/2"
install -d -o jobagent -g jobagent -m 0700 "$STATE/sessions"
install -d -o root -g jobagent -m 0750 "$ETC"
install -o root -g root -m 0644 "$OLD_ETC/allowed_signers" "$ETC/allowed_signers"

# ── Repo at a signed tag, never main ───────────────────────────────────────────
as_agent() { runuser -u jobagent -- "$@"; }
if [ ! -d "$APP/.git" ]; then
    log "clone"
    as_agent git clone -q "$REPO_URL" "$APP"
fi
as_agent git -C "$APP" fetch -q --tags --force origin
log "verify $TAG"
as_agent git -C "$APP" -c gpg.format=ssh -c gpg.ssh.allowedSignersFile="$ETC/allowed_signers" tag -v "$TAG"
as_agent git -C "$APP" -c advice.detachedHead=false checkout -q "refs/tags/$TAG"

# ── Own venv ───────────────────────────────────────────────────────────────────
# requirements-beelink.txt is requirements.txt (fastapi, httpx, pydantic, reportlab, ...) plus Playwright
# and CPU torch for job_pick; uvicorn is the one 24/7 extra it does not carry.
log "venv"
[ -x "$APP/.venv/bin/python" ] || as_agent python3 -m venv "$APP/.venv"
as_agent "$APP/.venv/bin/pip" install -q --upgrade pip
as_agent "$APP/.venv/bin/pip" install -q -r "$APP/requirements-beelink.txt" "uvicorn>=0.30.0"

# ── Env files: created empty from templates, never overwritten ─────────────────
for pair in "base.env:base.env.example" "claude.env:claude.env.example" "dashboard.env:dashboard.env.example"; do
    target="$ETC/${pair%%:*}"
    if [ ! -f "$target" ]; then
        install -o root -g root -m 0600 "$APP/deploy/beelink/env/${pair##*:}" "$target"
        log "created $target from its template -- fill it in: sudo nano $target"
    fi
done

# ── Units ──────────────────────────────────────────────────────────────────────
if have_systemd; then
    log "systemd units"
    for name in "${UNITS[@]}"; do
        install -m 0644 "$APP/deploy/beelink/systemd/jobagent24-$name.service" /etc/systemd/system/
    done
    for name in "${TIMERS[@]}"; do
        install -m 0644 "$APP/deploy/beelink/systemd/jobagent24-$name.timer" /etc/systemd/system/
    done
    systemctl daemon-reload
    # Display :2 reuses the old xvfb@/x11vnc@/novnc@ templates (instance 2 collides with nothing:
    # :0 and :1 are the old stack's). x11vnc@ reads the old vncpasswd.
    if [ -f "$OLD_ETC/vncpasswd" ]; then
        systemctl enable --now xvfb@2 x11vnc@2 novnc@2
    else
        log "no $OLD_ETC/vncpasswd -- display :2 not started"
    fi
    systemctl enable jobagent24-api.service
    if [ "$ENABLE_TIMERS" -eq 1 ]; then
        for name in "${TIMERS[@]}"; do systemctl enable --now "jobagent24-$name.timer"; done
    else
        log "timers installed but NOT enabled (pass --enable-timers after the watched runs in RUNBOOK-24-7.md)"
    fi
fi

log "done: $TAG at $(as_agent git -C "$APP" rev-parse --short HEAD). Fill $ETC/*.env, then: sudo systemctl start jobagent24-api"
