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

NETWORKING_STAGES = [
    "new",
    "networking_drafted",
    "networking_sent",
    "networking_followup_drafted",
    "networking_followup_sent",
    "closed",
]

# ── Reply status values ────────────────────────────────────────────────────────

REPLY_STATUSES = ["no_reply", "replied", "interested", "call_scheduled", "dead"]

TERMINAL_REPLY_STATUSES = ["replied", "interested", "call_scheduled", "dead"]

# ── Derived sets ──────────────────────────────────────────────────────────────

# ── Reply stages (set by reply_drafter; detected by monitor sent-detection) ────

REPLY_STAGES = ["reply_drafted", "reply_sent"]

# ── Derived sets ──────────────────────────────────────────────────────────────

DRAFTED_STAGES = [
    s for s in OUTREACH_STAGES + APPLIED_STAGES + NETWORKING_STAGES + REPLY_STAGES
    if "drafted" in s
]

TERMINAL_DRAFTED_STAGES = {
    "breakup_drafted", "applied_followup_drafted", "networking_followup_drafted", "reply_drafted",
}
