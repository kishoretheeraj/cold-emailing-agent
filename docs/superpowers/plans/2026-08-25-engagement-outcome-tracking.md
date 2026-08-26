# Engagement & Outcome Tracking Implementation Plan (Part B — Decision-Context Tagging)

**Goal:** Record *which live prompt configuration produced each draft* so a prompt rewrite can later be correlated with reply outcomes, and ship a read-only report that joins that fingerprint to the outcome signals that already exist (`contacts.classifier_status`, `research_cache.brief_reliable`).

**Architecture:** One additive nullable `draft_history.decision_context JSONB` column. One new pure function `emailer.hash_prompt_set(prompts)` (SHA-256, first 16 hex chars — the same truncation `gmail.create_draft` already uses for `X-Cold-Email-Key`). Both existing `log_drafted_email` call sites (`agent._execute_draft`, `reply_drafter.draft_reply`) already receive the live `prompts` dict, so nothing new is plumbed through `prepare_email`/`finalize_email`/`generate_email`. A new manual `engagement_report.py` (shaped like `reclassify_unrelated.py`) reads two new `db.py` accessors and joins in Python.

**Tech Stack:** Python 3.11 (plain, no type annotations), stdlib `hashlib` + `json`, Supabase (`draft_history`, `contacts`, `research_cache`), pytest + pytest-mock. No new dependency, no new secret, no cron entry, **no TypeScript change**.

**Spec:** `docs/superpowers/specs/2026-08-25-engagement-outcome-tracking-design.md`

**Part A (tracer links) is resolved in the spec as "rejected, do not build." There is no code, test, doc, or migration for Part A in this plan.**

## Global Constraints

- **No type annotations.** Plain Python. No `typing` imports.
- **No docstrings on `_`-prefixed helpers.** Public functions get one short docstring.
- **Section banners:** `# ── Section name ─────...` (16+ box-drawing chars).
- **Log format:** `f"{marker} | {name} | {company} | event | extra"`, pipe-separated.
- **All outbound calls mocked in tests.** Tests never travel. Every Supabase call in this plan is mocked at `db.get_client`.
- **Best-effort rule:** `decision_context` must never cost a draft. `log_drafted_email` stays best-effort (warns, never raises), and the hash computation is wrapped at both call sites so a fingerprint bug can never lose the `draft_history` row or abort a draft. This matters most in `reply_drafter.draft_reply`, where the hash line sits inside the big `try:` — an unwrapped raise there would land in the `except` block *after* the Gmail draft already exists, marking a successful draft as `failed`.
- **`engagement_report.py` is read-only.** It must never call `update_contact`, `log_drafted_email`, `upsert_*`, or any writer. No cron entry, no workflow step.
- **No auto-send path.** Unchanged: this system only creates drafts.

---

## File Structure

**Create:**
- `supabase/migrations/20260825000000_add_decision_context_to_draft_history.sql`
- `engagement_report.py` — manual read-only report. Own `logging.basicConfig` before project imports.
- `tests/test_decision_context.py` — `hash_prompt_set` unit tests.
- `tests/test_engagement_report.py` — mocked-Supabase accessor + join/grouping/rendering tests.

**Modify:**
- `emailer.py` — `import hashlib`; new `hash_prompt_set` section.
- `db.py` — `log_drafted_email(..., decision_context=None)`; two new read accessors.
- `agent.py` — import `hash_prompt_set`; wire `decision_context` in `_execute_draft`.
- `reply_drafter.py` — import `hash_prompt_set`; wire `decision_context` in `draft_reply`.
- `tests/test_db_draft_history.py` — new cases (extend, do not duplicate).
- `tests/test_agent_logging.py` — extend two existing assertions (extend, do not duplicate).
- `CLAUDE.md` — module layout entry, new `## Decision-context tagging` section, amend the `_FIRST_TOUCH_ACTIONS` bullet, two test-list entries.
- `docs/python/db-schema.md` — new `draft_history` column bullet, one amended + two new `db.py` function bullets.
- Memory: new `project-decision-context-tagging.md` + `MEMORY.md` index line.

---

## Task 1: Migration — `draft_history.decision_context`

- [x] Create `supabase/migrations/20260825000000_add_decision_context_to_draft_history.sql`:

```sql
-- decision_context: which live prompt configuration produced this draft.
--
-- Sub-project 3, Part B. One nullable JSONB column. Additive, no backfill,
-- no default, no index. Existing rows are unaffected and stay NULL forever --
-- the prompt snapshot behind a historical draft was never captured, so no
-- backfill is possible.
--
-- Governance, same posture as company_intel's funding columns: NULL means NOT
-- INSTRUMENTED, never "no context" and never zero. engagement_report.py renders
-- NULL as "unknown".
--
-- Shape today: {"prompt_hash": "3f9a1c2b7e0d4f6a"} -- SHA-256 of the live
-- prompts dict passed to that draft's generation call, first 16 hex chars.
-- JSONB rather than a typed column so a future signal (e.g. a stored critic
-- score) can be added without another migration -- the agent_events.metadata
-- precedent.

ALTER TABLE draft_history
  ADD COLUMN IF NOT EXISTS decision_context JSONB;

COMMENT ON COLUMN draft_history.decision_context IS
  'Prompt-set fingerprint in effect when this draft was generated: {"prompt_hash": "<16 hex>"}. NULL = not instrumented, never "no context".';
```

- [ ] Apply via `supabase db push`.
- [ ] Commit.

---

## Task 2: `emailer.hash_prompt_set`

Add `import hashlib` beside `import json` in `emailer.py`. Insert after the `_MODE_TAGS` dict, before `def _is_dartmouth(contact):`:

```python
# ── Decision-context fingerprint ───────────────────────────────────────────────

def hash_prompt_set(prompts):
    """
    Fingerprint the live prompts dict that produced a draft: SHA-256 of its
    sorted JSON serialization, first 16 hex chars (same truncation gmail.py
    uses for X-Cold-Email-Key). Pure, no I/O.

    Whole-snapshot, not per-template: the question this answers is "which prompt
    configuration was live," and the prompts table has no version column to
    derive anything finer from.
    """
    payload = json.dumps(prompts or {}, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]
```

Public (no underscore) since two call sites outside `emailer.py` use it.

Test file `tests/test_decision_context.py` — cases: 16-char lowercase hex shape; deterministic for the same dict; order-independent; changes when a value changes; changes when a key is added; handles `{}` and `None` (and both hash identically); handles unserializable values (`default=str`); does not mutate its input.

---

## Task 3: `db.log_drafted_email(..., decision_context=None)`

Change signature (`db.py` ~line 305):

```python
def log_drafted_email(contact_id, stage, subject, body,
                      message_id=None, gmail_draft_id=None, decision_context=None):
```

After the existing `gmail_draft_id` stanza, before the `try:`:

```python
    if decision_context is not None:
        row["decision_context"] = decision_context
```

Append two cases to `tests/test_db_draft_history.py` using the existing `fake_client` fixture and payload-assertion style: `decision_context` present in the insert payload when passed; absent (not `NULL`-written) when omitted.

---

## Task 4: Wire into `agent._execute_draft`

Extend the `emailer` import: `from emailer import generate_email, prepare_email, finalize_email, hash_prompt_set`.

Immediately before the `log_drafted_email(` call in `_execute_draft`:

```python
    # Decision-context tagging: which live prompt set produced this draft.
    # Wrapped because a fingerprint bug must never cost a draft_history row.
    try:
        decision_context = {"prompt_hash": hash_prompt_set(prompts)}
    except Exception as exc:
        decision_context = None
        log.warning(f"{mode_tag} {name} | {company} | decision_context skipped: {exc}")

    log_drafted_email(
        contact["id"], current_stage, subject, body,
        message_id=message_id, gmail_draft_id=gmail_draft_id,
        decision_context=decision_context,
    )
```

Extend the **existing** `test_execute_draft_calls_log_drafted_email` in `tests/test_agent_logging.py` with a shape assertion (`re.match(r"^[0-9a-f]{16}$", kwargs["decision_context"]["prompt_hash"])`) — this test drives `agent.run()`, whose `prompts` dict is environment-dependent (falls back to `{}` on load failure), so only the shape is stable here.

---

## Task 5: Wire into `reply_drafter.draft_reply`

Extend the `emailer` import: `from emailer import _call_claude, _normalize_body, hash_prompt_set`.

Immediately before the `log_drafted_email(` call in `draft_reply`:

```python
        # Decision-context tagging. Wrapped: an unwrapped raise here lands in
        # the outer except AFTER the Gmail draft already exists, which would
        # log a real draft as failed.
        try:
            decision_context = {"prompt_hash": hash_prompt_set(prompts)}
        except Exception as exc:
            decision_context = None
            log.warning(f"[REPLY-DRAFT] | {name} | {company} | decision_context skipped: {exc}")

        log_drafted_email(
            contact_id, "reply_drafted", subject, body,
            message_id=message_id, gmail_draft_id=gmail_draft_id,
            decision_context=decision_context,
        )
```

Extend the **existing** `test_reply_drafter_calls_log_drafted_email` — that test passes `prompts={}` explicitly, so assert the exact value: `kwargs["decision_context"]["prompt_hash"] == emailer.hash_prompt_set({})`.

---

## Task 6: `engagement_report.py` + two `db.py` accessors

**Design decisions (pinned, not to be re-litigated):**
1. "A reply" means `classifier_status IS NOT NULL`.
2. `n` = distinct contacts, not draft rows — most recent first-touch draft per contact wins (rows ordered `drafted_at DESC`, keep first seen per `contact_id`).
3. Stage set inlined as `_FIRST_TOUCH_DRAFTED_STAGES = ("first_touch_drafted", "applied_intro_drafted", "networking_drafted")` — a fourth manually-synced copy alongside `agent._FIRST_TOUCH_ACTIONS`, `emailer._FIRST_TOUCH_ACTIONS`, and monitor's stage-level set.
4. Research cache key comes from `research._cache_key(name, company)` (imported, not re-derived — the real implementation `.strip()`s before lowercasing, so a re-derived copy would silently miss whitespace-carrying names).
5. New whole-table accessor `get_research_reliability_map()` — `get_research_cache(key)` doesn't select `brief_reliable`.
6. Join happens in Python via the existing `contact_id` FK and `get_all_contacts()`. No PostgREST embed.
7. No `.range()` pagination yet — corpus is in the low hundreds; add it like `get_employer_h1b_stats_corpus()` if it ever crosses ~1000 rows.
8. Output marker `[ENGAGEMENT]`, stdout only — no `.log` file, so it's not added to CLAUDE.md's log-marker list.

Append to `db.py` (draft_history section, after `log_drafted_email`):

```python
def get_draft_history_by_stages(stages):
    """
    Fetch draft_history rows whose stage is in `stages`, newest first.
    Raises on failure -- an empty report and a failed read must not look alike.
    """
    result = _retry(lambda: (
        get_client()
        .table("draft_history")
        .select("contact_id, stage, decision_context, drafted_at")
        .in_("stage", list(stages))
        .order("drafted_at", desc=True)
        .execute()
    ))
    return result.data or []
```

And beside the `research_cache` helpers:

```python
def get_research_reliability_map():
    """
    Return {cache_key: brief_reliable} for every research_cache row.
    Read once for reporting; get_research_cache() is per-key and does not
    select brief_reliable. Raises on failure.
    """
    result = _retry(lambda: (
        get_client()
        .table("research_cache")
        .select("cache_key, brief_reliable")
        .execute()
    ))
    return {r["cache_key"]: r.get("brief_reliable")
            for r in (result.data or []) if r.get("cache_key")}
```

New `engagement_report.py` (own `logging.basicConfig` before project imports, stdout handler only, no log file):

```python
"""
Read-only engagement report: which live prompt configuration produced each
first-touch draft, and what outcome that contact reached.

Joins draft_history.decision_context (the prompt-set fingerprint written by
agent._execute_draft and reply_drafter.draft_reply) to the signals that already
exist: contacts.classifier_status for the outcome and research_cache.brief_reliable
for research quality.

"Replied" means classifier_status IS NOT NULL -- the monitor sets it only after a
reply was detected and classified. 'unrelated' still counts as a reply arriving;
the per-contact table prints the exact status so the reader can judge quality.

A raw join, not a stats engine. The corpus is small, so a per-group reply rate is
printed only when n >= MIN_GROUP_N; below that the count is printed with an
explicit note. A NULL decision_context means NOT INSTRUMENTED (the draft predates
this feature) and renders as "unknown" -- never as zero, never blank.

Never writes. Not in cron.

Run: python3 engagement_report.py
"""

import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)

from db import (get_all_contacts, get_draft_history_by_stages,
                get_research_reliability_map)
from research import _cache_key

_FIRST_TOUCH_DRAFTED_STAGES = (
    "first_touch_drafted", "applied_intro_drafted", "networking_drafted",
)

MIN_GROUP_N = 5
UNKNOWN = "unknown"


def _prompt_hash(row):
    ctx = row.get("decision_context")
    if not isinstance(ctx, dict):
        return UNKNOWN
    value = ctx.get("prompt_hash")
    return value if isinstance(value, str) and value else UNKNOWN


def _research_label(reliable):
    if reliable is True:
        return "reliable"
    if reliable is False:
        return "unreliable"
    return "no research"


def build_rows(draft_rows, contacts, reliability):
    """
    Join draft rows to contacts, one row per contact (most recent draft wins).
    Never raises: a malformed row is skipped with a warning.
    """
    rows = []
    try:
        by_id = {c.get("id"): c for c in contacts if isinstance(c, dict)}
        seen = set()
        for draft in draft_rows or []:
            try:
                if not isinstance(draft, dict):
                    continue
                cid = draft.get("contact_id")
                if cid is None or cid in seen:
                    continue
                contact = by_id.get(cid)
                if contact is None:
                    continue
                seen.add(cid)
                name = (contact.get("name") or "").strip() or "Unknown"
                company = (contact.get("company") or "").strip() or "Unknown"
                status = contact.get("classifier_status")
                rows.append({
                    "name": name,
                    "company": company,
                    "prompt_hash": _prompt_hash(draft),
                    "research": _research_label(
                        reliability.get(_cache_key(name, company))),
                    "outcome": status or "no reply yet",
                    "replied": status is not None,
                })
            except Exception as exc:
                log.warning(f"[ENGAGEMENT] | skipping malformed draft row: {exc}")
    except Exception as exc:
        log.warning(f"[ENGAGEMENT] | build_rows failed: {exc}")
    return rows


def group_counts(rows):
    """Return {prompt_hash: {"n": distinct contacts, "replies": N}}."""
    groups = {}
    for row in rows:
        g = groups.setdefault(row["prompt_hash"], {"n": 0, "replies": 0})
        g["n"] += 1
        if row["replied"]:
            g["replies"] += 1
    return groups


def render(rows):
    """Print the raw per-contact table, then per-prompt_hash counts."""
    log.info(f"[ENGAGEMENT] | START | {len(rows)} first-touch contact(s)")
    for row in sorted(rows, key=lambda r: (r["prompt_hash"], r["name"])):
        log.info(
            f"[ENGAGEMENT] | {row['name']} | {row['company']} | "
            f"prompt_hash={row['prompt_hash']} | research={row['research']} | "
            f"outcome={row['outcome']}"
        )

    groups = group_counts(rows)
    for prompt_hash in sorted(groups):
        g = groups[prompt_hash]
        line = (f"[ENGAGEMENT] | prompt_hash={prompt_hash} | "
                f"n={g['n']} | replies={g['replies']}")
        if g["n"] >= MIN_GROUP_N:
            rate = 100.0 * g["replies"] / g["n"]
            log.info(f"{line} | reply_rate={rate:.1f}%")
        else:
            log.info(f"{line} | n too small for a rate")
    log.info(f"[ENGAGEMENT] | DONE | {len(groups)} prompt_hash group(s)")


def main():
    try:
        draft_rows = get_draft_history_by_stages(_FIRST_TOUCH_DRAFTED_STAGES)
        contacts = get_all_contacts()
        reliability = get_research_reliability_map()
    except Exception as exc:
        log.warning(f"[ENGAGEMENT] | report aborted, read failed: {exc}")
        return
    render(build_rows(draft_rows, contacts, reliability))


if __name__ == "__main__":
    main()
```

`tests/test_engagement_report.py` covers: `get_draft_history_by_stages` filters/orders/raises-on-error; `get_research_reliability_map` keys correctly and raises on error; `build_rows` joins contact fields, strips/lowercases for the cache key, renders "no research"/"no reply yet" defaults, keeps only the most recent draft per contact, skips a draft with no matching contact; `group_counts` counts distinct contacts not draft rows; NULL/malformed `decision_context` (None, `{}`, wrong types, missing key) all render `"unknown"`, and an unknown group still reports its real counts; small-`n` suppression at the `MIN_GROUP_N=5` boundary (below prints "n too small for a rate", at/above prints the rate); the raw table still prints even when every group is small; `build_rows` never raises on malformed input (missing keys, `None` rows, wrong types); `main()` never raises when Supabase is down and never calls any writer (`log_drafted_email`, `update_contact`).

---

## Task 7: Documentation and memory

1. `CLAUDE.md` module layout — add `engagement_report.py` after `extract_voice.py`.
2. `CLAUDE.md` — amend the `_FIRST_TOUCH_ACTIONS` bullet in "Threading invariants" to note `engagement_report.py`'s fourth copy, `_FIRST_TOUCH_DRAFTED_STAGES`.
3. `CLAUDE.md` — new `## Decision-context tagging` section after `## Voice DNA`, covering: the JSONB shape and hash method, both call sites, the NULL-means-not-instrumented governance invariant, no TS mirror needed (contrast with Voice DNA — no prompt-assembly change), the report's raw-join posture and `n >= 5` threshold, and Part A rejected-not-deferred with the one-line reason.
4. `CLAUDE.md` `## Tests` — two new bullets for `test_decision_context.py` and `test_engagement_report.py`.
5. `docs/python/db-schema.md` — new `decision_context JSONB` bullet under `draft_history`; amend the `log_drafted_email` function bullet; add `get_draft_history_by_stages` and `get_research_reliability_map` bullets.
6. Memory: new `project-decision-context-tagging.md` (shipped 2026-08-25, sub-project 3 of 4; whole-snapshot hash rationale; NULL-means-not-instrumented; no TS mirror and why; report's `n>=5` threshold; tracer links rejected not deferred) + `MEMORY.md` index line.
7. Commit docs.

---

## Task 8: Full suite and push

1. Run `python3 -m pytest` (or the correct interpreter for this environment) — must be fully green before proceeding.
2. `git push`.

---

## Definition of done

1. Full test suite green.
2. Migration applied via `supabase db push`; `draft_history.decision_context` exists and is nullable.
3. `CLAUDE.md` updated: module layout, amended `_FIRST_TOUCH_ACTIONS` bullet, new section, two test-list entries.
4. `docs/python/db-schema.md` updated: new column bullet, amended function bullet, two new accessor bullets.
5. Memory entry written, `MEMORY.md` index updated.
6. `engagement_report.py` never writes, is not in any workflow, renders NULL `decision_context` as "unknown".
7. A `decision_context` failure can never lose a `draft_history` row or block a draft.
8. Nothing auto-sends. No tracer link, redirect domain, or tracking pixel — Part A is rejected, not deferred.
