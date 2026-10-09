# Beelink agent server

Source of truth for the Beelink box: what's on it, the rules for changing it, and what to build next. Written 2026-10-04, at the end of the base install.

**How to use (Kishore):** save this file as `docs/beelink-server.md` in your agents repo and add this line to the repo's `CLAUDE.md`:

```
@docs/beelink-server.md
```

**Agent instructions:** keep this file current. When a step is completed or any config on the box changes, update section 3 and tick the roadmap item in the same session.

---

## 1. Purpose

An always-on Linux box that runs Kishore's agents 24/7: browser automation, Anthropic computer use, and Claude Agent SDK agents. Agents are developed on the Mac and deployed to the Beelink via git. Websites and apps are hosted elsewhere (Vercel or Cloudflare Pages), never on this box.

## 2. Working agreement

Kishore is new to Linux server administration and learning as he goes.

- Explain why before what, briefly. Give one command at a time and wait for its output before giving the next.
- Always say which machine a command runs on. Mac prompt: `kishoretheeraj@MacBook-Air-76 ~ %`. Beelink prompt: `kishore@beelink:~$`. These have been mixed up before.
- Single-line commands only. Multi-line pastes and heredocs get mangled by zsh bracketed paste. To write a file, use `echo '...' | sudo tee /path` or `sudo nano /path`.
- Before touching SSH, networking, or firewall rules, confirm a recovery path exists (monitor and keyboard attached, or a second SSH session left open), and test in a new session before closing the old one.
- Prefer drop-in files in `*.d/` directories over editing main config files.
- Call out destructive commands explicitly before running them.
- Verify Anthropic product details (Claude Code install and auth, Agent SDK, billing, computer-use tool versions) against current official docs, not memory. They change often.

## 3. Current state (2026-10-06)

### Hardware

| Item | Value |
|---|---|
| Model | Beelink SER5 Pro |
| CPU | AMD Ryzen 7 5800H, 8 cores / 16 threads, Zen 3 |
| RAM | 16 GB DDR4 SO-DIMM, 2 slots, max 64 GB. Beelink ships this config as 2×8 GB; slot layout not yet confirmed (`dmidecode` needs sudo). **Linux sees only 12.6 GiB** (`MemTotal` 13204748 kB): the iGPU reserves the rest. Budget against 12.6, not 16 |
| Disk | 512 GB NVMe, KINGSTON OM3SGP4512K2-A00 (`/dev/nvme0n1`) |
| Wi-Fi | Intel Wi-Fi 6 AX200, interface `wlo1` (in use) |
| Ethernet | Realtek RTL8111/8168, interface `enp1s0` (unused, no cable) |
| BIOS | AMI Aptio, version 5800H502 (2023-02-15). Delete = setup, F7 = boot menu |
| BIOS settings | Fast Boot disabled. Boot mode UEFI. AC power loss set to Always On (Advanced → AMD CBS → FCH Common Options → AC Power Loss Options) |

### OS

| Item | Value |
|---|---|
| Distro | Debian 13 "trixie", installed from `debian-13.7.0-amd64-netinst.iso` |
| Kernel | 6.12.111+deb13-amd64 |
| Desktop | None. Headless; only "standard system utilities" selected at install |
| Hostname | `beelink` |
| Admin user | `kishore`, in the `sudo` and `docker` groups. Root has no password |
| Disk layout | Guided, entire disk, single partition (EFI + ext4 root + swap). SteamOS was wiped |
| Swap | 13 GB partition, `/dev/nvme0n1p3` (`swapon` isn't on a non-root PATH; read `/proc/swaps`) |
| Networking | ifupdown + wpa_supplicant, configured in `/etc/network/interfaces`. No NetworkManager, so `nmcli` and `nmtui` don't exist |

### Access

| Item | Value |
|---|---|
| Tailscale | v1.102.4, joined with plain `sudo tailscale up` (not `--ssh`). MagicDNS name `beelink`, IP 100.77.193.40. The Mac is 100.103.234.82 |
| SSH | OpenSSH, key-only. From the Mac: `ssh kishore@beelink`. Mac key: `~/.ssh/id_ed25519` (comment `kishore-macbook`). `~/.ssh/key_hiya` on the Mac is an unrelated project key; don't use it here |
| Console | Password login at the physical screen still works; this is the recovery path. The Evofox Ronin keyboard must be in wired mode (USB-C cable, FN+5), since Bluetooth doesn't work in the BIOS or early boot |

### Installed

- From Debian: `openssh-server curl git tmux htop unattended-upgrades`
- Tailscale, from Tailscale's apt repo
- Docker CE via `get.docker.com`, which set up Docker's apt repo, including the compose and buildx plugins. `docker run hello-world` verified
- Claude Code 2.1.289 for `kishore` (native installer, `~/.local/bin/claude`, on PATH for login shells). **Not logged in yet**
- Python 3.13.5 (Debian's own; CI uses 3.11)
- The M1 stack via `deploy/beelink/provision-debian.sh` at signed tag `beelink-v2` (2026-10-06): `jobagent` user, Chrome, Xvfb/x11vnc/noVNC (slot 0 active), `/opt/job-agent`, LibreOffice 25.2, Claude Code for `jobagent`, and Calibri in `/usr/local/share/fonts/calibri/` (the operator's own fonts from Word on the Mac)
- `/etc/job-agent/base.env` (5 secrets) and `/etc/job-agent/claude.env` (subscription token) filled 2026-10-06 from the Mac via `~/handoff-beelink-env.sh` (root:root 0600)
- **Running:** `resume-worker.timer` (every 30 min, enabled 2026-10-06; first watched run: preflight + canary passed, `rows=0`). `job-linkedin-ingest.timer` still disabled (LinkedIn login + watched run pending)

### Config files changed from defaults

| File | Contents and purpose |
|---|---|
| `/etc/modprobe.d/iwlmvm.conf` | `options iwlmvm power_scheme=1`. Keeps the AX200 out of power save, which causes laggy or dropped connections on always-on boxes. Verified: `/sys/module/iwlmvm/parameters/power_scheme` reads `1` |
| `/etc/ssh/sshd_config.d/10-no-passwords.conf` | `PasswordAuthentication no` |
| `/etc/apt/apt.conf.d/20auto-upgrades` | Daily package-list update and unattended security upgrades |
| `/etc/docker/daemon.json` | json-file logs capped at 10 MB × 3 files per container |
| `/etc/network/interfaces` | Written by the installer. Holds the Wi-Fi SSID and key for `wlo1`; treat as secret |

## 4. Rules

1. **Never set `ANTHROPIC_API_KEY` globally** (shell profiles, `/etc/environment`, settings `env` blocks). It overrides subscription login and switches Claude Code and the Agent SDK to pay-as-you-go API billing. If one container genuinely needs a Console key, pass it to that container only, through a `chmod 600` env file that is never committed.
   The resume worker, the apply-prepare worker and the job-pick scorer are the deliberate subscription-token holders: `CLAUDE_CODE_OAUTH_TOKEN` in `/etc/job-agent/claude.env`, loaded only by `resume-worker.service`, `apply-prepare.service` and `job-pick.service`. `apply-submit.service` loads `/etc/job-agent/approval.env` (`APPROVAL_SIGNING_KEY`) instead and is the only armed unit.
2. **Agents never run as `kishore`.** Membership in `sudo` or `docker` is root-equivalent, so a prompt-injected agent with either one owns the box. Agents get their own unprivileged users with no sudo and no docker group.
3. **arm64 vs amd64.** The Mac (M4) builds arm64 images by default; the Beelink needs amd64. Use `docker buildx build --platform linux/amd64 ...` on the Mac, or build on the Beelink after `git pull`.
4. **Hard memory limits on everything long-running** (`docker run --memory`, systemd `MemoryMax=`). Budget: 16 GB total, about 1–2 GB for the OS and Docker, and 1–2 GB per Xvfb + browser session, so plan for 3–4 concurrent browser sessions. RAM is the binding constraint, not CPU.
5. **Web content is untrusted.** Prompt injection is the main operational risk. Don't give a browsing agent secrets it doesn't need, and don't combine private data, untrusted content, and the ability to send data out in one agent without approval gates.
6. **Nothing exposed beyond the tailnet.** Docker's `-p 8080:8080` binds every interface and bypasses host firewall rules. Publish ports as `-p 127.0.0.1:8080:8080` and reach them from the Mac with `ssh -L 8080:localhost:8080 kishore@beelink`.
7. **Everything reproducible lives in git:** agent definitions, Dockerfiles, systemd units, settings. Deploying means pushing from the Mac and running `git pull` on the Beelink. Anything changed by hand on the box gets recorded in this file.
8. **Secrets** (OAuth tokens, API keys, the Wi-Fi key) are never committed. Store them in root- or agent-owned files with mode 600 and load them with systemd `EnvironmentFile=`.

### Billing status (re-verify before relying on it)

As of 2026-10-04, per Anthropic's help center article "Use the Claude Agent SDK with your Claude plan": a planned change that would have moved Agent SDK and `claude -p` usage off subscription limits was paused on June 15, 2026. That usage still draws from the subscription's limits, and Anthropic says it will give notice before any change takes effect. Calling the Messages API directly with a Console API key, as Anthropic's computer-use reference demo does, is pay-as-you-go.

Source: https://support.claude.com/en/articles/15036540-use-the-claude-agent-sdk-with-your-claude-plan

## 5. Health check

On the Beelink:

```
tailscale status
systemctl is-active ssh docker tailscaled unattended-upgrades
cat /sys/module/iwlmvm/parameters/power_scheme
free -h
df -h /
docker ps
journalctl -p err -b --no-pager | tail -n 30
```

From the Mac, confirm passwords are refused: `ssh -o PubkeyAuthentication=no kishore@beelink` should print `Permission denied (publickey)`.

## 6. Recovery

- **SSH lockout:** attach the monitor and keyboard (Ronin wired, FN+5), log in at the console with the password, then check `sudo sshd -t` and the files in `/etc/ssh/sshd_config.d/`.
- **Not reachable over Tailscale:** at the console, run `tailscale status`, and re-authenticate with `sudo tailscale up` if needed. Check Wi-Fi with `ip addr show wlo1` and `journalctl -u networking -b`. Restart Wi-Fi only from the console, never over SSH: `sudo ifdown wlo1 && sudo ifup wlo1`.
- **Reinstall:** the SanDisk Cruzer Blade 32 GB stick still holds the checksum-verified Debian 13.7.0 netinst. Plug it in before power-on, press F7, and choose `UEFI: SanDisk`. See section 9 for installer gotchas.

## 7. Roadmap

### Phase 0: small items, do soon

- [ ] Disable Tailscale key expiry for `beelink` (admin console → Machines → beelink → ⋯ → Disable key expiry). Otherwise the node key expires, 180 days by default, and the box silently drops off the tailnet.
- [ ] Record the RAM slot layout with `sudo dmidecode -t memory | grep -E "Size|Locator"` and update section 3.
- [x] Record swap (13 GB partition, section 3).
- [ ] Confirm AC power-loss auto-on with a cord test, if not already done: `sudo poweroff`, pull the cord for 10 seconds, plug it back in without pressing the button, then SSH in.
- [x] Delete the leftover installer: `rm ~/get-docker.sh`.

### Phase 1: Claude Code for `kishore` (interactive admin use)

- Install with `curl -fsSL https://claude.ai/install.sh | bash`, then `source ~/.profile`, then `claude --version`. Start `claude` and log in with the Claude subscription account, not the Console/API option.
- Done when: `claude` runs over SSH and `/status` shows subscription authentication.
- [x] Installed 2026-10-04 (2.1.289).
- [ ] Log in: run `claude` on the Beelink, `/login`, choose the subscription account.

### Phase 2: agent user and isolation model

- Decide how an unprivileged agent gets a browser without access to the Docker socket. Options: (a) rootless Docker for the agent user; (b) each agent runs inside its own container started by systemd, so the agent never touches the Docker socket; (c) long-lived browser containers started by the admin, with agents connecting over CDP on localhost. Record the choice here.
- **Chosen for the job-search agent (2026-10-04): (d) no containers.** Native systemd units from `deploy/beelink/systemd/`, all `User=jobagent` (system user, `nologin`, no sudo, no docker group), hardened with `ProtectSystem=strict`/`ProtectHome=yes`/`NoNewPrivileges=yes` and a `MemoryMax=` per unit. Reason: the units already existed, were reviewed, and run real headful Chrome, which is what the LinkedIn profile needs to look like. Containers remain the plan for the separate general-purpose agents below.
- Create the user: `sudo adduser --disabled-password agent`, with no sudo and no docker group.
- Unattended auth: `claude setup-token` generates a one-year OAuth token for environments without browser login. Store it as `CLAUDE_CODE_OAUTH_TOKEN` in a mode-600 `EnvironmentFile`. These tokens don't authenticate Remote Control.
- Done when: the agent user can run `claude -p "say hello"` under its own identity, with no sudo or Docker access.

### Phase 2b: job-search agent (M1) on this box

- [x] Sign and push a deploy tag from the Mac, then run `deploy/beelink/provision-debian.sh` (see `deploy/beelink/RUNBOOK.md`, top). Dry-run verified in a clean `debian:trixie` container on this box, 2026-10-04.
- [x] Fill `/etc/job-agent/base.env` (2026-10-06). [ ] Open the tunnel (`ssh -N -L 6080:localhost:6080 kishore@beelink`), log in to LinkedIn at `http://localhost:6080/vnc.html`.
- [ ] First watched manual run (RUNBOOK section 8), then enable `job-linkedin-ingest.timer`.
- [x] Copy Calibri to `/usr/local/share/fonts/calibri/` (2026-10-06)
- [x] Put the subscription token in `/etc/job-agent/claude.env` (2026-10-06)
- [x] Watched `systemctl start resume-worker`, then `systemctl enable --now resume-worker.timer` (2026-10-06)

### Phase 2c: apply worker (first ten applications)

- [ ] Re-provision at a new signed tag (installs `requirements-beelink.txt`, the apply units, display :1, `tailscale serve` for the takeover view).
- [ ] Fill `/etc/job-agent/approval.env` (same `APPROVAL_SIGNING_KEY` as Vercel).
- [ ] Watched `systemctl start apply-prepare` (RUNBOOK section 10), then enable `apply-prepare.timer`.
- [ ] Set `APPLY_SUBMIT_HOST=beelink` in Vercel, then enable `apply-submit.timer`.

### Phase 2d: fifty a day (sourcing and scoring)

- [ ] Push migration `20261009000000` (`db_migrate.yml` dryrun, then push). The queues read its columns.
- [ ] Re-provision at a new signed tag (installs CPU-only torch and the `job-sourcing`/`job-pick` units).
- [ ] Watched `systemctl start job-sourcing` then `job-pick` (RUNBOOK section 11), then enable both timers.
- [ ] Optional: set `job_search_preferences` on the Prompts page (titles, locations, caps).

### Phase 2e: every site (universal filler)

- [ ] Run `scripts/form_recon.py --from-feed 3` (RUNBOOK section 12) and send the report back.
- [ ] Watched `apply-prepare` run on a universal row, then one watched submit.
- [ ] Grow `APPLY_UNIVERSAL_PLATFORMS` per platform as watched runs prove it.

### Phase 3: first browser-automation workload

- Headful Chromium under Xvfb in a container, with `--memory` set. Headful holds up better against bot detection than headless. Playwright's official Docker images are a reasonable base.
- Done when: a systemd-managed container loads a page and saves a screenshot, restarts on failure, and logs to `journalctl` or `docker logs`.

### Phase 4: first computer-use environment

- Start from Anthropic's reference implementation, `computer-use-demo` in https://github.com/anthropics/anthropic-quickstarts (Docker, an Xvfb virtual display, a window manager, and a VNC/noVNC viewer). Take the current image name, ports, and computer-use tool version from its README and Anthropic's computer use docs, not from memory.
- It uses a Console API key, so it's pay-as-you-go. Set a budget first, and pass the key to this container only (rule 1).
- Publish ports on 127.0.0.1 and view from the Mac through an SSH tunnel (rule 6).
- Develop on the Mac against the same Linux container (arm64 build), not the real macOS desktop, so the agent learns the screen it will see in production.
- Done when: Kishore can watch the agent operate the virtual desktop from his Mac's browser.

### Phase 5: deploy pipeline

- The agents repo holds `CLAUDE.md`, `.claude/agents/`, `.claude/commands/`, settings, Dockerfiles, and systemd units and timers.
- Flow: push from the Mac → `git pull` on the Beelink → `sudo systemctl daemon-reload` → enable or restart the unit → `journalctl -u <unit> -f`.
- Done when: one agent runs on a schedule or continuously, survives a reboot, and picks up updates via `git pull`.

### Phase 6: operations

- Usage tracking (for example `ccusage`), disk and RAM monitoring, a `docker system prune` policy, and backups of agent state.
- Optional hardening: bind `sshd` to the tailnet only, and add a host firewall. Docker's docs warn that firewall rules created with `nft` aren't supported on a host running Docker, so plan this carefully.

## 8. Open decisions

- Agent isolation model for the general-purpose agents (Phase 2). The job-search agent uses native systemd as `jobagent` (see Phase 2).
- Python vs TypeScript Agent SDK. The Python SDK bundles the Claude Code CLI; check current docs for the TypeScript SDK's requirements.
- Computer use through a Console API key (pay-as-you-go) vs an Agent SDK agent driving a virtual desktop through an MCP server (subscription limits, while the current policy holds).
- RAM upgrade to 32 GB, only once 16 GB is the real ceiling.
- Wired Ethernet instead of Wi-Fi if a cable run becomes possible. `enp1s0` is ready, and wired is more reliable for a 24/7 box.
- **Reconcile with the existing M1 artifacts in `deploy/beelink/`** (systemd units for `xvfb@`, `x11vnc@`, `novnc@`, `chrome-profile@`, `job-linkedin-ingest` + timer, all `User=jobagent`; written before this box existed, host provisioning never run). They're the concrete Phase 2–5 workload for this box, but `deploy/beelink/RUNBOOK.md` disagrees with this file in three places:
  - It assumes **Ubuntu Server 22.04** (Python 3.11 from apt, deadsnakes PPA fallback). The box is Debian 13, which ships Python 3.13 and has no PPAs. The repo pins Python 3.11 in CI.
  - It opens **noVNC on the LAN** (`http://<beelink-lan-ip>:6080`, a `ufw` rule). Rule 6 says tailnet-only, and section 7 warns about host firewalls alongside Docker.
  - It runs Chrome **natively under systemd**, not in a container. That's a valid answer to the Phase 2 isolation question (a dedicated `jobagent` user, no sudo, no docker group, which satisfies rule 2), but it hasn't been recorded as the choice.
  - `deploy/beelink/env/base.env.example` carries `ANTHROPIC_API_KEY` for `cu_linkedin.py` (Messages API, pay-as-you-go). That's allowed under rule 1 only as a per-service mode-600 `EnvironmentFile`, never globally.

## 9. Installer gotchas (for a reinstall)

- **Writing the USB on macOS:** find the stick with `diskutil list`, then run one command at a time: `cd ~/Downloads`, `diskutil unmountDisk /dev/diskN`, `sudo dd if=debian-13.7.0-amd64-netinst.iso of=/dev/rdiskN bs=4m status=progress`. A multi-line paste once passed a literal `~` to dd. Afterwards macOS says the disk is unreadable; that's normal. Click Eject, never Initialize.
- **Keyboard:** Ronin in wired mode (FN+5). Its Windows/Mac mode is a physical switch on the keyboard; set it to Windows.
- **Boot:** the stick only appears in the F7 menu if it's plugged in before power-on. It shows up twice (`UEFI: SanDisk` and `UEFI: SanDisk, Partition 2`); either works.
- **Partitioning:** the installer pre-selects "use the largest continuous free space". Choose "Guided – use entire disk" instead, then the KINGSTON drive, not the SanDisk. "Write the changes to disks?" defaults to No.
- **Software selection:** untick "Debian desktop environment" and "GNOME", tick "SSH server", and keep "standard system utilities".
- **Wi-Fi in the installer** works out of the box, since firmware has been included since Debian 12. Choose `wlo1`, then WPA/WPA2 PSK.

## 10. References

- Claude Code install: https://code.claude.com/docs/en/quickstart
- Claude Code authentication (setup-token, precedence): https://code.claude.com/docs/en/authentication
- Agent SDK billing on Claude plans: https://support.claude.com/en/articles/15036540-use-the-claude-agent-sdk-with-your-claude-plan
- Docker on Debian: https://docs.docker.com/engine/install/debian/
- iwlwifi driver and `power_scheme`: https://wireless.docs.kernel.org/en/latest/en/users/drivers/iwlwifi.html
- Computer use reference implementation: https://github.com/anthropics/anthropic-quickstarts
