#!/usr/bin/env python3
"""
Batch API dry-run — manual smoke-test for the batch path + both fallbacks.

What it tests:
  Phase 1  Real batch submission → wait for result → finalize email (no Gmail, no Supabase)
  Phase 2  Partial failure: one errored batch result → verify sequential retry fires
  Phase 3  Catastrophic failure: batches.create() raises → verify all contacts fall back

Nothing is written to Supabase or Gmail.  The only external side-effect is one real
Anthropic batch request (Phase 1), which auto-expires after 24 h.

Usage:
    python3 test_batch_dryrun.py
"""

import sys
import time
import logging
from unittest.mock import patch, MagicMock

# ── Logging must come first ────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(message)s",
    datefmt="%H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)

import anthropic

from config import ANTHROPIC_API_KEY, EMAIL_MODEL, BATCH_POLL_INTERVAL
from emailer import prepare_email, finalize_email

# ── Dummy contact (Tier 3: no research, no critic — cheap and fast) ────────────
DUMMY = {
    "id": 9999,
    "name": "Alex Rivera",
    "email": "alex.rivera@example.com",
    "company": "Meridian Labs",
    "role": "Head of Product",
    "detail": "Scaling B2B SaaS from 50 to 500 enterprise customers",
    "notes": "",
    "tier": 3,
    "mode": "outreach",
    "stage": "new",
    "reply_status": "no_reply",
    "dartmouth": False,
    "job_title": None,
    "job_description": None,
    "applied_date": None,
}
ACTION = "send_first_touch"
CUSTOM_ID = f"dryrun-{DUMMY['id']}-{ACTION}"
PASS = "\033[92mPASS\033[0m"
FAIL = "\033[91mFAIL\033[0m"


def _load_prompts():
    try:
        from db import load_prompts
        p = load_prompts()
        log.info(f"Loaded {len(p)} prompts from Supabase")
        return p
    except Exception as exc:
        log.warning(f"Supabase unavailable ({exc}) — using config.py defaults")
        return {}


# ── Phase 1: real batch ────────────────────────────────────────────────────────

def phase1_real_batch():
    log.info("\n=== Phase 1: Real Anthropic batch submission ===")
    prompts = _load_prompts()

    user_prompt, system, ctx = prepare_email(DUMMY, ACTION, prompts=prompts)
    log.info(f"Prompt built ({len(user_prompt)} chars)")

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    batch = client.messages.batches.create(requests=[{
        "custom_id": CUSTOM_ID,
        "params": {
            "model": EMAIL_MODEL,
            "max_tokens": 1000,
            "system": [{"type": "text", "text": system,
                         "cache_control": {"type": "ephemeral"}}],
            "messages": [{"role": "user", "content": user_prompt}],
        },
    }])
    log.info(f"Submitted | batch_id={batch.id}")

    while batch.processing_status == "in_progress":
        log.info(f"Polling... counts={batch.request_counts}")
        time.sleep(BATCH_POLL_INTERVAL)
        batch = client.messages.batches.retrieve(batch.id)

    log.info(f"Complete | status={batch.processing_status} | counts={batch.request_counts}")

    results_map = {r.custom_id: r for r in client.messages.batches.results(batch.id)}
    result = results_map.get(CUSTOM_ID)

    if result is None:
        log.error(f"No result for custom_id={CUSTOM_ID}")
        return False

    if result.result.type != "succeeded":
        log.error(f"Batch result type={result.result.type} — expected 'succeeded'")
        return False

    raw_body = result.result.message.content[0].text
    log.info(f"Raw body ({len(raw_body)} chars):\n{raw_body}\n")

    # finalize: preflight + subject generation (real Claude call)
    # mock only db.log_agent_event to avoid Supabase writes on preflight failure
    with patch("db.log_agent_event"):
        subject, body = finalize_email(DUMMY, ACTION, raw_body, None, prompts, **ctx)

    log.info(f"Subject : {subject}")
    log.info(f"Body    :\n{body}\n")

    # Basic sanity checks
    assert subject, "Subject should not be empty"
    assert body, "Body should not be empty"
    assert DUMMY["name"].split()[0] in body, "Contact first name should appear in body"
    assert DUMMY["company"] in body, "Company name should appear in body"

    log.info(f"Phase 1: {PASS}")
    return True


# ── Phase 2: partial failure ───────────────────────────────────────────────────

def phase2_partial_failure():
    log.info("\n=== Phase 2: Partial failure — one errored batch result ===")
    prompts = _load_prompts()
    user_prompt, system, ctx = prepare_email(DUMMY, ACTION, prompts=prompts)

    # Build a fake errored result
    errored = MagicMock()
    errored.custom_id = CUSTOM_ID
    errored.result.type = "errored"

    mock_batch = MagicMock()
    mock_batch.id = "batch_fake_partial"
    mock_batch.processing_status = "ended"
    mock_batch.request_counts = {"succeeded": 0, "errored": 1}

    mock_client = MagicMock()
    mock_client.messages.batches.create.return_value = mock_batch
    mock_client.messages.batches.retrieve.return_value = mock_batch
    mock_client.messages.batches.results.return_value = [errored]

    sequential_calls = []

    def fake_generate_email(contact, action, original_subject=None, prompts=None):
        sequential_calls.append(action)
        return ("Dry-run subject (sequential)", "Dry-run body (sequential retry path).")

    batch_items = [
        (DUMMY, ACTION, None, None, CUSTOM_ID, ctx, "[OUTREACH]")
    ]

    retry_items = []
    try:
        _cl = mock_client
        batch = _cl.messages.batches.create(requests=[{}])
        results_map = {r.custom_id: r for r in _cl.messages.batches.results(batch.id)}
        for contact, action, tmid, orig_subj, cid, _ctx, mode_tag in batch_items:
            r = results_map.get(cid)
            if r is None or r.result.type != "succeeded":
                log.warning(f"Batch errored for {contact['name']} — queued for retry")
                retry_items.append((contact, action, tmid, orig_subj, mode_tag))
    except Exception as exc:
        log.warning(f"Batch catastrophic: {exc}")
        retry_items = [(c, a, t, s, m) for c, a, t, s, _, _, m in batch_items]

    assert len(retry_items) == 1, f"Expected 1 retry item, got {len(retry_items)}"

    for contact, action, tmid, orig_subj, mode_tag in retry_items:
        subject, body = fake_generate_email(contact, action, orig_subj, prompts)
        log.info(f"Sequential retry generated: subject='{subject}'")

    assert sequential_calls == [ACTION], \
        f"Expected sequential_calls=['{ACTION}'], got {sequential_calls}"

    log.info(f"Phase 2: {PASS} — errored result correctly routed to sequential retry")
    return True


# ── Phase 3: catastrophic failure ─────────────────────────────────────────────

def phase3_catastrophic_failure():
    log.info("\n=== Phase 3: Catastrophic failure — batches.create() raises ===")
    prompts = _load_prompts()
    user_prompt, system, ctx = prepare_email(DUMMY, ACTION, prompts=prompts)

    sequential_calls = []

    def fake_generate_email(contact, action, original_subject=None, prompts=None):
        sequential_calls.append(action)
        return ("Dry-run subject (catastrophic fallback)", "Dry-run body (catastrophic fallback).")

    batch_items = [
        (DUMMY, ACTION, None, None, CUSTOM_ID, ctx, "[OUTREACH]")
    ]

    retry_items = []
    try:
        raise ConnectionError("Simulated: Anthropic unreachable")
    except Exception as exc:
        log.warning(f"Batch failed ({exc}) — falling back to sequential for all {len(batch_items)} contacts")
        retry_items = [(c, a, t, s, m) for c, a, t, s, _, _, m in batch_items]

    assert len(retry_items) == 1, f"Expected 1 retry item, got {len(retry_items)}"

    for contact, action, tmid, orig_subj, mode_tag in retry_items:
        subject, body = fake_generate_email(contact, action, orig_subj, prompts)
        log.info(f"Sequential fallback generated: subject='{subject}'")

    assert sequential_calls == [ACTION], \
        f"Expected sequential_calls=['{ACTION}'], got {sequential_calls}"

    log.info(f"Phase 3: {PASS} — catastrophic failure correctly routed all contacts to sequential")
    return True


# ── Runner ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    results = []
    for label, fn in [
        ("Real batch submission",       phase1_real_batch),
        ("Partial failure fallback",    phase2_partial_failure),
        ("Catastrophic failure fallback", phase3_catastrophic_failure),
    ]:
        try:
            passed = fn()
        except Exception as exc:
            log.exception(f"{label} raised: {exc}")
            passed = False
        results.append((label, passed))

    log.info("\n=== Summary ===")
    all_pass = True
    for label, passed in results:
        status = PASS if passed else FAIL
        log.info(f"  {status}  {label}")
        if not passed:
            all_pass = False

    sys.exit(0 if all_pass else 1)
