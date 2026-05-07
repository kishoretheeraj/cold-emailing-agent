// ── Stage sequences ───────────────────────────────────────────────────────────

export const OUTREACH_STAGES = [
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
] as const;

export const APPLIED_STAGES = [
  "new",
  "applied_intro_drafted",
  "applied_intro_sent",
  "applied_followup_drafted",
  "applied_followup_sent",
  "closed",
] as const;

// ── Reply status values ───────────────────────────────────────────────────────

export const REPLY_STATUSES = [
  "no_reply",
  "replied",
  "interested",
  "call_scheduled",
  "dead",
] as const;

export const TERMINAL_REPLY_STATUSES = [
  "replied",
  "interested",
  "call_scheduled",
  "dead",
] as const;

// ── Derived sets ──────────────────────────────────────────────────────────────

export const DRAFTED_STAGES = [
  ...OUTREACH_STAGES,
  ...APPLIED_STAGES,
].filter((s) => s.includes("drafted"));
