# ── Stage sequences ────────────────────────────────────────────────────────────

OUTREACH_STAGES = [
    "new",
    "first_touch_drafted",
    "first_touch_sent",
    "followup1_drafted",
    "followup1_sent",
    "followup2_drafted",
    "followup2_sent",
    "breakup_drafted",
    "breakup_sent",
    "closed",
]

APPLIED_STAGES = [
    "new",
    "applied_intro_drafted",
    "applied_intro_sent",
    "applied_followup_drafted",
    "applied_followup_sent",
    "closed",
]

# ── Reply status values ────────────────────────────────────────────────────────

REPLY_STATUSES = ["no_reply", "replied", "interested", "call_scheduled", "dead"]

TERMINAL_REPLY_STATUSES = ["replied", "interested", "call_scheduled", "dead"]

# ── Derived sets ──────────────────────────────────────────────────────────────

DRAFTED_STAGES = [s for s in OUTREACH_STAGES + APPLIED_STAGES if "drafted" in s]
