#!/usr/bin/env bash
# Provision the Beelink (Debian 13) for the M1 LinkedIn display slot. Idempotent: safe to re-run.
#
#   sudo bash provision-debian.sh <signed-tag> [allowed_signers-file]
#
# Supersedes RUNBOOK.md sections 1-5 on Debian (that runbook assumed Ubuntu 22.04 and a LAN-exposed
# noVNC). Secrets are never written by this script: base.env is created empty for the operator to
# fill by hand, and the VNC password is only ever prompted for. job-linkedin-ingest.timer is never
# enabled here -- RUNBOOK.md section 8's watched manual run comes first.

set -euo pipefail

TAG="${1:?usage: provision-debian.sh <signed-tag> [allowed_signers-file]}"
SIGNERS_SRC="${2:-$(dirname "$0")/allowed_signers}"
REPO_URL="https://github.com/kishoretheeraj/cold-emailing-agent.git"
APP=/opt/job-agent
STATE=/var/lib/job-agent
ETC=/etc/job-agent

log() { echo "==> $*"; }
have_systemd() { [ -d /run/systemd/system ]; }

[ "$(id -u)" -eq 0 ] || { echo "run with sudo" >&2; exit 1; }
[ -f "$SIGNERS_SRC" ] || { echo "missing allowed_signers file: $SIGNERS_SRC" >&2; exit 1; }

# ── Host baseline ──────────────────────────────────────────────────────────────
if have_systemd; then
    log "never sleep"
    systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target >/dev/null
fi
log "unattended-upgrades never reboots on its own"
echo 'Unattended-Upgrade::Automatic-Reboot "false";' > /etc/apt/apt.conf.d/52unattended-upgrades-local

# ── Packages ───────────────────────────────────────────────────────────────────
log "apt packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq \
    xvfb x11vnc novnc websockify xdotool scrot mutter tint2 \
    python3 python3-venv git curl smem ca-certificates systemd-timesyncd \
    libreoffice-writer-nogui >/dev/null
have_systemd && timedatectl set-ntp true

# Chrome (not Chromium): the profile has to look like the browser the operator really uses. Its
# postinst adds Google's apt repo, so unattended-upgrades keeps it patched from then on.
if ! command -v google-chrome >/dev/null; then
    log "google-chrome"
    curl -fsSL https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb -o /tmp/chrome.deb
    apt-get install -y -qq /tmp/chrome.deb >/dev/null
    rm -f /tmp/chrome.deb
fi

# ── Resume worker prerequisites ────────────────────────────────────────────────
# Calibri is the operator's own licensed font (from Microsoft Word on the Mac), copied here by
# hand. Without it LibreOffice embeds Carlito and resume_agent's font check refuses every build.
FONT_DIR=/usr/local/share/fonts/calibri
[ -d "$FONT_DIR" ] && fc-cache -f "$FONT_DIR" >/dev/null
# fc-match, not a file check: proves fontconfig actually resolves the family LibreOffice will ask for.
if [ "$(fc-match -f '%{family}' Calibri)" != "Calibri" ]; then
    echo "fontconfig does not resolve Calibri -- copy Calibri*.ttf from the Mac into $FONT_DIR (RUNBOOK, top)" >&2
    exit 1
fi

# ── Agent user: no sudo, no docker group (docs/beelink-server.md rule 2) ───────
if ! id jobagent >/dev/null 2>&1; then
    log "user jobagent"
    useradd --system --user-group --home-dir "$STATE" --shell /usr/sbin/nologin jobagent
fi
for group in sudo docker; do
    if id -nG jobagent | tr ' ' '\n' | grep -qx "$group"; then
        echo "jobagent must not be in group $group" >&2; exit 1
    fi
done

install -d -o jobagent -g jobagent -m 0750 "$APP" "$STATE" "$STATE/profiles" "$STATE/profiles/0"
install -d -o root -g jobagent -m 0750 "$ETC"
install -o root -g root -m 0644 "$SIGNERS_SRC" "$ETC/allowed_signers"

# ── Repo at a signed tag, never main ───────────────────────────────────────────
as_agent() { runuser -u jobagent -- "$@"; }
if [ ! -d "$APP/.git" ]; then
    log "clone"
    as_agent git clone -q "$REPO_URL" "$APP"
fi
as_agent git -C "$APP" fetch -q --tags --force origin
log "verify $TAG"
as_agent git -C "$APP" -c gpg.format=ssh -c gpg.ssh.allowedSignersFile="$ETC/allowed_signers" \
    tag -v "$TAG"
as_agent git -C "$APP" -c advice.detachedHead=false checkout -q "refs/tags/$TAG"

log "venv"
[ -x "$APP/.venv/bin/python" ] || as_agent python3 -m venv "$APP/.venv"
as_agent "$APP/.venv/bin/pip" install -q --upgrade pip
as_agent "$APP/.venv/bin/pip" install -q -r "$APP/requirements-beelink.txt"

log "claude CLI for jobagent"
if [ ! -x "$STATE/.local/bin/claude" ]; then
    runuser -u jobagent -- env HOME="$STATE" bash -c 'curl -fsSL https://claude.ai/install.sh | bash' >/dev/null
fi
runuser -u jobagent -- env HOME="$STATE" "$STATE/.local/bin/claude" --version
if [ ! -f "$ETC/claude.env" ]; then
    install -o root -g root -m 0600 "$APP/deploy/beelink/env/claude.env.example" "$ETC/claude.env"
    log "created $ETC/claude.env from the template -- paste your setup-token: sudo nano $ETC/claude.env"
fi

# ── Secrets: created empty or prompted for, never written by this script ───────
if [ ! -f "$ETC/base.env" ]; then
    install -o root -g root -m 0600 "$APP/deploy/beelink/env/base.env.example" "$ETC/base.env"
    log "created $ETC/base.env from the template -- fill it in: sudo nano $ETC/base.env"
fi
if [ ! -f "$ETC/approval.env" ]; then
    install -o root -g root -m 0600 "$APP/deploy/beelink/env/approval.env.example" "$ETC/approval.env"
    log "created $ETC/approval.env from the template -- paste the signing key: sudo nano $ETC/approval.env"
fi
if [ ! -f "$ETC/vncpasswd" ] && [ -t 0 ]; then
    log "set the VNC console password (guards the console only, not a LinkedIn credential)"
    x11vnc -storepasswd "$ETC/vncpasswd"
fi
if [ -f "$ETC/vncpasswd" ]; then
    chown jobagent:jobagent "$ETC/vncpasswd"
    chmod 0600 "$ETC/vncpasswd"
fi

# ── Units ──────────────────────────────────────────────────────────────────────
if have_systemd; then
    log "systemd units"
    install -m 0644 "$APP"/deploy/beelink/systemd/*.service "$APP"/deploy/beelink/systemd/*.timer \
        /etc/systemd/system/
    systemctl daemon-reload
    # resume-worker.timer and the apply-prepare/apply-submit timers are enabled by hand after a
    # watched first run (RUNBOOK).
    if [ -f "$ETC/vncpasswd" ]; then
        systemctl enable --now xvfb@0 chrome-profile@0 x11vnc@0 novnc@0
        systemctl --no-pager --lines=0 status xvfb@0 chrome-profile@0 x11vnc@0 novnc@0 || true
        # Display :1 is the apply worker's; its noVNC is how a human takes over a CAPTCHA.
        systemctl enable --now xvfb@1 x11vnc@1 novnc@1
        # noVNC stays on 127.0.0.1; tailscale serve publishes it over HTTPS to the tailnet only.
        if command -v tailscale >/dev/null; then
            tailscale serve --bg --https=8443 http://127.0.0.1:6081
        fi
    else
        log "no $ETC/vncpasswd yet -- re-run from an interactive terminal to set it and start slot 0"
    fi
fi

log "done: $TAG at $(as_agent git -C "$APP" rev-parse --short HEAD). job-linkedin-ingest.timer stays disabled until RUNBOOK.md section 8."
