# ATS Career-Page Enrichment — Design

**Status:** Draft for review
**Date:** 2026-08-19

A new research channel: read a target company's public applicant-tracking-system
(ATS) job board and feed the active postings into the existing research brief.
Sub-project 2 of 4 (see "Deferred work" in
`docs/superpowers/specs/2026-08-19-email-trust-and-voice-design.md`).

**Goal:** Give the email writer a fact it currently cannot get from Tavily — that
this specific company is hiring *right now* in the contact's own function, and
what that req actually says — without adding an API key, a vendor, or a scraper.

**Ordering rationale:** Sub-project 1 shipped the untrusted-content guardrail
first precisely so that new external content sources could be added behind it.
This is the first of those sources. `content_trust.scan` is a prerequisite, not
an afterthought.

---

## Provenance

Inherited from the same OSS survey as sub-project 1. The mechanism (public ATS
JSON endpoints, no auth) was verified live by a prior session against 7 sampled
companies:

| Provider | Endpoint | Result on the sample |
|---|---|---|
| Greenhouse | `https://boards-api.greenhouse.io/v1/boards/{slug}/jobs` | 5/7 (stripe, airbnb, databricks, anthropic, figma) |
| Ashby | `https://api.ashbyhq.com/posting-api/job-board/{slug}` | covered notion and ramp |
| Lever | `https://api.lever.co/v0/postings/{slug}?mode=json` | third rung, not needed on this sample |

A Greenhouse → Ashby → Lever cascade reached 7/7. Slugs were the naive
lowercased company name. `?content=true` on the Greenhouse endpoint returns full
job-description text (~4.6k chars/posting). No auth, no key, clean `404` on a
miss.

This is **not** re-verified in code. Tests mock every HTTP call, per the repo's
rule that tests never travel.

Explicitly still rejected, unchanged from sub-project 1: cookie-based LinkedIn /
Twitter scraping and paid enrichment APIs re-selling scraped data. A company's
own careers page, published as JSON by the company's own vendor for exactly this
purpose, is a different category.

---

## Problem

`research.get_research_brief` has one channel: Tavily web search. Tavily is good
at "what has been written *about* this person or company" and bad at "what is
this company doing *today*". Hiring signal is the single most useful fact for a
job-seeker's cold email and it is precisely the fact Tavily returns worst:
search results about hiring are stale press coverage, aggregator spam, or job
boards that have already delisted the req.

The company's own ATS is the authoritative, current, free source for that fact.

## What the channel adds

Two things, in order of value:

1. **Confirmation of an open req in the contact's function.** "You are hiring
   two backend engineers on the payments team" is checkable, current, and
   directly relevant to why the email is being sent.
2. **Real job-description text.** Requirements, team names, and stack details
   written by the company, usable for a specific hook.

## Non-goals

- **Not a job-search feature.** Nothing surfaces reqs to the user, ranks them,
  or writes them to `contacts`. The output is prompt context and nothing else.
- **Not a company-intel table.** No new Supabase table, no new column. Unlike
  the visa gate (sub-project 4's shape), the ATS result is *volatile* — a req
  open today is closed next month — so persisting it as a company attribute
  would create exactly the stale-false-claim problem the visa gate's governance
  invariant exists to prevent. It lives in the 7-day `research_cache` blob and
  nowhere else.
- **No auto-send.** Unchanged: this system only creates drafts.

---

## Design

### New module `ats.py`

Self-contained, in the shape of `content_trust.py`: no `db`, no `emailer`, no
`gmail` import, so it is trivially testable and can never drag a Supabase or
Anthropic failure into the research pipeline. Its only outside dependency is
`urllib.request`, matching `ingest_oflc_lca.py` / `ingest_uscis_datahub.py` —
no new entry in `requirements.txt`.

Public surface:

```
fetch_jobs(company, role=None) -> list of normalized job dicts ([] on anything)
```

Normalized job dict:

```
{"title": str, "location": str, "url": str, "description": str, "source": str}
```

`source` is one of `"greenhouse"`, `"ashby"`, `"lever"`.

**Cascade, not fan-out.** Providers are tried in order and the *first* one that
returns a non-empty job list wins. A company lives on exactly one ATS; querying
the other two after a hit is wasted latency in a pipeline that already runs
inside a per-contact loop.

**Slug derivation.** Lowercase, strip a trailing corporate suffix (`inc`, `llc`,
`ltd`, `corp`, `co`, `plc`, `gmbh`), then drop every non-alphanumeric character.
`"Acme Corp, Inc."` → `"acme"`. A second hyphenated candidate (`"acme-corp"`) is
tried when the joined form differs from it, capped at two candidates per
provider so worst-case is 6 requests, not an unbounded sweep.

This is deliberately *not* `entity_resolution.normalize()`. That function exists
to make two human-written company strings fuzzy-comparable and replaces
punctuation with spaces to keep alias groups reachable; a URL slug needs the
opposite treatment. Coupling them would mean a slug tweak silently reshapes visa
entity matching.

**Relevance filtering.** When `role` is given, postings are scored by token
overlap between the contact's role and the job title, ties broken by original
order, and the top `ATS_MAX_JOBS` (3) are kept. With no role, the first 3
postings are kept. Descriptions are truncated to `ATS_MAX_DESCRIPTION_CHARS`
(1500); the observed ~4.6k chars per posting is mostly boilerplate benefits copy
and three of them unabridged would triple the curation input for no gain.

**HTML.** Greenhouse returns HTML-escaped HTML in `content`. `_strip_html`
unescapes, drops tags, and collapses whitespace. Ashby and Lever both expose a
plain-text description field; the HTML field is used as fallback.

**Never raises.** Every provider call is individually wrapped; `fetch_jobs`
wraps the whole cascade. A timeout, a 404, a 500, malformed JSON, an unexpected
payload shape, or a DNS failure all produce `[]`. This matches the Tavily path
(`_run_tavily` swallows per-query failures) and the visa gate's
`continue-on-error` posture: enrichment is never allowed to cost a draft.

### Wiring into `research.py`

A new `_run_ats(contact)` helper sits beside `_run_tavily` and
`_run_hardcoded_fallback`, and is called from `get_research_brief` after the
Tavily/fallback stage and before curation.

```
queries → tavily → (fallback) → ats → trust scan → curate → cache
```

`_curate_brief` grows an `ats_jobs=None` keyword. The postings are rendered into
a clearly labelled `ACTIVE JOB POSTINGS` section appended to the text that fills
the existing `{raw_results}` placeholder. The `research_curate_prompt` row in
Supabase is **not** changed — the curator already knows to synthesize whatever
it is given and to discard anything that contradicts the contact record, and
changing a live prompt row is a bigger blast radius than this feature needs.

`_curate_brief`'s early return becomes "no Tavily results *and* no ATS jobs",
so an ATS-only hit still produces a brief.

**Truncation.** The existing 6000-char cap keeps applying to the Tavily portion
*only*, and the ATS section is appended after that truncation, separately
bounded by `ATS_MAX_JOBS × ATS_MAX_DESCRIPTION_CHARS`. Folding both into one cap
would mean a long Tavily haul silently deletes the hiring signal, and would
change the curation input on contacts that get no ATS hit at all. With this
split, a no-ATS contact's curation input is byte-identical to today.

**Gating is unchanged.** `get_research_brief` still returns `""` immediately
when `TAVILY_API_KEY` is unset. That is a deliberate choice, not an oversight:
the key gates the whole *research feature*, and running an ATS-only pipeline in
a deployment that has switched research off would be a surprise. The channel is
additive inside research, not a second way to turn research on. `ATS_ENABLED` in
`config.py` is the independent off-switch for this channel alone.

### Untrusted content

Job-description text is written by whoever has req-editing rights at the target
company. It is exactly as attacker-influenceable as a Tavily result, and it
reaches a Claude prompt — the curation call — one step *earlier* than the
curated brief the existing scan covers.

So the ATS text is scanned **before it enters the curation prompt**:

- `content_trust.scan` over the concatenated titles and descriptions, in
  `get_research_brief`, immediately after `_run_ats` returns.
- Matches log `[RESEARCH-X]` with an `ats` qualifier, mirroring the existing
  brief scan.
- Matches land on the existing `research` `agent_events` row under a separate
  `ats_trust_flags` metadata key.

`ats_trust_flags` is deliberately a *distinct* key from the existing
`trust_flags`, not a merged list: the two flags mean different things when you
are reading the event later. `trust_flags` says the curated brief carries
injection patterns; `ats_trust_flags` says a job posting did. Merging them
destroys the provenance that makes the flag worth recording.

**Flag-only, unchanged.** The postings are still used, still curated, still
injected. Nothing here blocks a draft or rewrites the text — same reasoning as
sub-project 1: stripping destroys the evidence and silently changes the model's
input. A scanner failure degrades to "clean".

### Config

New constants in `config.py`, beside the `RESEARCH_*` block:

```
ATS_ENABLED = True
ATS_MAX_JOBS = 3
ATS_MAX_DESCRIPTION_CHARS = 1500
ATS_TIMEOUT_SECONDS = 8
ATS_MAX_SLUG_CANDIDATES = 2
```

`ATS_TIMEOUT_SECONDS = 8` is short on purpose: this runs inside the per-contact
loop of a cron job, and a hung careers API must not stall a run. Worst case with
three providers × two candidates is bounded by 6 × 8s, and only on a company
that exists on none of them.

### Logging

One new marker, `[RESEARCH-A]`, following the existing pipe-separated format:

```
[RESEARCH-A] | {name} | {company} | source=greenhouse | slug=acme | jobs=3
[RESEARCH-A] | {name} | {company} | no_ats_match | candidates=2
```

`agent_events` metadata for the `research` event gains `ats_jobs` (count) and,
when non-empty, `ats_trust_flags`.

### Caching

The postings ride along in the existing `research_cache` blob
(`brief_json.ats_jobs`) and inherit the 7-day TTL. On a cache hit the whole
pipeline is skipped, ATS included — no extra HTTP on a warm contact. A 7-day-old
"they are hiring" claim is acceptable staleness for a cold email; a shorter TTL
just for this channel would mean two cache lifetimes in one blob, which is not
worth the complexity.

---

## Testing

`tests/test_ats.py` — the module in isolation, all HTTP mocked at
`ats._http_get_json`:
- slug derivation (suffix stripping, punctuation, multi-word, hyphenated
  candidate, empty/garbage input)
- provider cascade: Greenhouse hit short-circuits; Greenhouse 404 falls to
  Ashby; both miss falls to Lever; all miss returns `[]`
- per-provider payload parsing into the normalized shape, including Ashby's
  `descriptionPlain` vs `descriptionHtml` fallback
- `_strip_html` on escaped Greenhouse content
- relevance ranking and the `ATS_MAX_JOBS` cap
- description truncation
- a parametrized never-raises sweep: timeout, `HTTPError`, `URLError`, invalid
  JSON, `None` payload, wrong-typed payload, missing keys — all return `[]`

`tests/test_research_ats.py` — the wiring:
- an ATS hit reaches the curation input
- an ATS-only hit (zero Tavily results) still produces a brief
- injection text inside a job description is flagged as `ats_trust_flags` and
  the brief is still returned (flag-not-block)
- `ats_trust_flags` and `trust_flags` stay distinct keys
- `ats.fetch_jobs` raising does not break `get_research_brief`
- `ATS_ENABLED = False` skips the channel entirely
- a cache hit does not call `fetch_jobs`

---

## Rollout

Additive and independently revertible: with `ATS_ENABLED = False` the curation
input is byte-identical to today. No migration, no schema change, no new
dependency, no new secret. The daily workflow needs no edit — `agent.py` already
imports `research`, and `monitor.yml` deliberately does not.

Cost: zero marginal API spend. The postings add up to ~4.5k chars to a curation
call already capped at 6000 chars of input, so Anthropic spend is unchanged in
practice.

---

## Deferred, still

Unchanged from sub-project 1's list. Sub-project 3's **tracer links remain
blocked** on the deliverability question — routing links through a redirect
domain is a known negative and this project has no sender-reputation
infrastructure. That decision is Kishore's and is not made here.
