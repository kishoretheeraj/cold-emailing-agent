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
│   ├── api/extract/route.ts
│   ├── api/trigger-agent/route.ts
│   ├── api/send-draft/route.ts
│   ├── api/update-draft/route.ts
│   ├── api/trash-message/route.ts
│   ├── overview/page.tsx
│   ├── prompts/page.tsx
│   ├── queue/page.tsx
│   ├── replies/page.tsx
│   ├── runs/page.tsx
│   ├── globals.css
│   ├── layout.tsx
│   └── page.tsx
├── components/
│   ├── ui/
│   │   ├── Badge.tsx
│   │   ├── Skeleton.tsx
│   │   ├── EmptyState.tsx
│   │   ├── Tooltip.tsx
│   │   ├── Select.tsx
│   │   ├── Sheet.tsx
│   │   └── ConfirmModal.tsx
│   ├── App.tsx
│   ├── AppProviders.tsx
│   ├── SmartInput.tsx
│   ├── StructuredForm.tsx
│   ├── ContactsList.tsx
│   ├── ContactsFilters.tsx
│   ├── ThreadView.tsx
│   ├── PromptsPage.tsx
│   ├── PromptCategory.tsx
│   ├── PromptSection.tsx
│   ├── QueuePage.tsx
│   ├── RepliesPage.tsx
│   └── Field.tsx
└── lib/
    ├── supabase.ts
    ├── gmail-server.ts
    ├── cadence.ts
    ├── personalization.ts
    ├── promptCategories.ts
    ├── promptVariables.ts
    └── types.ts
tests/
└── e2e/
    ├── helpers.ts
    ├── fixtures/
    └── *.spec.ts
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

`REPLY_STAGES` (`reply_drafted`, `reply_sent`) render in a separate "Reply" SelectGroup
in the stage dropdown — distinct from Outreach and Applied groups. If adding new reply
stages, update all three: `REPLY_STAGES` in `types.ts`, `STAGE_LABELS` in
`ContactsList.tsx`, and the Reply SelectGroup in the stage `<Select>` inside
`ContactsList.tsx`. **Do not add reply stages to `OUTREACH_STAGES` or `APPLIED_STAGES`.**

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

See docs/testing/mocking.md for mocking conventions (Supabase chain, IntersectionObserver, Radix/Vaul, Sonner).

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
- **Deploy is automatic** — pushing to `main` triggers Vercel auto-deploy via the GitHub
  integration. Vercel project `rootDirectory` is set to `contact-manager` and
  `commandForIgnoringBuildStep` is `git diff HEAD^ HEAD --quiet -- contact-manager/`, so
  pushes that only touch Python files (agent.py, monitor.py, etc.) are skipped — only
  `contact-manager/` changes trigger a build.
- If a **manual deploy** is ever needed, run from repo root with project ID override:
  ```
  VERCEL_PROJECT_ID=prj_Vf7rorfOlTiNHB5xKFcybUKf0ysV VERCEL_ORG_ID=team_BynuvJ8k5TWQEFKW0kvh046u vercel deploy --prod
  ```
  Running `vercel deploy --prod` from inside `contact-manager/` still fails (path resolves to
  `contact-manager/contact-manager`); always run from repo root.
- Env vars (public): `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY`.
- Env vars (server-only): `ANTHROPIC_API_KEY`, `GITHUB_DISPATCH_TOKEN`,
  `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET`, `GOOGLE_OAUTH_REFRESH_TOKEN`.
- `GITHUB_DISPATCH_TOKEN` must have `actions: write` on the agent repo.
- Gmail OAuth vars: run `cd contact-manager && npx tsx scripts/capture-gmail-token.mts` once
  to obtain `GOOGLE_OAUTH_REFRESH_TOKEN`. Must run from `contact-manager/` (dotenv and googleapis
  are in that directory's node_modules, not the repo root). Script uses `http://localhost:8080`
  redirect — Desktop app OAuth clients allow this without GCP Console config.
  Add all three vars to `.env` and Vercel dashboard.

See docs/routes/queue.md for /queue page details and cadence constants.

See docs/routes/replies.md for /replies page details.


## Style: comments and docs

- Don't add comments that restate the code.
- Do add a short comment for non-obvious workarounds (e.g., why `vi.hoisted` is needed,
  why `.limit()` must be last in the query chain).
- Don't write docstrings on tiny helper components.

See docs/schema/tables.md for table schemas (draft_history, email_messages, agent_events) and new types.

See docs/components/thread-view.md for ThreadView details and test mock pattern.

See docs/routes/runs.md for /runs page details.

See docs/routes/prompts.md for /prompts page details.

See docs/testing/e2e-helpers.md for e2e helper (mockSupabase) details.
