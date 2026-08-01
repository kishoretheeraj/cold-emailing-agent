export type Mode = "outreach" | "applied" | "networking";
export type ReplyStatus =
  | "no_reply"
  | "replied"
  | "interested"
  | "call_scheduled"
  | "dead";

export type CompanyIntelMatchStatus = "unknown" | "auto" | "needs_review" | "confirmed" | "rejected";

export type CompanyIntel = {
  id: number;
  normalized_name: string;
  raw_company_names: string[];
  matched_employer_id: number | null;
  match_confidence: number | null;
  match_status: CompanyIntelMatchStatus;
  top_candidates: { employer_id: number | null; normalized_name: string; score: number }[] | null;
  sponsors_h1b: boolean | null;
  h1b_recent_count: number | null;
  latest_filing_fy: number | null;
  approval_rate: number | null;
  typical_wage_level: string | null;
  cap_exempt_likely: boolean | null;
  source_vintages: Record<string, unknown> | null;
  reviewed_by_user_at: string | null;
  created_at: string;
  updated_at: string;
};

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
  classifier_status: string | null;
  dartmouth: boolean | null;
  job_title: string | null;
  job_description: string | null;
  company_applied: string | null;
  applied_date: string | null;
  connection_context: string | null;
  followup_date: string | null;
  notes: string | null;
  resume_url?: string | null;
  created_at?: string;
  message_id: string | null;
  last_emailed: string | null;
  deleted_at: string | null;
  state?: string | null;
  company_intel_id?: number | null;
  company_intel?: Pick<CompanyIntel, "sponsors_h1b" | "h1b_recent_count" | "match_status"> | null;
};

export type EmailMessage = {
  id: number;
  contact_id: number;
  direction: "outgoing" | "incoming";
  subject: string | null;
  body: string | null;
  sent_at: string;
  message_id: string | null;
  in_reply_to: string | null;
  stage_at_send: string | null;
  raw_headers: Record<string, unknown> | null;
};

export type DraftHistory = {
  id: number;
  contact_id: number;
  stage: string;
  subject: string | null;
  body: string | null;
  message_id: string | null;
  gmail_draft_id: string | null;
  drafted_at: string;
  sent_subject: string | null;
  sent_body: string | null;
  sent_at: string | null;
  edit_detected: boolean | null;
};

export type AgentEvent = {
  id: number;
  run_id: number | null;
  event_type: string;
  contact_id: number | null;
  contact_name: string | null;
  status: string;
  error_message: string | null;
  metadata: Record<string, unknown> | null;
  tokens_used: number | null;
  started_at: string;
  completed_at: string | null;
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
  connection_context: string | null;
  notes: string | null;
  resume_url?: string | null;
  state?: string | null;
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

export const REPLY_STAGES = ["reply_drafted", "reply_sent"];

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

export const NETWORKING_STAGES = [
  "new",
  "networking_drafted",
  "networking_sent",
  "networking_followup_drafted",
  "networking_followup_sent",
  "closed",
];

export const REPLY_STATUSES: ReplyStatus[] = [
  "no_reply",
  "replied",
  "interested",
  "call_scheduled",
  "dead",
];

export type ContactsQueryFilters = {
  nameOrCompany: string;
  stages: string[];
  tiers: number[];
  modes: ("outreach" | "applied" | "networking")[];
  dartmouthOnly: boolean;
  needsResponseOnly: boolean;
  sponsorsH1bOnly: boolean;
};

export const EMPTY_FILTERS: ContactsQueryFilters = {
  nameOrCompany: "",
  stages: [],
  tiers: [],
  modes: [],
  dartmouthOnly: false,
  needsResponseOnly: false,
  sponsorsH1bOnly: false,
};

export function filtersEqual(
  a: ContactsQueryFilters,
  b: ContactsQueryFilters
): boolean {
  return (
    a.nameOrCompany === b.nameOrCompany &&
    a.stages.length === b.stages.length &&
    a.stages.every((s, i) => s === b.stages[i]) &&
    a.tiers.length === b.tiers.length &&
    a.tiers.every((t, i) => t === b.tiers[i]) &&
    a.modes.length === b.modes.length &&
    a.modes.every((m, i) => m === b.modes[i]) &&
    a.dartmouthOnly === b.dartmouthOnly &&
    a.needsResponseOnly === b.needsResponseOnly &&
    a.sponsorsH1bOnly === b.sponsorsH1bOnly
  );
}
