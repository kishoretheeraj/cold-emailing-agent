# Critic loop (v1)

`emailer.critique_and_revise()` runs on Tier 1 first-touch emails only
(`send_first_touch` and `send_applied_intro` when `contact["tier"] == 1`).
All other tiers and all follow-up actions skip it entirely.

- **Pass condition**: `rewrite_required == False` in the critic JSON response.
  The critic now returns an 8-key dict: `verdict` (PASS/FAIL), `score` (0–21),
  `rewrite_required` (bool), `killed_by`, `failed_soft_criteria`,
  `banned_phrases_found`, `ai_tells_found`, `feedback`. 21 criteria: K1–K11
  kill switches + S1–S10 soft criteria. If `rewrite_required` is True, one
  regeneration is triggered via `extra_instruction`. Max 2 generation attempts
  total — never loop.
- **Prompt**: `critic_prompt` key in the Supabase `prompts` table
  (`sort_order=25`). `CRITIC_PROMPT_DEFAULT` in `config.py` is the fallback.
- **Failure safety**: any error inside `_run_critic` (format error, Claude
  error, JSON parse failure) returns the pass-through fallback
  `{"verdict": "PASS", "score": 16, "rewrite_required": False, ...}` and
  logs a warning. Critic failures never block draft creation.
- **Log marker**: `[CRITIC] | name | company | score=N | killed_by=[...] | failed_soft=[...] | retried=<bool>`
  appears exactly once per Tier 1 first-touch draft.
- **`CRITIC_PASS_THRESHOLD`** in `config.py` is no longer used by `critique_and_revise` — the pass condition is `rewrite_required`, not a numeric threshold. The constant is kept for reference but has no effect.
- **Cost**: adds 1 critic Claude call per Tier 1 first-touch, plus 1 optional
  regeneration call. Subject is also regenerated on retry.
- **Common first-draft failures to guard in `outreach_prompt`**:
  - K5 (structural AI tells): rule-of-three tricolons, "from X to Y" ranges,
    participial -ing closers ("...helping companies scale"). The outreach_prompt
    FINAL PASS must include explicit checks for these — listing them in ABSOLUTE
    NO'S alone is insufficient.
  - K7 (weak CTA): any open-ended question ("What do you look for in...") fails.
    The outreach_prompt CTA guidance must require a yes/no question for ALL
    recipient types. Never instruct "ask about perspective/their path" — that
    produces open-ended questions that K7 rejects.
  - S10 (dense paragraph): the outreach_prompt format rule must say "one sentence
    per paragraph, blank line between each". Any phrasing that permits grouping
    sentences (e.g. "may sit on the same line") causes `_normalize_body` to
    collapse them into a dense paragraph that S10 rejects.
  - S6 (multiple question marks): must be in ABSOLUTE NO'S, not just FINAL PASS.
