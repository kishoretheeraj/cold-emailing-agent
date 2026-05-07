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

export { OUTREACH_STAGES, APPLIED_STAGES, REPLY_STATUSES } from "@/lib/constants";
