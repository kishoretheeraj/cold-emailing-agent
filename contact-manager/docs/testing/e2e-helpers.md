# e2e helpers (Playwright)

`tests/e2e/helpers.ts` `mockSupabase()` intercepts four tables:
- `/rest/v1/contacts` — returns fixture rows (filtered) or handles PATCH/DELETE
- `/rest/v1/prompts` — returns prompts fixture (14 fixture rows)
- `/rest/v1/email_messages` — returns `[]` (empty thread)
- `/rest/v1/agent_events` — returns `[]` with `Content-Range: 0-0/0`

**`applyFilters` handles these URL params:** `or` (name/company ilike), `tier=in.(...)`,
`mode=in.(...)`, `stage=in.(...)`, `dartmouth=eq.true`, `created_at=lt.ISO`,
`id=eq.{id}` (single-row fetch by primary key — for `openContact`'s `.single()` call).

**`.single()` support:** when the request has `Accept: application/vnd.pgrst.object+json`
(set automatically by Supabase's `.single()`), the mock returns a plain JSON object (not
array) so the Supabase client parses it correctly. Without this, `.single()` would receive
an array, treat it as `data`, and corrupt `selectedContact` state.

When writing new e2e tests that need non-empty `email_messages` or `agent_events`, add a `page.route()` override **before** calling `mockSupabase(page)` or add a dedicated helper fixture.
