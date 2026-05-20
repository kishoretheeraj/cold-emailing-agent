// MIRRORED FROM agent's config.py::FOLLOWUP_DAYS — keep in sync.
// If agent cadence values change, update both files.
// Same "mirrored constant" pattern as REPLY_STAGES in types.ts.
export const CADENCE = {
  FIRST_TOUCH_TO_FU1_DAYS: 5,
  FU1_TO_FU2_DAYS: 4,
  FU2_TO_BREAKUP_DAYS: 3,
  APPLIED_INTRO_TO_FU_DAYS: 5,
} as const;

export type CadenceKey = keyof typeof CADENCE;

export const STAGE_TRANSITIONS: Record<
  string,
  { next: string; cadenceKey: CadenceKey | null }
> = {
  first_touch_drafted: {
    next: "first_touch_sent",
    cadenceKey: "FIRST_TOUCH_TO_FU1_DAYS",
  },
  followup1_drafted: {
    next: "followup1_sent",
    cadenceKey: "FU1_TO_FU2_DAYS",
  },
  followup2_drafted: {
    next: "followup2_sent",
    cadenceKey: "FU2_TO_BREAKUP_DAYS",
  },
  breakup_drafted: {
    next: "breakup_sent",
    cadenceKey: null,
  },
  applied_intro_drafted: {
    next: "applied_intro_sent",
    cadenceKey: "APPLIED_INTRO_TO_FU_DAYS",
  },
  applied_followup_drafted: {
    next: "applied_followup_sent",
    cadenceKey: null,
  },
  reply_drafted: {
    next: "reply_sent",
    cadenceKey: null,
  },
};

// /queue uses QUEUE_STAGES; /replies handles reply_drafted separately.
export const QUEUE_STAGES = [
  "first_touch_drafted",
  "followup1_drafted",
  "followup2_drafted",
  "breakup_drafted",
  "applied_intro_drafted",
  "applied_followup_drafted",
] as const;
