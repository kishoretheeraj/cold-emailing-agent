export type Mode = "outreach" | "applied";
export type ReplyStatus =
  | "no_reply"
  | "replied"
  | "interested"
  | "call_scheduled"
  | "dead";

export type Contact = {
  id: string;
  name: string | null;
  email: string | null;
  company: string | null;
  role: string | null;
  detail: string | null;
  tier: number | null;
  mode: Mode | null;
  stage: string | null;
  reply_status: ReplyStatus | null;
  dartmouth: boolean | null;
  job_title: string | null;
  job_description: string | null;
  company_applied: string | null;
  applied_date: string | null;
  followup_date: string | null;
  notes: string | null;
  created_at?: string;
};

export type ExtractedContact = {
  name: string | null;
  email: string | null;
  company: string | null;
  role: string | null;
  detail: string | null;
  tier: number | null;
  mode: Mode | null;
  dartmouth: boolean | null;
  job_title: string | null;
  job_description: string | null;
  applied_date: string | null;
  notes: string | null;
};

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
];

export const APPLIED_STAGES = [
  "new",
  "applied_intro_drafted",
  "applied_intro_sent",
  "applied_followup_drafted",
  "applied_followup_sent",
  "closed",
];

export const REPLY_STATUSES: ReplyStatus[] = [
  "no_reply",
  "replied",
  "interested",
  "call_scheduled",
  "dead",
];
