"""Area 5: read-only security checks. No exploitation, no credential reads (only stat/ls of the credential file)."""
import re
import socket

from common import Rec, http_call, sh

rec = Rec("security")
TS_IP = sh("ip -4 -o addr show tailscale0 | awk '{print $4}' | cut -d/ -f1").strip()
LAN_IP = sh("ip -4 route get 1.1.1.1 | grep -oP 'src \\K\\S+'").strip()

# S01-S03 reachability on non-loopback addresses
for cid, ip, label in (("S01", TS_IP, "tailscale"), ("S02", LAN_IP, "LAN")):
    out = sh(f"curl -sS -m 5 -o /dev/null -w '%{{http_code}}' http://{ip}:8765/health 2>&1")
    rec.case(cid, f"GET http://{ip}:8765/health ({label} address) must fail/refuse", "000" in out or "refused" in out.lower() or "timed out" in out.lower(),
             f"curl -> {out!r}", "P0", "Bind 127.0.0.1 only (uvicorn --host 127.0.0.1)")
ls = sh("ss -ltnH | awk '{print $4}' | grep ':8765$'")
rec.case("S03", "only 127.0.0.1:8765 is listening (no 0.0.0.0 / [::])", ls.strip() == "127.0.0.1:8765", f"listeners on :8765 = {ls!r}", "P0")
v6 = sh("curl -sS -m 3 -o /dev/null -w '%{http_code}' 'http://[::1]:8765/health' 2>&1")
rec.case("S03b", "IPv6 loopback [::1]:8765 not listening", "000" in v6 or "refused" in v6.lower(), f"curl -> {v6!r}", "P2")

# S04 credential file
st = sh("sudo -n stat -c '%a %U:%G %n' /etc/jobagent24/gmail_app_password")
rec.case("S04", "gmail_app_password mode 600", st.startswith("600 "), st, "P0", "chmod 600; chown jobagent:jobagent")
rec.case("S04b", "gmail_app_password owned by the service user (jobagent), not root-readable by others", " jobagent:jobagent " in st.replace(" ", "  ", 1) or "jobagent:jobagent" in st, st, "P1")
d = sh("sudo -n stat -c '%a %U:%G %n' /etc/jobagent24")
rec.case("S04c", "/etc/jobagent24 directory not world-writable", not d.split()[0].endswith(("2", "3", "6", "7")), d, "P1")

# S05 sqlite
files = sh("sudo -n sh -c 'stat -c \"%a %U:%G %n\" /opt/otp-broker/*.db*'")
bad = [l for l in files.splitlines() if not l.startswith("600 ")]
rec.case("S05", "SQLite db / -wal / -shm are mode 600", files and not bad, files.replace("\n", " | "), "P0", "Store.__init__ umask(0o077)+chmod; also set UMask=0077 in unit (present)")
dirm = sh("sudo -n stat -c '%a %U:%G %n' /opt/otp-broker /opt/otp-broker/.venv 2>&1")
rec.case("S05b", "/opt/otp-broker itself not world-accessible (750)", dirm.splitlines()[0].split()[0][-1] == "0", dirm.replace("\n", " | "), "P1")
rec.info("S05e", "/opt/otp-broker/.venv is 775 (group-writable by jobagent group)", dirm.replace("\n", " | "), "P2", "chmod -R g-w,o-rwx .venv; the venv is executable code run by the service")
rec.case("S05c", "db contains only request metadata (no message bodies/codes) -- schema check",
         "result" in sh("sudo -n sqlite3 /opt/otp-broker/otp-broker.db '.schema' 2>&1 || sudo -n python3 -c \"import sqlite3;print([r[0] for r in sqlite3.connect('file:/opt/otp-broker/otp-broker.db?mode=ro',uri=True).execute('select sql from sqlite_master')])\" 2>&1") or True,
         "waiters.result stores {code|url,message_id,received_at} of orphan results in PLAINTEXT until the row is deleted/expired (set_result); consumed(uidvalidity,uid,consumed_at) keeps no content", "P2",
         "Encrypt or hash orphan results, delete immediately after pickup, and purge consumed rows older than N days", ) if False else None
res_cols = sh("sudo -n python3 -c \"import sqlite3;c=sqlite3.connect('file:/opt/otp-broker/otp-broker.db?mode=ro',uri=True);print([r[1] for r in c.execute('pragma table_info(waiters)')]);print(c.execute('select count(*) from waiters where result is not null').fetchone(), c.execute('select count(*) from consumed').fetchone())\" 2>&1")
rec.info("S05d", "SQLite stores orphan OTP results in plaintext (waiters.result); consumed table grows unbounded", f"waiters columns / stored-results / consumed rows: {res_cols!r}. Code: Store.set_result persists {{'code': ...}} until the waiter row is deleted (expiry or retry pickup); `consumed` is never purged.",
         "P2", "Delete orphan result immediately on pickup, purge consumed rows > 7 days, keep db 600 (already)")

# S06 journal leak scan
def scan(window):
    raw = sh(f"sudo -n journalctl -u otp-broker --since '{window}' --no-pager -o short-iso", timeout=120)
    lines = raw.splitlines()
    stripped = []
    for l in lines:
        l = re.sub(r"^\S+\s+\S+\s+\S+\[\d+\]:\s*", "", l)                       # journald prefix (timestamp, host, unit[pid])
        l = re.sub(r"(?i)\b(?:process|pid|main pid)[ =:]+\d+", " ", l)                 # systemd 'Killing process 186153' (PIDs, 2nd false-positive class)
        l = re.sub(r"\[\d+\]", " ", l)                                          # "server process [PID]" (found as false positives on 1st run)
        l = re.sub(r"\d{4}-\d\d-\d\d[ T]\d\d:\d\d:\d\d[,.\d]*([+-]\d\d:?\d\d)?", " ", l)  # embedded app timestamps
        l = re.sub(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", " ", l)  # request_id uuids
        l = re.sub(r"<[^>\s]+@[^>\s]+>", " ", l)                                 # Message-IDs (opaque, metadata by design)
        l = re.sub(r"\b\d{1,3}(?:\.\d{1,3}){3}:\d+", " ", l)                      # ip:port
        stripped.append(l)
    hits = []
    for orig, l in zip(lines, stripped):
        for m in re.finditer(r"(?<![\w.:\-/])\d{6}(?![\w.:\-/])", l):
            hits.append((m.group(0), orig[-160:]))
    kw = [o[-160:] for o, l in zip(lines, stripped) if re.search(r"(code|otp|passcode|password)\W{0,3}\w*\s*[:=]?\s*\d{4,8}", l, re.I)]
    pw = [o[-120:] for o in lines if re.search(r"\b[a-z]{16}\b|(?:[a-z]{4} ){3}[a-z]{4}", o) and "Message" not in o]
    return len(lines), hits, kw, pw

n, hits, kw, pw = scan("1 hour ago")
rec.case("S06", "journal (last 1h): no 6-digit OTP-looking values after stripping timestamps/uuids/message-ids/ip:port", not hits and not kw,
         f"{n} lines scanned; 6-digit candidates={len(hits)} {hits[:5]}; keyword+digits lines={len(kw)} {kw[:3]}. METHOD: strip journald prefix, embedded timestamps, UUIDs, <message-ids>, ip:port; regex (?<![\\w.:/-])\\d{{6}}(?![\\w.:/-]); plus keyword-adjacent 4-8 digit scan. Note: live traffic in this window was only non-matching test waiters, so a real delivery path (which logs sender/message-id/timestamp only, per broker.py) was NOT exercised against a real code; the simulated deliveries run outside journald in test_races.", "P0")
n2, hits2, kw2, pw2 = scan("24 hours ago")
rec.case("S06b", "journal (last 24h) same scan", not hits2 and not kw2, f"{n2} lines; candidates={len(hits2)} {hits2[:5]}; keyword lines={len(kw2)}", "P0")
rec.case("S07", "journal contains no app-password-shaped token (16 lowercase letters / 4x4 groups)", not pw2, f"{len(pw2)} suspicious lines (values withheld)", "P0")
req_log = sh("sudo -n journalctl -u otp-broker --since '1 hour ago' --no-pager | grep -c 'registered kind='")
dom_log = sh("sudo -n journalctl -u otp-broker --since '24 hours ago' --no-pager | grep 'registered kind=' | head -3")
rec.info("S08", "Log hygiene: wait registration logs sender_domains + timeout (job-site names), delivery logs sender + message-id", f"{req_log} registrations/h; sample: {dom_log[-300:]!r}. Reveals which employers the agent is applying to to anyone with journal access; no codes/urls/hints logged.", "P2", "Acceptable for a local box; hash or truncate domains if journal is shipped off-host")

# S09 unit/user
u = sh("systemctl show otp-broker -p User -p NoNewPrivileges -p ProtectSystem -p ProtectHome -p PrivateTmp -p MemoryMax -p CapabilityBoundingSet -p RestrictAddressFamilies -p TimeoutStopUSec -p Restart -p WatchdogUSec")
pid = sh("systemctl show otp-broker -p MainPID --value")
proc = sh(f"ps -o user=,pid=,args= -p {pid}")
caps = sh(f"grep -E 'CapEff|NoNewPrivs|Uid' /proc/{pid}/status")
rec.case("S09", "service runs as non-root User=jobagent (unit AND live process)", "User=jobagent" in u and proc.split()[0] != "root", f"{u.replace(chr(10),' ')} | proc: {proc} | {caps.replace(chr(10),' ')}", "P0")
rec.case("S10", "no effective capabilities", "CapEff:\t0000000000000000" in caps, caps.replace("\n", " "), "P2", "CapabilityBoundingSet= (empty), AmbientCapabilities=")
score = sh("systemd-analyze security otp-broker --no-pager 2>&1 | tail -1")
rec.info("S11", "systemd hardening exposure score", score, "P2", "Add: MemoryMax=256M, RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX, CapabilityBoundingSet=, SystemCallFilter=@system-service, ProtectKernelTunables/Modules, RestrictNamespaces, LockPersonality, TimeoutStopSec=15, IPAddressDeny/Allow to Gmail only if feasible")
mem = "MemoryMax=infinity" in u
rec.case("S12", "resource limits set (MemoryMax / TasksMax)", not mem, u.replace("\n", " "), "P2", "MemoryMax=256M TasksMax=64")

# S13 unauthenticated local access
who = sh("sudo -n -u nobody curl -sS -m 5 http://127.0.0.1:8765/health 2>&1 | head -c 200")
rec.case("S13", "API requires local authentication (any local user can call /health, /v1/wait)", False,
         f"as user `nobody`: {who!r}. /v1/wait is unauthenticated: any local account, container with host networking, or SSRF-able local service can request OTPs/magic links for any sender domain (allowed_link_hosts is also caller-supplied).", "P1",
         "Bind a unix socket with mode 660 owned by jobagent:jobagent-clients, or require a bearer token from a 600 file; restrict sender_domains to a server-side allowlist")
docs = http_call("GET", "/openapi.json")
rec.case("S14", "API schema endpoints disabled", docs["status"] == 404, f"/openapi.json -> {docs['status']}", "P2", "FastAPI(docs_url=None, redoc_url=None, openapi_url=None)")
rec.case("S15", "[CODE-REVIEW] sender authenticity: From header trusted, no SPF/DKIM/DMARC check", False,
         "broker._matches/domain_matches use only the parsed From address. Gmail normally spam-folders spoofed mail, but anything delivered to INBOX with a forged From: noreply@<allowed-domain> (or a legit-but-compromised/forwarding sender, or a lookalike 'Reply-To' trick) is accepted as the OTP. Also kind=link returns attacker-chosen URLs on any allowlisted host.", "P1",
         "Require Authentication-Results: dkim=pass/dmarc=pass (Gmail adds it) with header.d aligned to sender_domains, and Return-Path/envelope domain consistent; skip messages in SPAM/with X-Gm-Spam")
rec.case("S16", "[CODE-REVIEW] hint is caller-controlled substring match over full message body", True, "Not exploitable beyond own mailbox; noted only.", "P2")
rec.done()
