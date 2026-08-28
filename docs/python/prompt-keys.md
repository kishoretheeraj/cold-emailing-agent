# Prompt keys (Supabase prompts table — 25 rows)

Loaded at startup by `db.load_prompts()` → `{key: value}`. Absent keys fall back to
`config.py` constants; `emailer.py` logs `[WARN] prompt key X not in DB — using fallback`.
Instruction-level keys (sort_orders 11–18) use `get_tier_instruction()`,
`get_template_instruction()`, `get_dartmouth_instruction()` in `emailer.py`.

| key | sort_order | purpose |
|-----|-----------|---------|
| `sender_profile` | 10 | Sender bio; injected as `{profile}` into every email template |
| `outreach_first_touch_instruction` | 11 | `{template_instruction}` for `cold_intro` emails |
| `outreach_followup1_instruction` | 12 | `{template_instruction}` for `follow_up_1` emails |
| `outreach_followup2_instruction` | 13 | `{template_instruction}` for `follow_up_2` emails |
| `outreach_breakup_instruction` | 14 | `{template_instruction}` for `breakup` emails |
| `tier_1_instruction` | 15 | `{tier_instruction}` for Tier 1 contacts |
| `tier_2_instruction` | 16 | `{tier_instruction}` for Tier 2 contacts |
| `tier_3_instruction` | 17 | `{tier_instruction}` for Tier 3 contacts |
| `dartmouth_instruction` | 18 | `{dartmouth_instruction}` when alumnus detected; used in outreach AND applied |
| `outreach_prompt` | 20 | Cold outreach email body (all 4 templates) |
| `critic_prompt` | 25 | Critic editor prompt; Tier 1 first-touch only |
| `research_query_prompt` | 26 | Haiku generates 1–5 Tavily queries |
| `research_curate_prompt` | 27 | Haiku synthesises Tavily results into a brief |
| `research_injection` | 28 | Wraps curated brief before appending to outreach/applied-intro |
| `applied_intro_prompt` | 30 | Applied intro email with 3 bullets |
| `applied_followup_prompt` | 40 | Applied follow-up; short, adds one new value |
| `networking_prompt` | 41 | Networking first touch; leads with `{connection_context_instruction}`, never a role pitch |
| `networking_followup_prompt` | 42 | The single networking follow-up nudge; no role/hiring language |
| `networking_subject_prompt` | 43 | Networking subject line; casual, never job-flavored (dedicated key, not a branch in `subject_prompt`) |
| `subject_prompt` | 50 | Subject line for outreach/applied; called once per first-touch email |
| `reply_classification_prompt` | 60 | Claude Haiku classifier; returns `{"classifier_status": "..."}` JSON |
| `reply_response_prompt` | 61 | Reply body template for `reply_drafter.py` |
| `forbidden_phrases` | 62 | Newline-delimited banned substrings for pre-flight check 6 |
| `guardrail_company_list` | 63 | Newline-delimited company watchwords for pre-flight check 4 |
| `voice_dna` | 64 | Writing-style block written by `extract_voice.py`. Optional: absent or blank means no voice block is injected. Injected into first-touch prompts only, and mirrored in `assembleUserMessage.ts`. |
| `target_roles` | 65 | Newline-delimited role titles `job_discovery.py` filters ATS postings against; empty means match everything |

**Locked:** `/api/extract` prompt is hardcoded — bound to `ExtractedContact` JSON schema.
