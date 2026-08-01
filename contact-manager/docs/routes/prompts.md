# /prompts page (app/prompts/page.tsx)

Client component (`PromptsPage.tsx`). Fetches all prompts ordered by `sort_order`. Sticky search (title + description). 8 collapsible categories via `PROMPT_CATEGORY_MAP` in `src/lib/promptCategories.ts` (added "Networking" between "Applied" and "Research Pipeline" — one category per mode is the established convention, not the exception); unknown keys fall into "Shared". Only "Sender & Core" open by default; state persists in `localStorage` (`"prompts-open-categories"`). localStorage read in `useEffect` post-mount (SSR-safe skeleton pattern). `PromptCategory.tsx` is the collapsible section wrapper; `PromptSection.tsx` (individual card) unchanged.

**Categorization drift:** When adding new prompt rows to Supabase, add the key to `promptCategories.ts` — omitted keys silently land in "Shared".

**Placeholder validation:** `PromptSection.tsx` calls `getUnknownVariables(prompt.key, draft)` from `src/lib/promptVariables.ts` on every render. Any `{placeholder}` that Python's `.format()` would try to fill but the code never provides triggers an amber inline warning. `PROMPT_VALID_KEYS` in `promptVariables.ts` mirrors `agent._PROMPT_VALID_KEYS` in Python — **keep both in sync** when adding a new prompt or changing a `tpl.format(...)` call site.

**Locked prompt:** `/api/extract` prompt (`route.ts`) is hardcoded, not in the prompts table. Bound to `ExtractedContact` JSON schema — editing it requires a `types.ts` update and code deploy in sync.
