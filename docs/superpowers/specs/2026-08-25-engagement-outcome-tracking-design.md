# Engagement & Outcome Tracking — Design

**Status:** Approved for implementation
**Date:** 2026-08-25

Sub-project 3 of 4 from the OSS survey (see
`docs/superpowers/specs/2026-08-19-email-trust-and-voice-design.md`, "Deferred
work"). Two halves, one built, one rejected. Both are resolved by this doc —
the original spec left tracer links as an explicit open concern ("that cost
needs deciding before it is specced"); this doc is that decision.

---

## Part A — Tracer links: rejected, not deferred

**Decision: do not build this.** Recorded here so the open concern in
sub-project 1's spec is closed, not silently dropped.

Reasons, stronger than the one-liner in the original spec:

1. **Every other signal-gathering feature in this repo is additive metadata
   or a prompt input** — Form D, the H-1B gate, ATS enrichment, content_trust,
   Voice DNA. None of them modifies the wire format of an outgoing email.
   Tracer links (redirect-domain links or a tracking pixel) would be the
   first, and the one category of change that cannot be reverted once sent —
   a burned sender reputation does not un-burn, unlike every other feature in
   this codebase which is a pure addition that can be turned off.
2. **The recipients are hiring managers**, not a marketing list. A
   surfaced tracking pixel (Gmail flags these) or a redirect-domain link is a
   reputational risk with the exact audience this project exists to reach,
   independent of the deliverability cost.
3. **No redirect domain or sender-reputation infrastructure exists.**
   Building one is a new external deployment surface (a domain, a redirect
   service, DKIM/SPF considerations for that domain), not a Python change —
   out of proportion to the signal it buys.
4. **The engagement signal this would provide already exists**, cheaper and
   with zero deliverability cost: `contacts.classifier_status` (reply
   detected + classified), `contacts.reply_status`, and
   `draft_history.edit_detected` together answer "did this draft earn a
   reply" — which is the actual question, not "was it opened." Open/click
   tracking is a strictly weaker proxy for the same question, purchased at
   real risk.

No code changes. This section is the artifact.

---

## Part B — Decision-context tagging

### Problem

`draft_history` records that a draft was created and what it said, but not
*which live prompt configuration produced it*. `prompts` rows are edited
live via the contact-manager (Prompts & Profile page) and there is no
version column — `db.load_prompts()` returns a bare `{key: value}` dict, and
`prompts_history` logs changes but nothing links a specific draft back to
the prompt values in effect when it was generated. Today there is no way to
answer "did the prompt rewrite two weeks ago actually change reply rates,"
because nothing ties a draft to which prompt snapshot made it.

Everything else needed for that correlation already exists and does not need
to be duplicated:
- Outcome: `contacts.classifier_status`.
- Research quality: `research_cache.brief_reliable`, keyed by
  `"{name_lower}|{company_lower}"`.
- Critic quality (Tier 1 first-touch only): `agent_events` rows with
  `event_type="critic"`, `metadata.score`.

The one missing fact is prompt-set identity at draft time.

### Design

**Schema.** One nullable, additive column:

```sql
ALTER TABLE draft_history ADD COLUMN IF NOT EXISTS decision_context JSONB;
```

Migration: `supabase/migrations/20260825000000_add_decision_context_to_draft_history.sql`.
Same posture as the Form D migration — additive, no backfill, existing rows
stay `NULL` and a `NULL` `decision_context` means *not instrumented yet*, not
"no context" — reports must render it as unknown, never as zero.

**Content — a JSONB object, not several typed columns**, matching the
`agent_events.metadata` precedent so future signals (e.g. a stored critic
score) can be added without another migration:

```json
{"prompt_hash": "3f9a1c2b7e0d4f6a"}
```

`prompt_hash` is a SHA-256 fingerprint (first 16 hex chars — the same
truncation `gmail.create_draft`'s `X-Cold-Email-Key` already uses) of the
live `prompts` dict actually passed to that draft's generation call,
serialized as `json.dumps(prompts, sort_keys=True, default=str)`. This is a
whole-snapshot fingerprint, not a per-template one — simpler, and sufficient
for "group drafts by which prompt configuration was live," which is the
question this exists to answer. Per-key/per-action versioning was considered
and rejected: `prompts` has no version column to derive it from, and a
derived version would need its own bookkeeping table for a benefit the
whole-snapshot hash already delivers at the granularity the report needs.

**New function** `emailer.hash_prompt_set(prompts)` — pure, no I/O. Public
(no underscore) since two call sites outside `emailer.py` use it.

**Call sites** — both places that already call `log_drafted_email`, both of
which already receive the live `prompts` dict as a parameter, so no new
plumbing is needed through `prepare_email`/`finalize_email`/`generate_email`:
- `agent._execute_draft(contact, action, subject, body, thread_message_id, mode_tag, today, prompts)`
- `reply_drafter.draft_reply(contact, reply_body_text, prompts, ...)`

Each computes `decision_context = {"prompt_hash": hash_prompt_set(prompts)}`
and passes it to `log_drafted_email(..., decision_context=decision_context)`.
`log_drafted_email` gains an optional `decision_context=None` kwarg, included
in the insert row only when not `None` — matching every other optional
param in that function already. Stays best-effort: a failed insert (as
today) logs a warning and never raises, so a `decision_context` bug can never
lose the draft-history row or block a draft.

**No `emailer.py` prompt-assembly changes.** `prepare_email`/`finalize_email`/
`generate_email` signatures and return values are untouched — this feature
observes what's already being passed around, it doesn't change what gets
built. Consequently **no `assembleUserMessage.ts` mirror is needed** (unlike
Voice DNA) — nothing about prompt assembly or injected content changes,
only what gets recorded about it after the fact.

### Report

New script `engagement_report.py`, read-only, following the
`reclassify_unrelated.py` pattern (own `logging.basicConfig`, no cron entry,
run manually). **A raw join, not a stats engine** — the corpus is small (the
Form D live run saw 36 contact companies; total replies across the whole
contact list are in the low single digits at most). Presenting a rate or a
"finding" over n≈5 would be manufacturing false confidence; printing the
joined rows and counts is honest at this sample size.

For each `draft_history` row whose `stage` is a first-touch drafted stage
(`first_touch_drafted`, `applied_intro_drafted`, `networking_drafted` — same
three-stage set `monitor.detect_sent_drafts()` already tracks in parallel,
per the existing manually-synced-set convention documented in CLAUDE.md),
joined to its contact's `name`, `company`, `classifier_status`:
- `decision_context->>'prompt_hash'` (or "unknown" if `NULL`)
- research reliability, looked up from `research_cache` by
  `f"{name.lower()}|{company.lower()}"` ("no research" if no cache row)
- outcome: `classifier_status` (or "no reply yet")

Grouped by `prompt_hash`, printing per-group counts (`n`, replies, reply
rate) **only when `n` is large enough to say anything** — the script prints
the raw per-contact table always, and a per-group rate line only for groups
with `n >= 5`, otherwise it prints the count with an explicit "n too small
for a rate" note rather than a misleading percentage.

### Testing

- `tests/test_decision_context.py` — `hash_prompt_set`: deterministic
  regardless of dict key order, differs when a value changes, handles
  `{}`/`None` without raising.
- `tests/test_db_draft_history.py` — new cases: `decision_context` included
  in the insert payload when passed, omitted (not `NULL`-written) when
  absent — mirrors the existing `gmail_draft_id` omission test.
- `tests/test_agent_logging.py` — extend the existing
  `test_execute_draft_calls_log_drafted_email` /
  `test_reply_drafter_calls_log_drafted_email` assertions to also check
  `kwargs["decision_context"]["prompt_hash"]` is a 16-char hex string.
- `tests/test_engagement_report.py` — mocked Supabase client (pattern from
  `tests/test_visa_intel_db.py`): grouping/join logic, `NULL` renders as
  "unknown" never zero, small-`n` groups suppress the rate line, never raises
  on a malformed/missing `decision_context`.

### Rollout

Purely additive. No behavior change to drafting, prompt assembly, or Gmail
output. Existing `draft_history` rows keep `decision_context = NULL`
("not instrumented") permanently — no backfill is possible since the prompt
snapshot at the time of those historical drafts wasn't captured. The report
only becomes informative going forward from this migration.
