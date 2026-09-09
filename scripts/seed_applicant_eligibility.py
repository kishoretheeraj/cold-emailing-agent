"""One-off seed for the applicant_eligibility prompts row -- run once, then edit the real answers
live via the contact-manager's Prompts page. Never re-run against a populated row without checking
first (it would overwrite real answers with placeholders).

Inserts the row directly rather than via db.upsert_prompt: that function only ever sets
key/value/updated_at, which is correct for editing an existing row (its only other caller,
extract_voice.py, always targets the pre-existing voice_dna row) but fails on a genuinely new
key -- prompts.display_title is NOT NULL and upsert_prompt never sets it. Caught live: the first
real run of this script raised a Postgres 23502 (null value in column "display_title") instead of
seeding the row, silently blocking every apply_agent.py preview run that depended on it."""

import json
from datetime import datetime, timezone

import db

_DEFAULT = {
    "work_authorized_us": "unknown -- fill in via Prompts page",
    "requires_visa_sponsorship": "unknown -- fill in via Prompts page",
    "gender": "decline to self-identify",
    "race_ethnicity": "decline to self-identify",
    "disability_status": "decline to self-identify",
    "veteran_status": "decline to self-identify",
    "lgbtq_identity": "decline to self-identify",
}


def seed(default=None):
    """Insert the applicant_eligibility row if it doesn't already have a value. Returns True if a
    row was written, False if an existing value was left untouched."""
    existing = db.load_prompts().get("applicant_eligibility")
    if existing:
        return False
    row = {
        "key": "applicant_eligibility",
        "value": json.dumps(default if default is not None else _DEFAULT),
        "display_title": "Applicant Eligibility",
        "description": "Fixed-answer sensitive questions for apply_agent.py -- work authorization, "
                        "visa sponsorship, EEO. Never LLM-generated per application.",
        "sort_order": 66,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    db.get_client().table("prompts").upsert(row, on_conflict="key").execute()
    return True


if __name__ == "__main__":
    if seed():
        print("Seeded applicant_eligibility with placeholder values -- go fill in the real answers via the Prompts page.")
    else:
        print("applicant_eligibility already has a value -- not overwriting. Edit it via the Prompts page instead.")
