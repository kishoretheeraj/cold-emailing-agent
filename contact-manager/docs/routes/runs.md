# /runs page (app/runs/page.tsx)

Client component. Fetches `agent_events` on mount and every 10 seconds via `setInterval`. Status filter chips (All / Success / Failed / Blocked) filter the in-memory list. Shows a 7-day failure badge in the header. Empty state when no events. Route: `/runs`, heading reads "Activity".

**Mocking in tests**: the `agent_events` list query uses `limitMock` as the terminal call; the count (badge) query uses a thenable `countChain`. These are separate chains distinguished by whether `select()` receives `{ count: "exact" }`.
