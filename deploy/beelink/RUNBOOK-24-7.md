# 24/7 stack: coexistence plan

The namespaced stack (`jobagent24-*`) runs beside the old one on the same Beelink. The old
`/opt/job-agent` units keep running untouched; nothing here edits them or `provision-debian.sh`.

## 1. Layout

| | Old | New |
|---|---|---|
| Code + venv | `/opt/job-agent` | `/opt/jobagent24` |
| State | `/var/lib/job-agent` | `/var/lib/jobagent24` |
| Secrets | `/etc/job-agent/*.env` | `/etc/jobagent24/{base,claude,dashboard}.env` |
| Units | `apply-*`, `job-*`, `resume-worker`, `chrome-profile@0` | `jobagent24-{discovery,screening,resume,apply,monitor,api}` |
| Display | `:0` (LinkedIn), `:1` (apply) | `:2` |
| VNC / noVNC | 5900/6080, 5901/6081 | 5902 / 6082 (loopback) |
| API | none | `127.0.0.1:8024` |

The user is the same unprivileged `jobagent` (no sudo, no docker group). Shared on purpose: the
`xvfb@`/`x11vnc@`/`novnc@` templates (instance 2 only), `notify-failure@`, the Claude CLI under
`/var/lib/job-agent/.local/bin` and `/etc/job-agent/vncpasswd`. Never run `systemctl stop` on a
template without an instance.

## 2. Roles and timers (staggered against the old ones)

| Unit | Runs | Timer | Old twin |
|---|---|---|---|
| discovery | `job_sourcing.py` | `01/2:13` | job-sourcing (`00/2:13`) |
| screening | `job_pick.py` | `01/2:43` | job-pick (`00/2:43`) |
| resume | `resume_agent.py --drain` | `*:15/30` | resume-worker (`*:0/30`) |
| apply | `apply_worker.py --prepare` (never submits) | `*:10/20` | apply-prepare (`*:0/20`) |
| monitor | `monitor.py` once | none (Actions runs it) | none on the box |
| api | uvicorn `api.dashboard:app` | none (long-running) | none |

Submitting stays on the old `apply-submit.service`, the only armed unit. The new stack never sets
the armed variable. Do not enable both stacks' timers for the same role long-term: they would
spend model calls on the same rows (claims are atomic, so rows are not double-processed, but the
budget is shared).

## 3. Install

1. `provision-debian.sh` must already have run.
2. `sudo JOBAGENT24_REPO_URL=<url> bash deploy/beelink/provision-jobagent24.sh <signed-tag>`
3. Fill `/etc/jobagent24/base.env` (the five core secrets), `claude.env` (`claude setup-token`),
   `dashboard.env` (`openssl rand -hex 32`).
4. Watched first runs (section 4), then `sudo bash provision-jobagent24.sh <tag> --enable-timers`.

### Redeploying a running install

`provision-jobagent24.sh <new-tag>` is safe to repeat; it never edits code under live units. Exact sequence:

1. Takes `/var/lock/jobagent24-deploy.lock` (a second concurrent run exits 1).
2. Records which `jobagent24-*` timers and the api were active.
3. Stops the timers, then every `jobagent24-*` service (a running oneshot is waited for), and **verifies each unit is `inactive`/`failed`/`unknown`** (`require_stopped`). A unit that will not stop aborts the deploy (exit 1) before any checkout or pip. Old-stack units are never touched.
4. Fetches, verifies the signed tag, checks it out, upgrades the venv (`pip install`).
5. Installs units, `daemon-reload`.
6. Always starts the api (`restart` if it was active, else `start`), then polls `http://127.0.0.1:8024/api/health` for up to 30 s. No healthy answer exits 1 (`journalctl -u jobagent24-api -n 50`) with the timers still stopped. The one exception is a first install (api was not active and `dashboard.env` has no `DASHBOARD_API_TOKEN` value): it logs what to fill in and exits 0 with the timers stopped.
7. Only after a healthy answer, restarts the timers that were active (all four with `--enable-timers`).

If the script fails between 3 and 7 the namespace (including the timers) stays **stopped** deliberately. Fix the cause and re-run. Migration `20261013000000` must be pushed (`db_migrate.yml` dryrun, then push) before the new tag runs; the dashboard decision RPC and the document lease depend on it.

### Approval flow (where the gate lives)

The dashboard's Approve is intent: it records an `application_runs` row (adapter `dashboard`) and never sets `approved_at`. Only the contact-manager's `/api/applications/[id]/submit`, which signs with `APPROVAL_SIGNING_KEY` and calls the `approve_application` RPC, makes a row submittable. `apply_worker --submit` takes only rows that RPC approved and re-verifies the signature. The dashboard API deliberately does not hold the key. The worker logs dashboard approvals still awaiting the signed step (`[APPLY-WORKER] ... await the signed approve_application step`).

## 4. Verify each service

| Service | Command | Expect |
|---|---|---|
| any | `sudo systemctl start jobagent24-<x>; journalctl -u jobagent24-<x> -n 50` | ExecStartPre passes; exit 0 |
| discovery | `systemctl start jobagent24-discovery` | `[SOURCING]` DONE line, an `agent_runs` row `source='job_sourcing'` |
| screening | `systemctl start jobagent24-screening` | scored count, no stale-queue exit 1 |
| resume | `systemctl start jobagent24-resume` | preflight + canary pass, `rows=N` |
| apply | `systemctl start jobagent24-apply` | `[APPLY-WORKER] START prepare`; an `application_runs` row; a v2 resume uploaded for a Greenhouse/Lever row |
| monitor | `systemctl start jobagent24-monitor` (only with Actions' monitor paused) | `monitor.log` DONE |
| api | `curl -s localhost:8024/api/health` | `{"status":"ok",...}`; `/api/queue` is 403 without a token, 401 with a wrong one, 200 with the right one |
| display :2 | `ssh -L 6082:localhost:6082 kishore@beelink`, open `http://localhost:6082/vnc.html` | blank desktop |

## 5. Browser provisioning convention

- The apply worker launches its own Playwright Chrome (`APPLY_BROWSER_CHANNEL=chrome`,
  `APPLY_BROWSER_HEADLESS=0`) on display `:2`, serialized by `APPLY_DISPLAY_LOCK=/var/lib/jobagent24/display2.lock`.
- Logins persist as per-tenant `storage_state` files in `APPLY_SESSIONS_DIR=/var/lib/jobagent24/sessions` (0700).
- `apply_pipeline.py` (unit `jobagent24-pipeline.service`, dry run, never armed, **not enabled by
  provisioning**) launches its own Chrome the same way, with no debugging port and no flags that hide
  automation, and gives every job a fresh browser context: service workers blocked, WebSockets never
  connected, `Worker`/`SharedWorker` refused (each refusal counted as `workers_refused` in the job's
  result facts). It holds `display2.lock` for its browser's lifetime, so `jobagent24-apply` skips
  runs while it is up. It runs the network self-test after every launch and recycles Chrome every
  `APPLY_BROWSER_MAX_JOBS` (100) jobs. Supervised soak:
  `sudo systemctl start jobagent24-pipeline` then `journalctl -u jobagent24-pipeline -f`; stop with
  `sudo systemctl stop jobagent24-pipeline`. Soak criteria: per-job-context-plan.md section g and
  "How each correction was carried out" item 7.
- A persistent profile, if ever needed, follows `chrome-profile@.service`: unit `jobagent24-chrome@%i.service`,
  `--user-data-dir=/var/lib/jobagent24/profiles/%i`, `HOME=/var/lib/jobagent24`, `JoinsNamespaceOf=xvfb@%i.service`,
  never `--headless` or `--remote-debugging-port`. Slot 2 is reserved (`profiles/2` exists); no such unit ships
  yet. LinkedIn stays on the old slot 0.

## 6. Roll back

```
sudo systemctl disable --now jobagent24-{discovery,screening,resume,apply}.timer
sudo systemctl disable --now jobagent24-api jobagent24-pipeline xvfb@2 x11vnc@2 novnc@2
sudo rm /etc/systemd/system/jobagent24-* && sudo systemctl daemon-reload
```
Old units are unaffected. Optional cleanup: `sudo rm -rf /opt/jobagent24 /var/lib/jobagent24 /etc/jobagent24`.
Rows the new apply worker already prepared remain valid: submission, leases and approvals live in Supabase.
