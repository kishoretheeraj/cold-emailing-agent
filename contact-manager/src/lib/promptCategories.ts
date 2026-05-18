export const CATEGORY_ORDER = [
  "Sender & Core",
  "Outreach Modifiers",
  "Applied",
  "Research Pipeline",
  "Reply Pipeline",
  "Retrospective",
  "Shared",
] as const;

export type PromptCategory = (typeof CATEGORY_ORDER)[number];

// Keys not listed here fall into "Shared" automatically — see CLAUDE.md:
// "Categorization drift" note for guidance when adding new prompt rows.
export const PROMPT_CATEGORY_MAP: Record<string, PromptCategory> = {
  sender_profile: "Sender & Core",
  outreach_prompt: "Sender & Core",
  subject_prompt: "Sender & Core",
  critic_prompt: "Sender & Core",

  outreach_first_touch_instruction: "Outreach Modifiers",
  outreach_followup1_instruction: "Outreach Modifiers",
  outreach_followup2_instruction: "Outreach Modifiers",
  outreach_breakup_instruction: "Outreach Modifiers",
  tier_1_instruction: "Outreach Modifiers",
  tier_2_instruction: "Outreach Modifiers",
  tier_3_instruction: "Outreach Modifiers",

  applied_intro_prompt: "Applied",
  applied_followup_prompt: "Applied",

  research_query_prompt: "Research Pipeline",
  research_curate_prompt: "Research Pipeline",
  research_injection: "Research Pipeline",

  reply_classification_prompt: "Reply Pipeline",
  reply_response_prompt: "Reply Pipeline",
  forbidden_phrases: "Reply Pipeline",
  guardrail_company_list: "Reply Pipeline",

  retrospective_pair_analysis_prompt: "Retrospective",
  retrospective_aggregation_prompt: "Retrospective",

  dartmouth_instruction: "Shared",
};
