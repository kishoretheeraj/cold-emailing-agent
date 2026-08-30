"""One-off seed for the applicant_eligibility prompts row -- run once, then edit the real answers
live via the contact-manager's Prompts page. Never re-run against a populated row without checking
first (it would overwrite real answers with placeholders)."""

import json

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

if __name__ == "__main__":
    existing = db.load_prompts().get("applicant_eligibility")
    if existing:
        print("applicant_eligibility already has a value -- not overwriting. Edit it via the Prompts page instead.")
    else:
        db.upsert_prompt("applicant_eligibility", json.dumps(_DEFAULT))
        print("Seeded applicant_eligibility with placeholder values -- go fill in the real answers via the Prompts page.")
