# Resilience patterns

- **Anthropic API** (`emailer._call_claude`): uses the official `anthropic` SDK
  with `max_retries=4` (5 total attempts). The SDK auto-retries 429, 529, and
  5xx; non-retryable 4xx raise immediately. Signature:
  `_call_claude(prompt, model=None, max_tokens=1000, system=None)`. When `system`
  is provided it is sent as a system-prompt block with `cache_control: ephemeral`
  for prompt caching. All seven system-bearing generators pass the sender profile
  as `system`: `_generate_outreach`, `_generate_applied_intro`,
  `_generate_applied_followup`, `_generate_subject`, `_run_critic`
  (all in `emailer.py`); `_generate_reply_body` (`reply_drafter.py`); and
  `_generate_queries` (`research.py`). `_curate_brief` and `_classify_reply`
  have no stable system component and send no `system` param.
  `_call_claude` logs `[CACHE] model=... | cache_read=N | cache_created=N`
  at INFO level whenever either counter is non-zero, so cache hits are visible
  in `agent.log` / `monitor.log`.
  Run `python3 measure_caching.py` to count tokens for every system block
  and see which are above/below the cache threshold. Caching activates
  automatically when the system prompt reaches the 1024-token minimum
  (currently below threshold; grows as Supabase prompts are edited).
  **Credit exhaustion fast-fail**: a module-level `_credit_exhausted` flag
  is set on the first `400 credit balance too low` response. All subsequent
  `_call_claude` calls raise `RuntimeError` immediately without hitting the
  network, saving the `INTER_CALL_SLEEP` sleep and HTTP round-trip for every
  remaining contact. Log marker: `[CREDIT] Anthropic credit balance exhausted`.
- **Tavily** (`research.py`): `_get_client()` lazily initialises a singleton
  `TavilyClient`. All failures inside `get_research_brief` degrade to `""` —
  the function never raises. Absent `TAVILY_API_KEY` short-circuits immediately.
- **Supabase** (`db._retry`): every query/update is wrapped in a 3-attempt
  retry with the same 2 s / 4 s backoff. Catches broad `Exception` — Supabase
  blips are transient; the retry budget is small.
- **Failure notification** (`notify_failure.py`): both workflows have an
  `if: failure()` step that runs this script. It emails `GMAIL_ADDRESS` via
  Gmail SMTP using `GMAIL_APP_PASSWORD` — no new secrets required.
- **Prompt validation** (`agent._validate_prompts`): called at the top of `run()` right after `load_prompts()`. Checks every formattable prompt against `_PROMPT_VALID_KEYS` (a dict of prompt key → valid format kwargs, mirroring each `tpl.format(...)` call site). If any prompt contains a `{placeholder}` the code never provides, it logs `[PROMPT-VALIDATION] <key>: unknown placeholder(s) [...]` and raises `ValueError` before contacting Supabase, Tavily, or Anthropic — zero wasted API credits. The valid-key map lives in `agent._PROMPT_VALID_KEYS` (Python) and `src/lib/promptVariables.PROMPT_VALID_KEYS` (TypeScript, used by the contact-manager UI). **Keep both in sync** if you add a new prompt or change a format() call site.
- **Batch API fallback** (`agent.py` Phases 2–5): the Anthropic Messages Batch API path has two-layer resilience. Partial failure: if individual batch results have `type != "succeeded"`, those contacts are appended to `retry_items` and re-attempted sequentially in Phase 5 via `generate_email()`. Catastrophic failure: if `batches.create()` or the poll loop raises, the entire `try/except` around Phases 2–4 catches it, sets `retry_items` to every collected contact, and Phase 5 runs sequential generation for all of them — **including contacts already successfully drafted earlier in the same batch**, so anything that can raise while processing one already-`"succeeded"` result (e.g. extracting `result.result.message.content[0].text`) must stay inside the per-contact `try/except`, not before it, or one malformed item escalates into re-processing the whole batch. `_execute_draft()` is a shared private helper (extracted to avoid duplicating the create-draft / persist / label / update flow between the batch success path and the sequential retry path).
- **`.format()` template calls must catch broad `Exception`, not just `KeyError`.** A live-edited Supabase prompt can contain a stray `{}` or `{0}` (e.g. a pasted JSON example), which raises `IndexError`/`ValueError` from `str.format()`, not `KeyError`. Every prompt-formatting try/except in `emailer._run_critic`, `research._generate_queries`, and `research._curate_brief` catches `Exception` for this reason — narrowing back to `KeyError` reintroduces an uncaught-exception path that bypasses the intended "log + fall back" behavior documented in docs/python/critic-loop.md.
