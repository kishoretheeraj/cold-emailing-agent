# Beelink host provisioning runbook (M1)

Every command here runs **on the Beelink itself**, by hand, by the operator. Nothing in CI or in
any agent session can run or verify these — the box is not reachable from anywhere else in this
repo's tooling. Work top to bottom; each section is independently re-runnable.

**The real box runs Debian 13, not Ubuntu.** On it, sections 1-5 are replaced by one idempotent
script, run on the Beelink after a signed tag exists (see `docs/beelink-server.md`):

```bash
# Mac: review the exact commit you're deploying (`git show --stat <commit>`), then sign THAT commit
# -- never a bare `tag -s`, which signs whatever HEAD is, and build-continue.yml can push unreviewed
# commits to main. Uses your existing SSH key: no GPG, no global config change.
git -c gpg.format=ssh -c user.signingkey=$HOME/.ssh/id_ed25519 tag -s beelink-v1 <commit> -m "beelink-v1" && git push origin beelink-v1
# Mac: copy your own Calibri (from Microsoft Word) to the box -- provision refuses to run without it
scp "/Applications/Microsoft Word.app/Contents/Resources/DFonts/"[Cc]alibri*.ttf kishore@beelink:/tmp/
# Beelink: install it
sudo install -d /usr/local/share/fonts/calibri && sudo install -m 0644 /tmp/[Cc]alibri*.ttf /usr/local/share/fonts/calibri/ && rm /tmp/[Cc]alibri*.ttf
# Mac: copy the script and the signer list to the box
scp deploy/beelink/provision-debian.sh deploy/beelink/allowed_signers kishore@beelink:~/
# Beelink: provision (prompts once for the VNC password), then fill in the secrets
sudo bash ~/provision-debian.sh beelink-v1 ~/allowed_signers
sudo nano /etc/job-agent/base.env
# Mac: create a one-year subscription token (opens a browser); copy the printed token
claude setup-token
# Beelink: paste it after CLAUDE_CODE_OAUTH_TOKEN=
sudo nano /etc/job-agent/claude.env
```

It differs from the Ubuntu steps below in four ways: Debian's own Python 3.13 (no deadsnakes PPA
on Debian), tags verified against `/etc/job-agent/allowed_signers` with SSH signatures instead of
a GPG keyring, no `/etc/fstab` edit (Chrome already runs with `--disable-dev-shm-usage`), and no
ufw (noVNC binds 127.0.0.1; see section 5). Sections 6-9 apply unchanged apart from the URL.

The original Ubuntu steps follow for reference. They assumed a fresh Ubuntu Server install, a
`jobagent` user, and network on the LAN.

## 1. Host baseline

```bash
# Never sleep. A job box that suspends is a job box that misses its timers.
sudo systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target

# Security patches apply; reboots are scheduled by hand, never mid-task.
sudo apt-get install -y unattended-upgrades
echo 'Unattended-Upgrade::Automatic-Reboot "false";' \
  | sudo tee /etc/apt/apt.conf.d/52unattended-upgrades-local

sudo timedatectl set-ntp true

# Chrome's 64MB default /dev/shm crashes tabs. Either raise it here or rely on the
# --disable-dev-shm-usage flag already in chrome-profile@.service (both is fine).
echo 'tmpfs /dev/shm tmpfs defaults,size=2G 0 0' | sudo tee -a /etc/fstab
sudo mount -o remount /dev/shm
```

Also set **BIOS → "restore on AC power loss" = on**, so a real power outage comes back up.

## 2. Packages

**Assumes Ubuntu Server 22.04 LTS**, which ships Python 3.11 in its default repos. **If the box is
actually running 24.04 LTS or newer, `python3.11` is not installable from the default repos**
(24.04 ships 3.12 as `python3`) -- add the deadsnakes PPA first:

```bash
# Only if `apt-cache policy python3.11` shows nothing -- i.e. only on 24.04+, skip on 22.04:
sudo add-apt-repository -y ppa:deadsnakes/ppa
sudo apt-get update
```

```bash
sudo apt-get update
sudo apt-get install -y \
  xvfb x11vnc novnc websockify xdotool scrot mutter tint2 \
  python3.11 python3.11-venv git curl smem gnupg

# Chrome (not Chromium): the profile has to look like the browser the operator really uses.
curl -fsSL https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb \
  -o /tmp/chrome.deb
sudo apt-get install -y /tmp/chrome.deb
```

## 3. Layout, secrets, and the repo

The box pulls **signed git tags only**, never `main`. `build-continue.yml` pushes unreviewed
AI-generated commits to `main` hourly; auto-pulling that onto the box that will eventually hold
the ARMED submit credential would defeat the isolation `APPLY_AGENT_ARMED` exists to guarantee.

**`git tag -v` needs the signer's public key in `jobagent`'s own GPG keyring first**, or every
verification fails with "no public key" on the very first attempt -- this is a real prerequisite
for the signed-tags-only deploy story, not an implementation detail to skip. Do this once, on your
own machine (wherever you already run `git tag -s`), then move the *public* key to the box:

```bash
# On your own machine, if you don't already have a signing key:
gpg --quick-generate-key "Your Name <you@example.com>" ed25519 sign 2y
git config --global user.signingkey <the key ID gpg just printed>
git config --global tag.gpgSign true   # optional: sign every tag by default

# Export the PUBLIC key only -- never move a private key onto the always-on box:
gpg --armor --export <the key ID> > signing-pubkey.asc
scp signing-pubkey.asc jobagent@<beelink-host>:/tmp/
```

```bash
# Back on the Beelink, as jobagent:
gpg --import /tmp/signing-pubkey.asc
rm /tmp/signing-pubkey.asc

sudo mkdir -p /opt/job-agent /var/lib/job-agent/profiles/0 /etc/job-agent
sudo chown -R jobagent:jobagent /opt/job-agent /var/lib/job-agent

sudo -u jobagent git clone https://github.com/<owner>/cold-email-agent.git /opt/job-agent
cd /opt/job-agent
sudo -u jobagent git fetch --tags
sudo -u jobagent git -c gpg.program=gpg tag -v <tag>   # must verify before checkout
sudo -u jobagent git checkout <tag>

sudo -u jobagent python3.11 -m venv /opt/job-agent/.venv
sudo -u jobagent /opt/job-agent/.venv/bin/pip install -r requirements.txt

sudo cp deploy/beelink/env/base.env.example /etc/job-agent/base.env
sudo chown root:root /etc/job-agent/base.env
sudo chmod 0600 /etc/job-agent/base.env
sudo nano /etc/job-agent/base.env   # fill in the five values by hand

# VNC password. Not a LinkedIn credential -- this only guards the console.
sudo x11vnc -storepasswd /etc/job-agent/vncpasswd
sudo chown jobagent:jobagent /etc/job-agent/vncpasswd
sudo chmod 0600 /etc/job-agent/vncpasswd
```

`x11vnc@.service` runs as `User=jobagent`, not root, so the file must be owned by `jobagent` --
`chmod` alone leaves it root-owned and unreadable by the service, which fails to start with
`-rfbauth` set. (This is unrelated to `/etc/job-agent/base.env` and `armed.env`, which stay
`root:root` on purpose -- systemd reads `EnvironmentFile=` as PID 1, before the service's own
user is dropped into.)

**Do not copy the repo's own `.env` to this box.** `config.load_dotenv()` would source it, and
that file is not what defines this host's environment.

## 4. Install the units

```bash
sudo cp /opt/job-agent/deploy/beelink/systemd/*.service /etc/systemd/system/
sudo cp /opt/job-agent/deploy/beelink/systemd/*.timer /etc/systemd/system/
sudo systemctl daemon-reload

sudo systemctl enable --now xvfb@0 chrome-profile@0 x11vnc@0 novnc@0
systemctl status xvfb@0 chrome-profile@0 x11vnc@0 novnc@0
```

## 5. Reach VNC through an SSH tunnel (no firewall needed)

`novnc@.service` binds `127.0.0.1:608%i` and `x11vnc@.service` runs with `-localhost`, so neither
is reachable from the LAN or the tailnet. Reach slot 0 from the Mac over Tailscale:

```bash
ssh -N -L 6080:localhost:6080 kishore@beelink
```

Don't add ufw/nft rules for this: Docker shares the host and Docker's docs warn that host firewall
rules made with `nft` aren't supported alongside it (`docs/beelink-server.md` rule 6).

## 6. Log in to LinkedIn once, by hand

With the section 5 tunnel open, open `http://localhost:6080/vnc.html` on the Mac (slot 0's noVNC front end -- 6080 is `608%i` for `%i=0`; a later slot would be 6081-6083 and needs its own `-L`), enter the VNC
password, and sign in to LinkedIn inside that Chrome window — including any 2FA. The session
cookie now lives in `/var/lib/job-agent/profiles/0` and survives restarts.

This is the **only** place a LinkedIn credential is ever entered. It is never typed by the agent,
never stored in `/etc/job-agent/base.env`, and never appears in this repo.

## 7. Validate real memory before adding load

Use `smem`/`ps_mem`, **not** `ps` — `ps` over-counts Chrome's shared mappings and will tell you
a slot costs several times what it does.

```bash
sudo smem -t -k -P 'chrome|Xvfb|x11vnc|websockify'
```

Target: 3 concurrent display slots steady-state (1 LinkedIn + 2 ATS later), hard cap 4. The box
is a Ryzen 7 5800H (8 cores / 16 threads), but only ~12.6 GiB of its 16 GB is visible to Linux (the
iGPU reserves the rest), so RAM is the binding constraint here. Every unit carries a `MemoryMax=`;
`tests/test_beelink_units.py` asserts three slots fit the budget.

## 8. First real run — manually, before the timer goes live

Same "run it manually before scheduling it" pattern that found 4 real bugs on Stage-1 visa
intel's first live run and 1 on Form D's, both despite green suites.

```bash
sudo systemctl start job-linkedin-ingest.service
journalctl -u job-linkedin-ingest.service -f
sudo -u jobagent tail -f /opt/job-agent/cu_linkedin.log
```

Watch the session live in the noVNC window while it runs. Confirm, before enabling the timer:

- the cursor moves in visible steps and pauses irregularly between actions (not a metronome);
- the agent never clicks Apply, Connect, Follow, Message, or Save;
- `job_applications` gained rows with `source='linkedin'` and every new `job_url` matches
  `https://www.linkedin.com/jobs/view/<digits>` (with an optional query string) -- NOT merely "no
  `?refId=`/`?trackingId=` tail", which a collapsed `.../jobs/collections/recommended` URL would
  also pass;
- `api_usage_log` gained `module='cu_linkedin'` rows with real token counts;
- `agent_runs` gained one row with `source='cu_linkedin'`.

Then, and only then:

```bash
sudo systemctl enable --now job-linkedin-ingest.timer
systemctl list-timers job-linkedin-ingest.timer
```

## 9. If a CAPTCHA or login challenge appears

`cu_linkedin.log` will carry
`[CU-LINKEDIN] | CAPTCHA or login challenge -- needs a human at the VNC console for display
slot 0`. VNC in and solve it yourself, in the browser. Nothing in this system may ever solve,
bypass, or work around it — not in code, not by retrying, not by switching tools.

## 10. Apply worker (first-ten-applications Phase D)

`apply-prepare` fills applications in real Chrome on display :1 and stops before Submit;
`apply-submit` is the only Beelink unit with `APPLY_AGENT_ARMED=1` and submits only rows you
approved in the contact-manager, whose signature it can verify. Provisioning installs both units
and starts display :1 (`xvfb@1`, `x11vnc@1`, `novnc@1`) but never enables their timers.

```bash
# Beelink: the same signing key Vercel has as APPROVAL_SIGNING_KEY (32+ random characters)
sudo nano /etc/job-agent/approval.env
# Beelink: the vault key for per-site passwords (generate it as the file's comment says; keep a copy)
sudo nano /etc/job-agent/vault.env
# Beelink: the takeover view, tailnet-only over HTTPS (provisioning already ran this; check it)
sudo tailscale serve status
```

Set `NEXT_PUBLIC_TAKEOVER_URL` in Vercel to `https://<beelink tailnet name>:8443/vnc.html` so the
"Needs you" card links straight to the browser; it opens from the Mac or the phone while either is
on the tailnet. The VNC password from section 3 guards it.

**Watched first prepare**, with nothing approved:

```bash
sudo systemctl start apply-prepare && journalctl -u apply-prepare -f
tail -f /opt/job-agent/apply_worker.log   # apply_agent's own lines land here too
```

Watch display :1 while it runs. Expected: rows move to `ready_for_review` (or `needs_input` with a
reason), each with an `application_runs` row (`apply_status.yml` shows them), and no Submit click.
A CAPTCHA shows a "Needs you" card; solve it in the viewer, press I'm done, and the worker
continues. Then:

```bash
sudo systemctl enable --now apply-prepare.timer
```

**Submit**, only after the prepare output looks right and `APPLY_SUBMIT_HOST=beelink` is set in
Vercel (so approvals stop dispatching the GitHub workflow):

```bash
sudo systemctl enable --now apply-submit.timer
```

Approve one application in the contact-manager and watch `journalctl -u apply-submit -f` and display
:1. A submit that cannot verify the approval signature puts the row back in `needs_input` before any
browser opens; a missing `approval.env` key stops the run without touching the approval.

## 11. Sourcing and scoring (fifty a day)

`job-sourcing` finds postings every 2 hours. It reads the Simplify new-grad feed and sweeps the
company boards in `job_boards` (Greenhouse, Ashby and Lever whole boards, plus a Workday search).
It drops off-target titles, senior roles, non-US locations, stale postings and roles that refuse
sponsorship, and saves the rest without duplicates. It makes no model calls and needs only
`base.env`.

`job-pick` scores the new rows 30 minutes later. It runs the same filters, then a local embedding,
then the fit judge on the Claude subscription, 10 postings per call. `jobright_pull.yml` no longer
scores anything.

Migration `20261009000000` must be pushed (`db_migrate.yml`) before either unit runs. The queues
read its columns.

**Watched first run:**

```bash
sudo systemctl start job-sourcing && journalctl -u job-sourcing -f
tail -f /opt/job-agent/job_sourcing.log      # one DONE line with counts per outcome
sudo systemctl start job-pick && journalctl -u job-pick -f
```

Expected: the sourcing `DONE` line shows `saved=…`, `skipped_location=…`, `duplicate_…` and
`boards_ok=…` counts. A second immediate `start` saves 0 (everything is a duplicate). `job-pick`
writes verdicts, and strong rows wait for `resume-worker`. Then:

```bash
sudo systemctl enable --now job-sourcing.timer job-pick.timer
```

Search preferences live in the `job_search_preferences` row of the Prompts page (JSON: titles,
seniority words, locations, posting age, per-company cap, daily submit cap). A bad value falls
back to its default. `job_pick` needs CPU-only torch. `requirements-beelink.txt` installs it from
PyTorch's CPU index, so reprovision or `pip install -r requirements-beelink.txt` after pulling.

## 12. Every site: recon and the universal filler

The universal filler (`universal_filler.py`, spec `docs/superpowers/specs/2026-10-09-every-site-design.md`)
fills one-page application forms on sites without a hand-written filler. It runs only where
`APPLY_UNIVERSAL_ENABLED=1` (the four apply/resume/pick units) and only for the platforms in
`APPLY_UNIVERSAL_PLATFORMS` (shipped: `generic,workable,smartrecruiters`; keep the list identical in
all four units). Multi-step forms, sign-in walls without a saved session, and Apply links that open
another site stop with a reason in "Needs you".

**Recon first** (read-only: it presses only "Apply", never types, never submits):

```bash
cd /opt/job-agent && sudo -u jobagent .venv/bin/python scripts/form_recon.py --from-feed 3 --out /tmp/recon.json
```

Send the JSON back to the session that builds adapters (it holds labels and button names only).
Each report says per site whether the form is one page (`state: form`, a `submit` role and no
`next` role), a wizard, a sign-in wall, or an iframe.

**Watched preview:** with `apply-prepare.timer` stopped, `sudo systemctl start apply-prepare` and
watch `journalctl -u apply-prepare -f` for `[APPLY-UNIVERSAL]` lines. A sign-in wall asks you in the
contact-manager to sign in once on the takeover view; the session is then saved for that site (only
that site's cookies). Add a platform to `APPLY_UNIVERSAL_PLATFORMS` (all four units, `daemon-reload`)
only after its watched preview and one watched submit look right.
