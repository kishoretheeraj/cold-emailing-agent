# BUILDER LOCK — 24/7 job-agent build

**SUPERSESSION #2 (2026-10-11 ~00:25 ET):** The replacement coordinator spawned
2026-10-11 ~00:08 ET claimed this lock but produced ZERO build artifacts across
2 consecutive checks (00:18 ET, 00:25 ET — ~17 min, no jobagent24/ dir, no new
files, branch overnight-247-restart still at 28c3b42, no matching processes on
the VM). Per the mission 2-check kill rule it is SUPERSEDED. If the 00:08
coordinator wakes: stand down immediately — do NOT build, do NOT write code,
do NOT deploy.

**New approach (different from both dead coordinators):** do NOT rebuild from
scratch. The 24/7 system was ALREADY built and test-passing in
~/workspace/cold-emailing-agent at commit 5fc51e5 (2026-10-11 03:40 UTC:
resume/v2/, answer_bank_vault.py, security/, ops/, ats_api/, api/dashboard.py,
147 tests passing). Phase D now = PORT those modules into this repo
(~/workspace/jobagent-247/, branch overnight-247-restart), Codex-review them,
write jobagent24-*.service units, deploy to /opt/jobagent24 on the Beelink.

**Owned by:** autonomous-10min-check root, 2026-10-11 ~00:25 ET (port-and-deploy track).

**Branch:** `overnight-247-restart` in ~/workspace/jobagent-247/
**Deploy target:** /opt/jobagent24 on Beelink (100.77.193.40), new
namespaced units jobagent24-{discovery,screening,resume,apply,monitor,api}.service

Prior claims:
- Original: 2026-10-10 ~23:42 ET (superseded 2026-10-11 ~00:08 ET)
- Replacement coordinator: 2026-10-11 ~00:08 ET (superseded ~00:25 ET)
