@AGENTS.md

# Cold Email Ops — Contact Manager

A Next.js app for adding contacts to the cold-email Supabase table that the Python agent
reads every morning. Two input modes (Smart Input / Structured Form), an infinite-scroll
contacts list with search/filter, and a Vaul side sheet for editing all contact fields and
soft delete.

When working in this repo, follow the rules below. They reflect how the code was actually
written, not just style preferences.

---

## Stack and versions

- **Next.js 16** with the App Router. (Treat anything from earlier Next docs with suspicion
  — see `AGENTS.md`.)
- **React 19**. Use `"use client"` for any component that touches state, refs, effects, or
  browser APIs.
- **Tailwind CSS v4**. Theme tokens go in `@theme {}` inside `globals.css`, **not** in
  `tailwind.config.js`. No config file exists.
- **TypeScript strict mode**. Don't use `any` — use `unknown` and narrow.
- **@supabase/supabase-js** in the browser; no service role key in the client.
- **@anthropic-ai/sdk** server-side only, behind `/api/extract`.
- **Sonner** for toasts. Import `{ toast }` from `"sonner"` and call `toast.success()` /
  `toast.error()`. No homemade Toast component.
- **Vaul** for the contact side sheet (`direction="right"`). Wrappers in
  `src/components/ui/Sheet.tsx`.
- **Radix UI** for accessible primitives: Tooltip (`@radix-ui/react-tooltip`), Select
  (`@radix-ui/react-select`). Wrappers in `src/components/ui/`.
- **Lucide React** for icons.
- **Playwright** for e2e smoke tests. Run with `npm run test:e2e`.

## Module layout

```
src/
├── app/
│   ├── api/extract/route.ts        # Server-only Claude POST handler (prompt LOCKED — see below)
│   ├── api/trigger-agent/route.ts  # Proxies to GitHub Actions workflow_dispatch. Needs GITHUB_DISPATCH_TOKEN env var.
│   ├── api/send-draft/route.ts     # POST {contact_id} → drafts.send(), flips stage to *_sent, updates draft_history
│   ├── api/update-draft/route.ts   # POST {contact_id, subject, body} → drafts.update(), persists edits to draft_history
│   ├── api/trash-message/route.ts  # POST {message_id} → messages.trash() (reserved for future undo feature)
│   ├── overview/page.tsx           # Dashboard: action items, pipeline funnel, agent status. 30s auto-refresh.
│   ├── prompts/page.tsx            # 7-line server shell → <PromptsPage />
│   ├── queue/page.tsx              # Server shell → <QueuePage /> (bulk draft approval with 5s undo)
│   ├── replies/page.tsx            # Server shell → <RepliesPage /> (reply triage with 5s undo)
│   ├── runs/page.tsx               # Activity page — agent_events table, 10s auto-refresh
│   ├── globals.css                 # @theme tokens + global resets + Vaul overrides
│   ├── layout.tsx                  # Inter font, dark theme, AppProviders wrapper
│   └── page.tsx                    # 1-line server component → <App />
├── components/
│   ├── ui/                     # In-house primitive wrappers (NO shadcn)
│   │   ├── Badge.tsx           # Semantic color variants
│   │   ├── Skeleton.tsx        # Loading placeholder
│   │   ├── EmptyState.tsx      # Centered empty-state layout
│   │   ├── Tooltip.tsx         # Radix Tooltip wrapper + TooltipProvider re-export
│   │   ├── Select.tsx          # Radix Select wrapper (SelectTrigger, SelectItem, etc.)
│   │   ├── Sheet.tsx           # Vaul Drawer direction=right (SheetContent, SheetBody…)
│   │   └── ConfirmModal.tsx    # Radix Dialog wrapper for confirmation prompts
│   ├── App.tsx                 # Top-level shell + refreshKey + sonner toast calls
│   ├── AppProviders.tsx        # "use client" wrapper: TooltipProvider + Toaster
│   ├── SmartInput.tsx          # Paste → /api/extract → editable preview → save
│   ├── StructuredForm.tsx      # Two form sections: outreach + applied
│   ├── ContactsList.tsx        # Infinite-scroll list + filters + Vaul sheet + soft delete
│   ├── ContactsFilters.tsx     # Search input (with × clear button) + tier/mode pills + stage select + dartmouth + needs-response
│   ├── ThreadView.tsx          # Email thread history shown inside the Vaul side sheet
│   ├── PromptsPage.tsx         # "use client" — fetches all prompts, sticky search, 7 collapsible categories
│   ├── PromptCategory.tsx      # "use client" — collapsible section header + PromptSection list
│   ├── PromptSection.tsx       # "use client" — individual prompt card with save/reset; shows amber warning for unknown {placeholders}
│   ├── QueuePage.tsx           # "use client" — three-column bulk-send queue; 30s auto-refresh; focus by contact_id
│   ├── RepliesPage.tsx         # "use client" — two-column reply triage; classifier sort; 5s undo; 30s auto-refresh
│   └── Field.tsx               # Label / TextInput / TextArea / ToggleSwitch / TierSelector
└── lib/
    ├── supabase.ts             # Anon-key browser client singleton
    ├── gmail-server.ts         # Server-only Gmail API client (OAuth refresh token). Import in API routes only.
    ├── cadence.ts              # MIRRORED cadence constants — keep in sync with agent config.py::FOLLOWUP_DAYS
    ├── personalization.ts      # highlight(body, contact) → Segment[] — amber token highlighting for QueuePage
    ├── promptCategories.ts     # CATEGORY_ORDER, PROMPT_CATEGORY_MAP — TS-only, no DB column
    ├── promptVariables.ts      # extractVariables, getPythonFormatPlaceholders, getUnknownVariables, PROMPT_VALID_KEYS
    └── types.ts                # Contact + ReplyStatus + stage arrays + filter types + DraftHistory
tests/
└── e2e/                        # Playwright smoke tests
    ├── helpers.ts              # mockSupabase() — intercepts contacts, prompts, email_messages, agent_events, draft_history + API routes
    ├── fixtures/               # contacts.json (50 rows), prompts.json, draft_history.json (7 rows), email_messages.json (4 rows)
    └── *.spec.ts               # 00-shell through 12-replies
```

## Coding conventions

- **Server vs. client.** Default to server components. `page.tsx` is a server component
  that just imports the client `App`. Client components must start with `"use client";` on
  line 1 (no blank line above).
- **No emojis in shipped code or copy.**
- **No em dashes inside email-related copy or prompts.**
- **Tailwind utility classes only** — no inline `style` props. No CSS modules.
- **Theme tokens.** Use the custom palette: `bg-bg`, `bg-surface`, `bg-surface-2`,
  `border-border`, `border-border-strong`, `text-fg`, `text-fg-muted`, `text-fg-dim`, plus
  indigo / red / amber / emerald scales for semantic color. All defined in `globals.css`.
- **No new top-level CSS files.** Extend `globals.css` if needed.
- **No additional state libraries** (Redux, Zustand, etc.).

## Component patterns

- Forms use helpers from `Field.tsx`. `ToggleSwitch` props: `on` + `onChange` + `label`
  (NOT `value` — don't mix up).
- Toast via sonner: `toast.success(msg)` / `toast.error(msg)` directly in components, or
  passed as `onSuccess` / `onError` props from `App.tsx`.
- Side sheet: render `<Sheet>` + `<SheetContent>` from `@/components/ui/Sheet`. One Sheet
  instance per page, open controlled by selectedContact state.
- Confirm dialogs: use `<ConfirmModal>` from `@/components/ui/ConfirmModal`. Props:
  `open`, `title`, `body`, `confirmLabel`, `confirmVariant`, `onConfirm`, `onCancel`,
  `loading`.
- Tooltips: wrap trigger in `<Tooltip content="...">`. `TooltipProvider` is at the root
  (in AppProviders.tsx) — don't add another one.

## API route conventions

- Server-only routes live in `src/app/api/<name>/route.ts`.
- Use `export const runtime = "nodejs"` (Anthropic SDK requires Node).
- Never expose `ANTHROPIC_API_KEY` to the browser.
- Validate the body shape: `400` for missing input, `502` for malformed downstream data,
  `500` for unexpected SDK errors.
- Strip ` ```json` code fences from Claude responses before `JSON.parse`.

### Gmail API routes (Phase 0 — bulk-send infrastructure)

**POST `/api/send-draft`** — body: `{ contact_id: string }`
- Fetches contact + latest `draft_history` row with `sent_body IS NULL`.
- Calls `gmail.users.drafts.send({ id: gmail_draft_id })`.
- Flips contact `stage` to `*_sent`, sets `last_emailed` + `followup_date`.
- Updates `draft_history` with sent content. Inserts `email_messages` + `agent_events`.
- Idempotent: returns `{ already_sent: true }` if stage is already `*_sent`.
- Status codes: 400 (missing id), 404 (contact not found), 409 (wrong stage),
  410 (no draft ID or draft deleted from Gmail), 401 (auth failure), 502 (Gmail error).

**POST `/api/update-draft`** — body: `{ contact_id: string, subject: string, body: string }`
- Reads existing draft headers (In-Reply-To, References) via `drafts.get` to preserve threading.
- Calls `drafts.update` with a new RFC822 message. Persists edits to `draft_history`.
- Status codes: 400 (missing fields), 404/409/410 same as send-draft, 502 (Gmail error).

**POST `/api/trash-message`** — body: `{ message_id: string }`
- Reserved for future undo feature. Calls `messages.trash`. Not called from UI in v1.
- Status codes: 400 (missing id), 401 (auth), 502 (Gmail error).

## Supabase patterns

- All client-side reads/writes go through the singleton exported from `src/lib/supabase.ts`.
- Inserts always set `stage: "new"` and `reply_status: "no_reply"`.
- **Contact list query** (keyset pagination, PAGE_SIZE=30):
  ```ts
  supabase.from("contacts")
    .select(LIST_COLUMNS)     // explicit column list — NOT select("*"). Heavy text fields
                              // (detail, job_description, job_title) excluded from list view.
    .is("deleted_at", null)   // soft delete filter — always include
    // ...optional filter methods (.or, .in, .eq, .lt)...
    .order("created_at", { ascending: false })
    .limit(30)                // limit always LAST — after all filters
  ```
  Do not call `.limit()` before `.or()` / `.in()` / `.lt()` — the chain must resolve at `.limit()`.
- **Full-record fetch on row click** (`openContact` in `ContactsList.tsx`): after setting `selectedContact` with list-column data, a second `select("*").eq("id", id).single()` call fetches all fields for the side sheet. This keeps the list fast while the sheet gets the complete record (detail, job_description, etc.). If you add a new field that's needed in the list view, add it to `LIST_COLUMNS`. If it's only needed in the sheet, leave it out.
- **Soft delete**: `supabase.from("contacts").update({ deleted_at: new Date().toISOString() }).eq("id", id)`. Never hard-delete.
- **Optimistic updates** (stage/tier changes): mutate local state first, then issue the
  Supabase update. On error, revert local state and call `onError`.
- **Text field blur-save in the sheet**: all editable text fields (name, email, company, role, detail, resume_url, job_title, notes) save on blur via `handleBlurSave(field, localValue, label, revert)` in `ContactsList.tsx`. The `revert` callback restores the local React state on error. Mode and dartmouth save immediately on click (`handleModeChange`, `handleDartmouthChange`) using the same optimistic pattern as `handleTierChange`.
- The contacts table has RLS **disabled**. The anon key can read/write all columns. Keep
  in mind when adding new columns — no RLS policy changes needed but also no row-level
  security.

## Color and stage display rules (kept in sync with the Python agent)

Stage display uses Badge variants in `ContactsList.tsx::stageVariant`:
- `_drafted` suffix → amber Badge
- `_sent` suffix → indigo Badge
- `positive_reply`, `engaged`, `_replied` → emerald Badge
- `closed`, `bounced`, `unsubscribed` → muted Badge
- Otherwise → default Badge

Tier display: T1 → indigo, T2 → default, T3 → muted.

If you add a stage, update `OUTREACH_STAGES` / `APPLIED_STAGES` in `types.ts` and
`STAGE_LABELS` map in `ContactsList.tsx`.

## Tests (Vitest)

- **Every code change must ship with tests.** New functions get a test file or new cases in the nearest existing test file. Bug fixes get a regression test that would have caught the bug. No exceptions for "trivial" changes — if it's worth changing, it's worth a test.
- **Zero tolerance for failing tests.** `npm test` must show 0 failures before every commit. If a test fails — even one that was already failing before your change — fix it. Never ship with a known failure and never describe failures as "pre-existing" or "unrelated". A broken test suite is a broken codebase.
- **Fix → rerun → confirm green.** After fixing a failing test, always rerun the full suite (`npm test`) to confirm no regressions were introduced by the fix itself.
- Run: `npm test`. Watch: `npm run test:watch`.
- Test files colocate with source: `Foo.test.tsx` next to `Foo.tsx`.
- Vitest config: `vitest.config.ts` (jsdom + React plugin + `@/` alias).
- Setup file: `vitest.setup.ts` — registers jest-dom, stubs env vars, installs a plain-
  function IntersectionObserver mock (NOT `vi.fn()` — see below). Also stubs `localStorage`
  because Node.js 22 exposes a native `localStorage = undefined` that shadows jsdom's.

### Mocking conventions

**Supabase chain mock** — the new query builder calls methods in a specific order and
`.limit()` must be the terminal resolver. Use a shared `readChain` object:
```ts
const { limitMock, updateEqMock } = vi.hoisted(() => ({
  limitMock: vi.fn(),
  updateEqMock: vi.fn(),
}));

vi.mock("@/lib/supabase", () => {
  const readChain: Record<string, unknown> = {};
  for (const m of ["is", "order", "or", "in", "eq", "lt"]) {
    readChain[m] = vi.fn(() => readChain);
  }
  readChain.limit = limitMock;
  return {
    supabase: {
      from: vi.fn(() => ({
        select: vi.fn(() => readChain),
        update: vi.fn(() => ({ eq: updateEqMock })),
      })),
    },
  };
});
```

**IntersectionObserver mock** — must be a plain function (not `vi.fn()`). `vi.restoreAllMocks()`
in afterEach will reset a `vi.fn()` implementation, breaking tests that capture the IO
callback. Install a plain function in `vitest.setup.ts` and override with another plain
function per-test if you need to capture the callback:
```ts
// vitest.setup.ts — base stub
global.IntersectionObserver = function() {
  return { observe() {}, disconnect() {}, unobserve() {} };
} as unknown as typeof IntersectionObserver;

// In individual test file — capturing version
let ioCallback: (...) => void;
global.IntersectionObserver = function(cb) {
  ioCallback = cb;
  return { observe() {}, disconnect() {}, unobserve() {} };
} as unknown as typeof IntersectionObserver;
```

**Radix / Vaul mocks** — these use portals. In tests that render components using Sheet
or ConfirmModal, mock the primitives so they render into the jsdom body without portal
quirks. See `ContactsList.test.tsx` for complete Vaul + Radix Dialog + Radix Select mock
examples.

**Sonner mock:**
```ts
vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
  Toaster: () => null,
}));
```

- Variables used inside `vi.mock()` factories must be hoisted with `vi.hoisted()`.
- Reset mocks in `beforeEach`, not afterEach.
- **App shell test** (`App.test.tsx`): always assert that persistent nav links (e.g.
  "Overview", "Prompts", "Activity") exist with the correct `href`. When rewriting App.tsx,
  verify this test still passes before committing.

## Tests (Playwright e2e)

- **MANDATORY before every push**: `npm run test:e2e` must pass before pushing any UI change to GitHub. A Claude Code PreToolUse hook enforces this automatically — it runs the full suite before every `git push` and blocks the push if any test fails. All tests must be green.
- **Zero tolerance for failing e2e tests.** Same rule as Vitest: if any test fails, fix it and rerun the full suite before pushing. Never push with a failing e2e test, even if it was failing before your change.
- **Write e2e tests for every UI change.** For any change that affects what the user sees or interacts with, add a Playwright test that exercises the changed flow. Assertions alone are not enough — take a screenshot with `page.screenshot()` and visually verify the result before committing.
- **Verify screenshots.** After capturing a screenshot in a test, read the image and confirm it shows the correct UI. Do not claim a UI change is correct without having looked at the screenshot. Silent test passes do not prove correct visual output.
- Run: `npm run test:e2e`.
- Tests live in `tests/e2e/`. Files run alphabetically (00–). Update the count in this file when adding new spec files.
- **Current test count: 48** (as of `12-replies.spec.ts`; vitest: 282 passed, playwright: 48 passed).
- **Network interception**: use `mockSupabase(page)` from `tests/e2e/helpers.ts` in
  `beforeEach`. This installs `page.route()` handlers that intercept Supabase REST calls
  and return fixture data. Does NOT require env var changes or clearing `.next/cache`.
- Fixtures: `tests/e2e/fixtures/contacts.json` (50 rows) and `prompts.json`.
- Do NOT hit the real Supabase from e2e tests.
- Do NOT change `NEXT_PUBLIC_SUPABASE_URL` for e2e runs — `page.route()` intercepts at
  the browser level regardless of which URL is compiled into the bundle.
- **Shell regression test**: `00-shell.spec.ts` runs first and asserts top-level chrome
  (heading, nav links, mode buttons). If you add a new persistent nav element, add an
  assertion here. If you remove one intentionally, update this test.

## When changing things

- **Don't introduce server actions** for inserts/updates.
- **Don't add a service-role key path.**
- **Don't introduce a state-management library.**
- **Don't break the `/api/extract` JSON contract** without updating `ExtractedContact` in
  `types.ts`.
- **Don't hard-delete contacts.** Always soft delete via `deleted_at`.
- **Don't introduce shadcn/ui** or any component library. The in-house `src/components/ui/`
  primitives are intentional and sufficient.
- **Don't add a new CSS file.** Extend `globals.css`.
- **Always include `.is("deleted_at", null)`** in any query that reads the contacts list.

## Build / deploy

- `npm run build` — typecheck + production build. Must pass.
- `npm test` — Vitest unit tests. Must pass. **0 failures required — no exceptions.**
- `npm run test:e2e` — Playwright smoke tests. Must pass. **0 failures required — no exceptions.**
- Deploy from **repo root** (not from `contact-manager/`): the Vercel project root is configured
  as `contact-manager/` so the CLI must be run one level up. Because the repo-root `.vercel/`
  links to a different project, always specify the project ID explicitly:
  ```
  VERCEL_PROJECT_ID=prj_Vf7rorfOlTiNHB5xKFcybUKf0ysV VERCEL_ORG_ID=team_BynuvJ8k5TWQEFKW0kvh046u vercel deploy --prod
  ```
  Running `vercel deploy --prod` from inside `contact-manager/` fails (path resolves to
  `contact-manager/contact-manager`). Running without the env vars from repo root deploys
  the wrong project.
- Env vars (public): `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY`.
- Env vars (server-only): `ANTHROPIC_API_KEY`, `GITHUB_DISPATCH_TOKEN`,
  `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET`, `GOOGLE_OAUTH_REFRESH_TOKEN`.
- `GITHUB_DISPATCH_TOKEN` must have `actions: write` on the agent repo.
- Gmail OAuth vars: run `cd contact-manager && npx tsx scripts/capture-gmail-token.mts` once
  to obtain `GOOGLE_OAUTH_REFRESH_TOKEN`. Must run from `contact-manager/` (dotenv and googleapis
  are in that directory's node_modules, not the repo root). Script uses `http://localhost:8080`
  redirect — Desktop app OAuth clients allow this without GCP Console config.
  Add all three vars to `.env` and Vercel dashboard.

## /queue page (Phase 1 — bulk-send UI)

Three-column layout: left rail (filters), center (scrollable draft list), right (focused detail + action bar).

**Data fetch**: two-step on mount + 30s auto-refresh.
1. Contacts `IN QUEUE_STAGES` (from `cadence.ts`) AND `deleted_at IS NULL`, sorted tier ASC + created_at DESC.
2. draft_history rows for those contacts with `sent_body IS NULL`, latest per contact.

**Focus tracking**: focus is tracked by `contact_id`, not index. On auto-refresh, if the focused contact is still present its position is restored. If it disappeared, focus resets to index 0.

**5-second undo pattern** (used for both Approve and Send and Mark Dead):
- API fires AFTER the 5-second delay. Optimistically remove from list → show toast with Undo button → start `setTimeout(5000)`.
- Undo: `clearTimeout`, re-insert at `originalIndex`, toast.info("Send canceled"). API never called.
- Timer fires: POST `/api/send-draft` (or Supabase PATCH for dead). On error: re-add to list + error toast.
- Unmount: all pending timers are cleared — navigating away cancels pending sends (deliberate trade-off, documented in comment).
- Concurrent: multiple contacts can have pending timers simultaneously; each is tracked separately in `pendingSends` Map.

**Quick Fix mode** (`E` key): replaces subject/body in right column with editable inputs. "Save and Send" calls `/api/update-draft` first, then enters the 5s undo flow.

**Keyboard map** (document-level listener; early-returns when input/textarea is focused):

| Key | Action |
|---|---|
| `j` / `↓` | Next draft |
| `k` / `↑` | Previous draft |
| `g g` | Jump to top (500ms window between presses) |
| `G` | Jump to bottom |
| `e` | Approve and Send (5s undo) |
| `E` | Open Quick Fix |
| `o` | Edit in Gmail (new tab, `/u/0/#drafts?compose=<id>`) |
| `x` | Skip (session-only, survives 30s auto-refresh) |
| `D` | Mark dead (uppercase D — 5s undo) |
| `1`/`2`/`3` | Toggle tier filter |
| `?` | Show keyboard shortcuts overlay |
| `Esc` | Close Quick Fix → clear filters |

**Signals in right column**:
- Critic: queryable from `agent_events` (event_type='critic', metadata.score/verdict/retried). T2+ shows "n/a (T2+)".
- Pre-flight: shows "✓ passed" inferred from draft existence (no logged event for passing preflight — only blocked events exist).
- Edited in Gmail: always "—" in v1 (`draft_history.edit_detected` is null until send; per-row API call too expensive).

## /replies page (Phase 2 — reply triage UI)

Two-column layout: left (320px scrollable triage list), right (focused detail + action bar).

**Data fetch**: three-step on mount + 30s auto-refresh.
1. Contacts where `classifier_status IS NOT NULL` AND `reply_status NOT IN (interested,call_scheduled,dead)` AND `deleted_at IS NULL`.
2. `draft_history` rows with `stage='reply_drafted'` AND `sent_body IS NULL`, latest per contact.
3. `email_messages` rows with `direction='incoming'`, latest per contact (for left-list snippet + timestamp).

**Sort**: client-side — positive_reply first (priority 0), soft_yes second (priority 1), others last, then created_at DESC within each group.

**Left list rows**: classifier dot+label (emerald=positive, amber=soft_yes, gray=others), name+company, stripped incoming snippet (quoted lines/headers removed, max 80 chars), relative timestamp.

**Right column**: contact header + classifier badge, ThreadView (self-contained, fetches own data per contactId), suggested reply block (subject+body, only for positive/soft_yes with draft), action bar.

**Action bar** — with draft (positive/soft_yes):
[Approve and Send] [Quick Fix] [Edit in Gmail] | [Mark interested] [Mark call scheduled] [Mark dead]

**Action bar** — without draft (hard_no, unrelated, etc.):
[Open in Gmail] | [Mark interested] [Mark call scheduled] [Mark dead]

**5-second undo**: same pattern as QueuePage. `pendingActions: Map<contact_id, PendingEntry>`. Both "Approve and Send" and "mark reply_status" changes use this pattern. Reply_status changes: Supabase PATCH fires after 5s; undo reverts optimistic removal from list.

**Important**: `reply_status` updates (i/c/D) do NOT touch `stage` — stage is managed manually via the contacts side sheet on /contacts.

**Keyboard map** (same early-return pattern as QueuePage):

| Key | Action |
|---|---|
| `j` / `↓` | Next reply |
| `k` / `↑` | Previous reply |
| `e` | Approve and Send (only when draft exists) |
| `E` | Quick Fix (only when draft exists) |
| `o` | Edit in Gmail (draft) or Open Gmail inbox (no draft) |
| `i` | Mark interested (5s undo) |
| `c` | Mark call scheduled (5s undo) |
| `D` | Mark dead (5s undo, uppercase D) |
| `?` | Keyboard shortcuts overlay |
| `Esc` | Close Quick Fix |

**ThreadView**: reused as-is — `<ThreadView contactId={focused.id} />`. It fetches its own email_messages per contactId on mount/change. No additional data plumbing needed.

## Mirrored cadence constants

`src/lib/cadence.ts` mirrors `agent/config.py::FOLLOWUP_DAYS`. If the agent cadence
changes (days between emails), update **both** files. Same pattern as `REPLY_STAGES`
in `types.ts` mirroring `constants.py`. The `STAGE_TRANSITIONS` map in `cadence.ts`
is the authoritative transition table for all `/api/send-draft` stage flips.
`QUEUE_STAGES` (in `cadence.ts`) is used by `/queue` page to filter contacts for bulk approval.

## Style: comments and docs

- Don't add comments that restate the code.
- Do add a short comment for non-obvious workarounds (e.g., why `vi.hoisted` is needed,
  why `.limit()` must be last in the query chain).
- Don't write docstrings on tiny helper components.

## New tables (Phase 0 — 2026-05-20)

**`draft_history`** — lifecycle of every Gmail draft created by the agent.
- `gmail_draft_id TEXT` — Gmail API draft ID, required by `/api/send-draft`.
- `subject, body` — draft content as generated. Updated by `/api/update-draft` on Quick Fix edits.
- `sent_subject, sent_body, sent_at, edit_detected` — populated by `/api/send-draft` after send.
- `edit_detected = true` when the sent body differs from the draft body (user edited in Gmail).
- RLS disabled. Access via `/api/send-draft` and `/api/update-draft`; read by `/queue` page.
- Written by Python `db.log_drafted_email()` — called from `agent._execute_draft` and `reply_drafter.draft_reply`.

## New tables (Sprint 2 — 2026-05-16)

Two new Supabase tables are readable by the frontend. Both have RLS disabled.

**`email_messages`** — outgoing and incoming emails per contact, written by the Python agent and monitor.
- `direction`: `"outgoing"` | `"incoming"`
- `sent_at`: timestamptz, used for chronological ordering
- Used by `ThreadView.tsx` in the Vaul side sheet.
- Query pattern: `.from("email_messages").select("*").eq("contact_id", id).order("sent_at", { ascending: true })` — no `.limit()`, `.order()` is terminal.

**`agent_events`** — per-action audit log written by the Python agent/monitor (preflight blocks, reply classification, draft creation).
- `status`: `"success"` | `"failed"` | `"blocked_preflight"` | `"running"`
- `event_type`: `"preflight"` | `"classify_reply"` | `"draft_reply"` | `"critic"` | `"sent_detection"`
- Used by `/runs` page (`app/runs/page.tsx`).
- Query pattern: `.from("agent_events").select("*").order("started_at", { ascending: false }).limit(100)` — `.limit()` is terminal.
- Badge query (7-day failures): `.select("id", { count: "exact", head: true }).in("status", [...]).gte("started_at", since)` — this returns a thenable, not a limit-terminated chain.

## New types (types.ts)

- `Contact.classifier_status: string | null` — auto-set by monitor; never by user. Distinct from `reply_status` (user-managed).
- `REPLY_STAGES = ["reply_drafted", "reply_sent"]` — **mirrored constant**: must also be updated in Python `constants.py` if changed.
- `ContactsQueryFilters.needsResponseOnly: boolean` — filter: `classifier_status IN (positive_reply, soft_yes) AND reply_status NOT IN (interested, call_scheduled, dead)`.
- `EmailMessage` type — mirrors `email_messages` table.
- `AgentEvent` type — mirrors `agent_events` table.

## ThreadView component

`ThreadView.tsx` renders inside the Vaul side sheet in `ContactsList.tsx`. It fetches `email_messages` for the selected contact on mount. Outgoing messages are right-aligned indigo; incoming are left-aligned muted. Bodies >300 chars are truncated with an expand toggle.

**Testing**: `ContactsList.test.tsx` mocks `ThreadView` to `null` to avoid triggering the `email_messages` Supabase query inside the test's mock chain:
```ts
vi.mock("@/components/ThreadView", () => ({ ThreadView: () => null }));
```
The `ThreadView.test.tsx` file provides its own isolated mock of the supabase chain.

## /runs page (app/runs/page.tsx)

Client component. Fetches `agent_events` on mount and every 10 seconds via `setInterval`. Status filter chips (All / Success / Failed / Blocked) filter the in-memory list. Shows a 7-day failure badge in the header. Empty state when no events. Route: `/runs`, heading reads "Activity".

**Mocking in tests**: the `agent_events` list query uses `limitMock` as the terminal call; the count (badge) query uses a thenable `countChain`. These are separate chains distinguished by whether `select()` receives `{ count: "exact" }`.

## /prompts page (app/prompts/page.tsx)

Client component (`PromptsPage.tsx`). Fetches all prompts ordered by `sort_order`. Sticky search (title + description). 7 collapsible categories via `PROMPT_CATEGORY_MAP` in `src/lib/promptCategories.ts`; unknown keys fall into "Shared". Only "Sender & Core" open by default; state persists in `localStorage` (`"prompts-open-categories"`). localStorage read in `useEffect` post-mount (SSR-safe skeleton pattern). `PromptCategory.tsx` is the collapsible section wrapper; `PromptSection.tsx` (individual card) unchanged.

**Categorization drift:** When adding new prompt rows to Supabase, add the key to `promptCategories.ts` — omitted keys silently land in "Shared".

**Placeholder validation:** `PromptSection.tsx` calls `getUnknownVariables(prompt.key, draft)` from `src/lib/promptVariables.ts` on every render. Any `{placeholder}` that Python's `.format()` would try to fill but the code never provides triggers an amber inline warning. `PROMPT_VALID_KEYS` in `promptVariables.ts` mirrors `agent._PROMPT_VALID_KEYS` in Python — **keep both in sync** when adding a new prompt or changing a `tpl.format(...)` call site.

**Locked prompt:** `/api/extract` prompt (`route.ts`) is hardcoded, not in the prompts table. Bound to `ExtractedContact` JSON schema — editing it requires a `types.ts` update and code deploy in sync.

## e2e helpers update

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
