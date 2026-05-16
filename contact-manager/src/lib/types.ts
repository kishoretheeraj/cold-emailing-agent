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
  message_id: string | null;
  last_emailed: string | null;
  deleted_at: string | null;
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

export type ContactsQueryFilters = {
  nameOrCompany: string;
  stages: string[];
  tiers: number[];
  modes: ("outreach" | "applied")[];
  dartmouthOnly: boolean;
};

export const EMPTY_FILTERS: ContactsQueryFilters = {
  nameOrCompany: "",
  stages: [],
  tiers: [],
  modes: [],
  dartmouthOnly: false,
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
    a.dartmouthOnly === b.dartmouthOnly
  );
}
