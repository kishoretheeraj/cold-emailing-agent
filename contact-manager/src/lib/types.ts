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
  resume_url?: string | null;
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
  resume_url?: string | null;
  missing_email?: boolean;
  missing_required?: boolean;
  required_missing_fields?: string[];
};

export type BulkExtractResponse = {
  contacts: ExtractedContact[];
  count: number;
  is_bulk: boolean;
};

export type ContactReviewStatus = "pending" | "confirmed" | "skipped";

export type ReviewContact = ExtractedContact & {
  status: ContactReviewStatus;
};

export type BulkImportWindow = {
  startedAt: number;
  endedAt: number;
};

export type Prompt = {
  key: string;
  value: string;
  description: string | null;
  display_title: string;
  default_value: string | null;
  sort_order: number;
  updated_at: string;
};

export { OUTREACH_STAGES, APPLIED_STAGES, REPLY_STATUSES } from "@/lib/constants";
