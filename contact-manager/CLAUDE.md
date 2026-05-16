@AGENTS.md

# Cold Email Ops — Contact Manager

A Next.js app for adding contacts to the cold-email Supabase table that the Python agent
reads every morning. Two input modes (Smart Input / Structured Form), an infinite-scroll
contacts list with search/filter, and a Vaul side sheet for status updates and soft delete.

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
│   ├── api/extract/route.ts   # Server-only Claude POST handler
│   ├── globals.css             # @theme tokens + global resets + Vaul overrides
│   ├── layout.tsx              # Inter font, dark theme, AppProviders wrapper
│   └── page.tsx                # 1-line server component → <App />
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
│   ├── ContactsFilters.tsx     # Search input + tier/mode pills + stage select + dartmouth
│   └── Field.tsx               # Label / TextInput / TextArea / ToggleSwitch / TierSelector
└── lib/
    ├── supabase.ts             # Anon-key browser client singleton
    └── types.ts                # Contact + ReplyStatus + stage arrays + filter types
tests/
└── e2e/                        # Playwright smoke tests (6 critical-path tests)
    ├── helpers.ts              # mockSupabase() — page.route() Supabase interception
    ├── fixtures/               # contacts.json (50 rows) + prompts.json
    └── *.spec.ts               # 01-list-loads through 06-drafted-delete-warning
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

## Supabase patterns

- All client-side reads/writes go through the singleton exported from `src/lib/supabase.ts`.
- Inserts always set `stage: "new"` and `reply_status: "no_reply"`.
- **Contact list query** (keyset pagination, PAGE_SIZE=30):
  ```ts
  supabase.from("contacts")
    .select("*")
    .is("deleted_at", null)   // soft delete filter — always include
    // ...optional filter methods (.or, .in, .eq, .lt)...
    .order("created_at", { ascending: false })
    .limit(30)                // limit always LAST — after all filters
  ```
  Do not call `.limit()` before `.or()` / `.in()` / `.lt()` — the chain must resolve at `.limit()`.
- **Soft delete**: `supabase.from("contacts").update({ deleted_at: new Date().toISOString() }).eq("id", id)`. Never hard-delete.
- **Optimistic updates** (stage/tier changes): mutate local state first, then issue the
  Supabase update. On error, revert local state and call `onError`.
- **Notes autosave**: save on blur (`onBlur`). No debounce needed for single-event triggers.
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

- Run: `npm test`. Watch: `npm run test:watch`.
- Test files colocate with source: `Foo.test.tsx` next to `Foo.tsx`.
- Vitest config: `vitest.config.ts` (jsdom + React plugin + `@/` alias).
- Setup file: `vitest.setup.ts` — registers jest-dom, stubs env vars, installs a plain-
  function IntersectionObserver mock (NOT `vi.fn()` — see below).

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

## Tests (Playwright e2e)

- Run: `npm run test:e2e`.
- Tests live in `tests/e2e/`. Six critical-path smoke tests only.
- **Network interception**: use `mockSupabase(page)` from `tests/e2e/helpers.ts` in
  `beforeEach`. This installs `page.route()` handlers that intercept Supabase REST calls
  and return fixture data. Does NOT require env var changes or clearing `.next/cache`.
- Fixtures: `tests/e2e/fixtures/contacts.json` (50 rows) and `prompts.json`.
- Do NOT hit the real Supabase from e2e tests.
- Do NOT change `NEXT_PUBLIC_SUPABASE_URL` for e2e runs — `page.route()` intercepts at
  the browser level regardless of which URL is compiled into the bundle.

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
- `npm test` — Vitest unit tests. Must pass.
- `npm run test:e2e` — Playwright smoke tests. Must pass.
- `vercel deploy --prod` to deploy. Env vars in Vercel dashboard.
- Three env vars: `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY`,
  `ANTHROPIC_API_KEY`. The first two are public (no RLS — be aware). Third is server-only.

## Style: comments and docs

- Don't add comments that restate the code.
- Do add a short comment for non-obvious workarounds (e.g., why `vi.hoisted` is needed,
  why `.limit()` must be last in the query chain).
- Don't write docstrings on tiny helper components.
