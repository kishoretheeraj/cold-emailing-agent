# Beelink host provisioning runbook (M1)

Every command here runs **on the Beelink itself**, by hand, by the operator. Nothing in CI or in
any agent session can run or verify these — the box is not reachable from anywhere else in this
repo's tooling. Work top to bottom; each section is independently re-runnable.

Assumed: a fresh Ubuntu Server install, a `jobagent` user, and network on the LAN.

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

## 5. Confine VNC to the LAN

**Allow SSH before enabling the firewall.** `ufw`'s default is deny-incoming; enabling it over an
SSH session without an explicit allow rule drops that session immediately, and on a headless box
with no other access path, that's a keyboard-and-monitor recovery trip. Do the SSH rule first,
always:

```bash
sudo ufw allow OpenSSH   # do this BEFORE `ufw enable`, or you will lock yourself out

# noVNC front ends, slots 0-3 (see the port scheme comment in novnc@.service: 6080-6083)
sudo ufw allow from 192.168.0.0/16 to any port 6080:6083 proto tcp

sudo ufw enable
sudo ufw status verbose   # confirm both the SSH and noVNC rules are listed before disconnecting
```

No port forwarding, no public exposure. Adding Tailscale later is a transparent additional
interface and needs no change here.

## 6. Log in to LinkedIn once, by hand

Open `http://<beelink-lan-ip>:6000/vnc.html` from a laptop on the same LAN, enter the VNC
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
is CPU-bound (4 E-cores, no hyperthreading) well before RAM runs out, so do not size slots from
RAM headroom alone.

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
- `job_applications` gained rows with `source='linkedin'` and clean `job_url`s with no
  `?refId=` tail;
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
