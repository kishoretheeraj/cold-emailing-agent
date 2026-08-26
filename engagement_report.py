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

# Fourth copy of the first-touch set (agent._FIRST_TOUCH_ACTIONS,
# emailer._FIRST_TOUCH_ACTIONS, monitor.detect_sent_drafts's stage-level set,
# and this one). Stage-level like monitor's, manually synced -- see CLAUDE.md.
_FIRST_TOUCH_DRAFTED_STAGES = (
    "first_touch_drafted", "applied_intro_drafted", "networking_drafted",
)

MIN_GROUP_N = 5
UNKNOWN = "unknown"


# ── Row construction ───────────────────────────────────────────────────────────

def _prompt_hash(row):
    ctx = row.get("decision_context")
    if not isinstance(ctx, dict):
        return UNKNOWN          # NULL, or a shape we did not write
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
                    continue          # rows arrive drafted_at DESC: newest wins
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


# ── Rendering ──────────────────────────────────────────────────────────────────

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


# ── Main ───────────────────────────────────────────────────────────────────────

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
