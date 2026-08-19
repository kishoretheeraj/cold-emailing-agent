# Email Trust & Voice — Design

**Status:** Draft for review
**Date:** 2026-08-19

Two changes to how generated email copy is produced and how untrusted external
text is handled. Sub-project 1 of 4 (see "Deferred work" at the end).

**Goal:** (a) Treat scraped/received text as data-never-instructions with an
auditable anomaly signal, and (b) make generated copy sound like Kishore rather
than like a model imitating a generic professional.

**Ordering rationale:** The guardrail ships first because sub-project 3 adds
four new external content sources feeding Claude prompts. Hardening before
widening, not after.

---

## Provenance

Ideas inherited from an OSS survey (7 repos, 3 targeted searches):
- Untrusted-content rule + anomaly logging — [santifer/career-ops](https://github.com/santifer/career-ops)
- Voice DNA writing-style extraction — [santifer/career-ops](https://github.com/santifer/career-ops)

Explicitly rejected during that survey and **not** part of any sub-project:
cookie-based LinkedIn/Twitter/Reddit scraping (LinkedIn sued Proxycurl into
shutdown, July 2025), and paid enrichment APIs re-selling the same scraped data
under an "API" label (HarvestAPI, ScrapingDog, CUFinder, Clay, People Data Labs).

---

## Part A — Untrusted external content guardrail

### Problem

`research.py` (Tavily results) and `reply_drafter.py` (inbound reply bodies)
both feed attacker-influenceable text straight into Claude prompts. A web page
or an inbound email containing `Ignore previous instructions and ...` is
currently indistinguishable, to the model, from legitimate context. There is no
detection, no logging, and no documented boundary.

### Why this is not a 7th `preflight.py` check

Verified against the code, both reasons independently disqualify it:

1. **Wrong data.** `preflight.check(body, contact, prompts)` is called at
   `emailer.py:257` with the *generated outgoing body*. By then the research
   brief has already been consumed by Claude and is not in scope. A check there
   structurally cannot see the text we need to inspect.
2. **Wrong contract.** A non-empty `preflight.check` return means "one
   regeneration, then hard-block and raise `ValueError`"
   (`emailer.py:264-270`). That is the opposite of the flag-don't-block
   behaviour this needs — a suspicious research brief should still produce a
   draft, just an annotated one.

So detection sits on the **inputs**, in a new module, not on the output.

### Design

New module `content_trust.py` — pure functions, no I/O, no Claude call, so it
is cheap and trivially testable:

```
scan(text) -> list of matched pattern labels (empty list = clean)
```

Pattern classes (substring/regex, case-insensitive): instruction-override
phrases (`ignore previous/above instructions`, `disregard your instructions`),
role-injection markers (a line starting `system:` / `assistant:` /
`<|im_start|>`), and exfiltration/imperative-at-the-model phrasing
(`send an email to`, `forward this to`, `reveal your prompt`).

Two call sites, both **flag-only, never block**:

- `research.py::get_research_brief` — scan `brief_text` after curation, before
  it is cached and returned. Log `[RESEARCH-X]`; record on the existing
  `research` `agent_events` row via a `trust_flags` metadata key.
- `reply_drafter.py` — scan the inbound reply body before classification.
  Log; record on the existing event with the same metadata key.

Detected text is **not** stripped or rewritten. Stripping teaches nothing and
silently changes the model's input; flagging preserves the evidence and makes
the anomaly reviewable. The brief is still used.

Docs: a short "Untrusted external content" section added to
`docs/python/research-pipeline.md` and `docs/python/reply-pipeline.md` stating
the rule — external text can influence *content*, never *control flow*, and can
never trigger a send, a stage change, or a rule override.

### Testing

`tests/test_content_trust.py` — parametrized over each pattern class (positive
and negative cases), plus a false-positive guard: legitimate business copy that
happens to contain `forward this to your team` must not flag. Call-site tests
assert flag-not-block: a research brief containing an injection string still
returns the brief and still produces a draft.

---

## Part B — Voice DNA

### Problem

`sender_profile` is a hand-written description of who Kishore is. Nothing
describes how he actually *writes*. The prompt templates carry negative
constraints (no em dashes, forbidden phrases) but no positive style signal, so
copy regresses toward generic LLM register.

### Design

New script `extract_voice.py`, run manually / occasionally — **not** in the
daily cron:

1. Fetch the N most recent sent messages from `[Gmail]/Sent Mail`.
2. Filter to human-written prose: drop anything carrying the
   `X-Cold-Email-Key` header (the agent's own drafts — never train on our own
   output, that compounds model voice instead of correcting it), drop
   automated/notification senders, drop very short bodies.
3. One Claude call extracts a `## Writing Style` block: sentence-length rhythm,
   greeting/sign-off habits, contraction and hedging frequency, characteristic
   vocabulary, punctuation habits.
4. Write the result to the `prompts` table as a new `voice_dna` row.

**New IMAP code is required.** `gmail.py` today exposes only *targeted*
lookups — `find_sent_by_subject`, `find_sent_by_thread_id`,
`find_sent_for_thread`. "List the N most recent sent messages" does not exist
and will be added as `fetch_recent_sent(limit, since_date)`, following the
existing mailbox-quoting and `_fetch_body_text` conventions in
`docs/python/imap.md`.

### Injection, and the mirroring requirement

`voice_dna` is injected into the first-touch prompt assembly alongside
`sender_profile`, and applies to conversational outreach copy only — not to
subject generation and not to the critic rubric.

**It must be mirrored in TypeScript.** `contact-manager/src/lib/assembleUserMessage.ts`
re-implements `emailer.py`'s prompt assembly so the Prompt Lab can preview what
the agent will actually send; it already reads `sender_profile` (lines 351, 436,
449). If `voice_dna` is injected Python-side only, the Lab's preview silently
diverges from production and the Lab stops being trustworthy. Both sides change
together, in the same PR.

**Precedence:** an explicit style instruction written into a prompt template
always wins over the extracted `voice_dna` default. Voice DNA is a default, not
an override.

**Existing rules are unchanged and still win.** The em-dash ban and
`forbidden_phrases` are hard constraints; if extraction observes Kishore using
em dashes in real mail, the ban still applies to generated copy.

### Testing

`tests/test_extract_voice.py` — mocked IMAP and mocked Claude (never travels,
per the repo's test rules). Cases: agent-authored mail is excluded from the
sample; too few samples degrades to a no-op rather than writing a junk
`voice_dna` row; a failed Claude call leaves the existing row untouched.
`assembleUserMessage.test.ts` gets a case asserting the TS assembly includes
`voice_dna` when present and omits it cleanly when absent — this is the test
that catches Python/TS drift.

---

## Rollout

Both parts are additive and independently revertible. Part A changes no
generated copy (flag-only). Part B changes copy only after `extract_voice.py`
is run for the first time and a `voice_dna` row exists — until then, prompt
assembly is byte-identical to today. No migration is required for Part B
(`prompts` is a key/value table; this is an INSERT).

---

## Deferred work

Verified during research, specced separately — this doc covers sub-project 1
only.

**2 — Research pipeline enrichment.** ATS career-page JSON as a new research
channel. Verified live: naive lowercase-company-name slugs hit
`boards-api.greenhouse.io` for 5 of 7 sampled companies, and Ashby covered the
other 2, so a Greenhouse→Ashby→Lever cascade reached 7/7 on the sample;
`?content=true` returns full job-description text (~4.6k chars) with no auth
and a clean 404 on miss. Plus Trafilatura as a rate-limit-free companion to
Jina Reader, GitHub API for technical contacts, SEC EDGAR for company context.
Ships after Part A.

**3 — Engagement & outcome tracking.** Decision-context tagging (record which
prompt version and research brief produced each draft, correlate against
`classifier_status` to learn what actually earns replies) and tracer links.
**Open concern on tracer links:** routing links through a redirect domain is a
known deliverability negative and this project has no sender-reputation
infrastructure. That cost needs deciding before it is specced, not after.

**4 — Sourcing intelligence.** SEC DERA Form D quarterly funding ingestion,
shaped like `ingest_oflc_lca.py` and matched via the existing
`entity_resolution.py`, writing funding columns onto `company_intel`. Same
governance invariant as the visa gate: no match degrades to unknown/NULL, never
to a false claim.

Also surveyed and deliberately dropped: persona-roleplay critic (folded into
the existing critic loop later if the Tier-1 rubric proves insufficient) and
content-hash approval invalidation (spans Python and the Next.js
`/api/send-draft` route, and "approval" is undefined for non-Tier-1 drafts
since the critic only runs there — needs its own scoping pass).
