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

# Abort the deploy unless the unit really stopped (a stop on a never-installed unit reads "inactive").
require_stopped() {
    local state
    state="$(systemctl is-active "$1" 2>/dev/null || true)"
    case "$state" in
        inactive|failed|unknown) ;;
        *) echo "$1 is still $state after stop: aborting before touching code" >&2; exit 1 ;;
    esac
}

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

# ── Quiesce the namespace ──────────────────────────────────────────────────────
# Checkout and pip must not run under live code: stop every jobagent24-* timer and unit first (timers
# first, so nothing restarts), remembering what was running. stop waits for a running oneshot to end.
# Only jobagent24-* units are touched. If this script fails after this point the namespace stays
# stopped on purpose: half-deployed code must not run. Fix the cause and re-run.
exec 9>/var/lock/jobagent24-deploy.lock
flock -n 9 || { echo "another provision-jobagent24.sh run is in progress" >&2; exit 1; }
WAS_ACTIVE_TIMERS=()
WAS_API_ACTIVE=0
if have_systemd; then
    for name in "${TIMERS[@]}"; do
        if systemctl is-active --quiet "jobagent24-$name.timer"; then WAS_ACTIVE_TIMERS+=("$name"); fi
    done
    if systemctl is-active --quiet jobagent24-api.service; then WAS_API_ACTIVE=1; fi
    log "quiescing jobagent24-*"
    for name in "${TIMERS[@]}"; do
        systemctl stop "jobagent24-$name.timer" 2>/dev/null || true
        require_stopped "jobagent24-$name.timer"
    done
    for name in "${UNITS[@]}"; do
        systemctl stop "jobagent24-$name.service" 2>/dev/null || true
        require_stopped "jobagent24-$name.service"
    done
fi

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
    # The api restarts only if it was running: on a first install the env files are still empty templates
    # and it cannot start yet.
    if [ "$WAS_API_ACTIVE" -eq 1 ]; then
        systemctl restart jobagent24-api.service
        healthy=0
        for _ in $(seq 1 30); do
            if curl -fsS http://127.0.0.1:8024/api/health 2>/dev/null | grep -q '"status":"ok"'; then healthy=1; break; fi
            sleep 1
        done
        [ "$healthy" -eq 1 ] || { echo "jobagent24-api not healthy after restart: journalctl -u jobagent24-api -n 50" >&2; exit 1; }
        log "api healthy"
    fi
    # Timers come back only now, after the api answered its health check. A failed check exits above and
    # leaves them stopped, like the rest of the namespace.
    if [ "$ENABLE_TIMERS" -eq 1 ]; then
        for name in "${TIMERS[@]}"; do systemctl enable --now "jobagent24-$name.timer"; done
    else
        for name in "${WAS_ACTIVE_TIMERS[@]}"; do systemctl start "jobagent24-$name.timer"; done
        log "timers installed; only those running before this deploy were restarted (--enable-timers enables all)"
    fi
fi

log "done: $TAG at $(as_agent git -C "$APP" rev-parse --short HEAD). Fill $ETC/*.env, then: sudo systemctl start jobagent24-api"
