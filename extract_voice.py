"""
Extracts a Voice DNA writing-style block from real sent mail and stores it as
the `voice_dna` prompts row. Run manually, not from the daily cron.

Usage: python3 extract_voice.py [--limit 40]
"""

import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M",
    handlers=[logging.FileHandler("extract_voice.log"), logging.StreamHandler()],
)
log = logging.getLogger(__name__)

import db
import gmail
from emailer import _call_claude

# ── Extraction prompt ──────────────────────────────────────────────────────────

VOICE_EXTRACTION_PROMPT = """You are analysing a person's real sent emails to describe how they write.

Below are {count} emails this person actually wrote.

<emails>
{samples}
</emails>

Produce a section titled "## Writing Style" describing observable habits only:
- typical sentence length and rhythm
- how they open and sign off
- contraction and hedging frequency
- characteristic word choices and phrases they reuse
- punctuation habits

Rules:
- Describe only what you observe. Do not invent traits or flatter the writer.
- Do not quote any email verbatim. Describe patterns, not content.
- Do not mention any company, person, or project name from the samples.
- Do not use em dashes anywhere in your output.
- Keep it under 200 words.

Output the "## Writing Style" section and nothing else."""

MAX_SAMPLE_CHARS = 1500


# ── Extraction ─────────────────────────────────────────────────────────────────

def _format_samples(bodies):
    parts = []
    for i, body in enumerate(bodies, 1):
        parts.append(f"--- Email {i} ---\n{body[:MAX_SAMPLE_CHARS]}")
    return "\n\n".join(parts)


def run(limit=40, min_samples=5):
    """
    Fetch recent sent mail, extract a writing-style block, write it to the
    `voice_dna` prompts row. Returns True when a row was written.
    """
    bodies = gmail.fetch_recent_sent(limit=limit)
    if len(bodies) < min_samples:
        log.warning(
            f"[VOICE] | extraction skipped | samples={len(bodies)} | "
            f"min_required={min_samples}"
        )
        return False

    prompt = VOICE_EXTRACTION_PROMPT.format(
        count=len(bodies), samples=_format_samples(bodies))

    try:
        raw = _call_claude(prompt)
    except Exception as exc:
        log.warning(f"[VOICE] | extraction failed | _call_claude error: {exc}")
        return False

    block = (raw or "").strip()
    if not block:
        log.warning("[VOICE] | extraction produced empty output | not written")
        return False

    db.upsert_prompt("voice_dna", block)
    log.info(f"[VOICE] | extraction complete | samples={len(bodies)} | chars={len(block)}")
    return True


if __name__ == "__main__":
    import sys
    _limit = 40
    if "--limit" in sys.argv:
        _limit = int(sys.argv[sys.argv.index("--limit") + 1])
    run(limit=_limit)
