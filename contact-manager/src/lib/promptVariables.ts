// Matches single-brace {identifier} only; \w+ requires word chars so
// patterns with spaces (e.g., {First Name}) never match.
export function extractVariables(template: string): string[] {
  const matches = template.matchAll(/\{(\w+)\}/g);
  const seen = new Set<string>();
  const out: string[] = [];
  for (const m of matches) {
    if (!seen.has(m[1])) {
      seen.add(m[1]);
      out.push(m[1]);
    }
  }
  return out;
}

// Exact set of keys each Python prompt passes to .format(**kwargs).
// Prompts not listed here are either not formatted or use config.py defaults only.
export const PROMPT_VALID_KEYS: Record<string, ReadonlySet<string>> = {
  outreach_prompt: new Set([
    "profile", "name", "company", "role", "detail", "tier",
    "tier_instruction", "template", "template_instruction", "dartmouth_instruction",
  ]),
  // Per-action instructions injected as {template_instruction} — free-text, no format variables
  outreach_first_touch_instruction: new Set<string>([]),
  outreach_followup1_instruction: new Set<string>([]),
  outreach_followup2_instruction: new Set<string>([]),
  outreach_breakup_instruction: new Set<string>([]),
  applied_intro_prompt: new Set([
    "profile", "name", "role", "company", "job_title", "job_description",
    "applied_date", "dartmouth_instruction",
  ]),
  applied_followup_prompt: new Set([
    "profile", "name", "role", "company", "job_title", "dartmouth_instruction",
  ]),
  subject_prompt: new Set(["name", "company", "mode", "job_title", "body"]),
  critic_prompt: new Set(["sender_profile", "contact_context", "subject", "body"]),
  research_injection: new Set(["brief_text"]),
  research_query_prompt: new Set([
    "sender_profile", "name", "company", "role", "detail", "notes", "dartmouth", "tier",
  ]),
  research_curate_prompt: new Set(["name", "company", "role", "detail", "raw_results"]),
  reply_response_prompt: new Set(["profile", "name", "company", "role", "reply_body"]),
  reply_classification_prompt: new Set(["reply_body"]),
};

// Extracts ALL single-brace placeholders Python's str.format() would try to substitute,
// including ones with spaces like {First Name}. Skips escaped doubles {{...}}.
export function getPythonFormatPlaceholders(template: string): string[] {
  const matches = template.matchAll(/(?<!\{)\{([^{}]+)\}(?!\})/g);
  const seen = new Set<string>();
  const out: string[] = [];
  for (const m of matches) {
    if (!seen.has(m[1])) {
      seen.add(m[1]);
      out.push(m[1]);
    }
  }
  return out;
}

// Returns placeholders in `template` that Python's .format() would try to fill
// but the code never provides for this prompt key. Empty array = no issues.
export function getUnknownVariables(key: string, template: string): string[] {
  const valid = PROMPT_VALID_KEYS[key];
  if (!valid) return [];
  return getPythonFormatPlaceholders(template).filter((p) => !valid.has(p));
}

// Prompts whose Claude output is parsed as structured JSON by the Python code.
// `keys` = the JSON keys the code reads (renaming any of these breaks the run).
// `label` = human-readable description of the expected output format.
export const PROMPT_OUTPUT_SCHEMAS: Record<
  string,
  { keys: readonly string[]; label: string }
> = {
  critic_prompt: {
    keys: ["verdict", "rewrite_required", "score", "killed_by", "failed_soft_criteria", "feedback"],
    label: "Critic evaluation JSON",
  },
  reply_classification_prompt: {
    keys: ["classifier_status"],
    label: "Reply classification JSON",
  },
  research_query_prompt: {
    keys: [],
    label: "JSON array of search query strings",
  },
};
