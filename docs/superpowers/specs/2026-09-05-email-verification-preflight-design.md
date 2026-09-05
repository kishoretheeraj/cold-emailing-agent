# Email Verification Pre-flight — Design

**Status:** Draft for review
**Date:** 2026-09-05

Phase 5 of the full-fledged job-platform buildout (see
`docs/superpowers/specs/2026-08-26-full-fledged-job-platform-buildout.md`).
A bounce-risk check on a contact's email address, run once before the
first-touch draft is even generated.

**Goal:** Stop the agent from spending a Claude call (and drafting an email
that will just bounce) on a contact whose email address is syntactically
malformed or whose domain cannot receive mail at all.

---

## Problem

Nothing in the pipeline today looks at `contact["email"]` before using it.
A typo'd address (`@gmial.com`), a placeholder left over from manual data
entry, or a domain that no longer exists all reach `_execute_draft` and
`create_draft` exactly like a real one — the draft gets created, the contact
advances to `*_drafted`, and the bounce (if the user even notices it) happens
in Gmail after the fact, by which point the agent already spent a Claude
call generating a body no one will read.

## Non-goals

- **Not real deliverability verification.** No SMTP handshake, no mailbox
  ping. That is exactly what `email-sleuth`-style tools do next and it is
  explicitly out of scope here: an SMTP `RCPT TO` probe is slow, frequently
  blocked or rate-limited by receiving servers, and easily mistaken by a
  receiving mail server for spam-harvesting — a much bigger footprint than
  this repo's stated bounce-risk goal needs. The check stops at "can this
  domain receive mail at all," which is the same tier of confidence
  `check_wrong_company` / `check_stale_year` already operate at: cheap,
  deterministic-where-possible signals, not proof.
- **Not a `company_intel`-style persisted signal.** An address that fails to
  resolve today might resolve tomorrow (a DNS blip, a mail-provider
  migration) and vice versa. Nothing here writes to Supabase. The governance
  posture is the same one already documented for the visa gate and Form D:
  a negative result must never be presented as a permanent fact.
- **Not integrated into `preflight.check()`'s list.** Every existing
  pre-flight check operates on the *generated body* and its failure triggers
  one retry with an error-list prompt, because a body defect is something
  Claude can fix by rewriting. A bad email address is a property of the
  *contact record*, not the body — rewriting the email text cannot repair it,
  so folding this into `preflight.check()`'s regenerate-and-recheck loop
  would burn a wasted Claude call for zero benefit. This ships as its own
  small gate instead (the stub in the master spec explicitly leaves this
  choice open: "additive to `preflight.py`'s existing checks **or** a new
  pre-draft gate").
- **No new dependency beyond `dnspython`.** Nothing else about the pipeline
  changes.

---

## Design

### New module `email_verify.py`

Self-contained, in the shape of `content_trust.py` / `ats.py`: no `db`,
`gmail`, or `emailer` import. Its only outside dependency is `dnspython`
(new entry in `requirements.txt` — the stdlib has no MX-record lookup;
`socket.getaddrinfo` only resolves A/AAAA, which would silently accept a
domain that has a website but explicitly refuses mail via an empty MX
record).

Public surface:

```python
EmailVerifyResult = namedtuple("EmailVerifyResult", ["status", "reason"])

def verify(email) -> EmailVerifyResult
```

`status` is one of three values, and the three-way split is the whole point
of this design:

- **`"invalid"`** — a deterministic, non-network fact: the address fails
  syntax validation, *or* the domain has neither an MX record nor a
  fallback A/AAAA record (i.e. DNS gave a conclusive "this domain cannot
  receive mail," not "the lookup didn't work"). This is the only status
  that blocks a draft.
- **`"unknown"`** — the DNS lookup itself failed or timed out (no
  nameservers reachable, resolver timeout, unexpected exception). This is
  the "not observed" case from every other governance-tagged signal in this
  repo (the visa gate, Form D, decision-context) and it is never treated as
  a negative. A transient resolver hiccup must never cost the user a draft.
- **`"valid"`** — syntax passes and the domain resolves an MX record (or,
  per RFC 5321 fallback behavior, an A/AAAA record when no MX is published).

**Syntax check.** A standard, non-backtracking `local@domain.tld` regex
(same style as `preflight.py`'s existing patterns — simple character
classes, no nested quantifiers). A failure here is `"invalid"` without ever
touching the network — no reason to pay a DNS round trip for `"not-an-email"`.

**Domain check.** `dns.resolver.resolve(domain, "MX")` with a short,
explicit timeout (`EMAIL_VERIFY_DNS_TIMEOUT_SECONDS`, mirroring
`ATS_TIMEOUT_SECONDS`'s reasoning — this runs inside the per-contact loop of
a cron job and a slow or blackholed resolver must not stall a run):

| Resolver outcome | Meaning | Result |
|---|---|---|
| MX records returned | domain accepts mail | `"valid"` |
| `NoAnswer` (domain exists, no MX) | fall back to A/AAAA lookup | `"valid"` if A/AAAA resolves, else `"invalid"` |
| `NXDOMAIN` | domain does not exist | `"invalid"` |
| `Timeout`, `NoNameservers`, anything else | lookup did not complete | `"unknown"` |

**Never raises.** `verify()` wraps the whole check in a single
`try/except Exception` that degrades to `"unknown"` on anything unforeseen
— same posture as `ats.fetch_jobs`'s "enrichment must never cost a draft"
rule, except here an `"unknown"` doesn't mean "no enrichment," it means
"draft anyway," since the whole point is to never manufacture a false block
out of a network problem.

### Wiring into `agent.py`

The check has to run **before** the batch request is built, not after — the
entire reason for this feature is to avoid spending a Claude call on a
contact whose email will bounce, and `agent.run()`'s Phase 1 loop is the one
place that happens before any API cost is committed:

```
for contact in contacts:
    ...
    action = decide_action(contact, today)
    if action == "skip": ...

    if action in _FIRST_TOUCH_ACTIONS and EMAIL_VERIFY_ENABLED:
        result = email_verify.verify(contact.get("email", ""))
        if result.status == "invalid":
            log + agent_events + skipped += 1; continue

    ... existing prepare_email / batch_requests.append(...) ...
```

Gated to `_FIRST_TOUCH_ACTIONS` only (the same set `agent.py` already uses
for research/voice/critic gating) — by the time a contact is on a follow-up
action, the first-touch email already went out through this same gate (or
the feature was off when it did), so re-checking on every follow-up is pure
overhead for no new information. This mirrors the existing pattern
documented for Voice DNA and Tier-1 critic eligibility: `_FIRST_TOUCH_ACTIONS`
membership already silently gates several things, and this is one more,
deliberately, not by accident.

Only `"invalid"` skips. `"unknown"` and `"valid"` both fall through to the
existing code unchanged. This is intentionally the mirror image of the visa
gate's governance invariant ("a missed match must always degrade to
`NULL`/unknown, never present as a false negative") applied to a blocking
gate instead of a tagging one: here, "unknown" must always degrade to
*allow*, never to a false block.

A skipped contact behaves exactly like an existing `skip` outcome from
`decide_action`: `skipped` is incremented, no batch request is built for it,
and — same as every other skip path in this loop — `update_contact` is never
called, so the contact's stage does not advance. It will be re-evaluated
(and re-checked) on the next run. A permanently-bad address will therefore
log the same skip once per run indefinitely until the user fixes the
address in Supabase; that repeat-noise is accepted for v1, same tradeoff
`ats.py`/`jobright.py` accept for their own best-effort failures — there is
no new table to remember "this address was already flagged," and adding one
would reopen the same stale-claim problem the visa gate's governance
invariant exists to avoid (today's "invalid" might be tomorrow's "valid").

### Logging and events

New marker `[EMAIL-VERIFY]`, existing pipe-separated format:

```
[EMAIL-VERIFY] | {name} | {company} | skip | invalid email: {reason}
```

New `agent_events` row, `event_type="email_verify"`, `status="blocked_invalid_email"`,
`metadata={"email": ..., "reason": ...}` — same shape as the existing
`preflight` event, logged via the existing best-effort `db.log_agent_event`
(never raises, never blocks the skip itself if the DB write fails).

### Config

New constants in `config.py`, beside the `ATS_*` block:

```python
# ── Email verification pre-flight ────────────────────────────────────────────

EMAIL_VERIFY_ENABLED = True
EMAIL_VERIFY_DNS_TIMEOUT_SECONDS = 5
```

`EMAIL_VERIFY_ENABLED` is the independent off-switch for this gate alone,
matching `ATS_ENABLED`'s role for the ATS channel — flipping it off restores
today's behavior byte-for-byte (every contact reaches the batch regardless
of address).

---

## Testing

`tests/test_email_verify.py` — the module in isolation, all DNS resolution
mocked at `dns.resolver.resolve`:
- syntax: valid shapes pass without ever calling the resolver; malformed
  shapes (`no-at-sign`, `@no-local-part`, `trailing@dot.`, empty string,
  `None`) return `"invalid"` without a network call
- MX hit → `"valid"`
- `NoAnswer` on MX + A record resolves → `"valid"`
- `NoAnswer` on MX + A also raises `NXDOMAIN` → `"invalid"`
- `NXDOMAIN` on MX directly → `"invalid"`
- `Timeout` / `NoNameservers` / an unexpected exception → `"unknown"`
- a never-raises parametrized sweep across malformed input and resolver
  exceptions

`tests/test_agent_email_verify.py` — the wiring in `agent.run()`'s Phase 1
loop:
- an `"invalid"` result skips the contact before any batch request is built
  (`_batch_client.messages.batches.create` never called, or called with a
  shorter request list than contacts)
- `"unknown"` and `"valid"` both proceed to batch as today
- the skip increments `skipped`, not `drafted` or `errors`, and never calls
  `update_contact`
- a follow-up action (`send_followup1`, etc.) never calls `email_verify.verify`
  at all
- `EMAIL_VERIFY_ENABLED = False` skips the check entirely, restoring
  pre-feature behavior
- `email_verify.verify` raising (defense in depth, even though the module
  contract says it never does) does not break the run

---

## Rollout

Additive and independently revertible: with `EMAIL_VERIFY_ENABLED = False`
the Phase 1 loop is byte-identical to today. One new dependency
(`dnspython`, pure-Python, no C extension, no new secret), no migration, no
schema change. `monitor.yml` is unaffected — it never imports `agent.py`'s
draft-generation path.

Cost: saves a Claude call (and a wasted draft) on every contact this gate
actually catches; adds one DNS lookup (typically single-digit milliseconds,
capped at `EMAIL_VERIFY_DNS_TIMEOUT_SECONDS`) per first-touch contact per
run.
