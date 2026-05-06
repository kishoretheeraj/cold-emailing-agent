@AGENTS.md

# Cold Email Ops — Contact Manager

A small Next.js app for adding contacts to the cold-email Supabase table that
the Python agent reads every morning. Two input modes (Smart Input / Structured
Form), a 20-row contacts list, and a side panel for status updates.

When working in this repo, follow the rules below. They reflect how the code
was actually written, not just style preferences.

---

## Stack and versions

- **Next.js 16** with the App Router. (Treat anything from earlier Next docs
  with suspicion — see `AGENTS.md`.)
- **React 19**. Use `"use client"` for any component that touches state, refs,
  effects, or browser APIs.
- **Tailwind CSS v4**. Theme tokens go in `@theme {}` inside `globals.css`,
  **not** in `tailwind.config.js`.
- **TypeScript strict mode**. Don't use `any` — use `unknown` and narrow.
- **@supabase/supabase-js** in the browser; no service role key in the client.
- **@anthropic-ai/sdk** server-side only, behind `/api/extract`.

## Module layout

```
src/
├── app/
│   ├── api/extract/route.ts   # Server-only Claude POST handler
│   ├── globals.css             # @theme tokens + global resets
│   ├── layout.tsx              # Inter font, dark theme
│   └── page.tsx                # 1-line server component → <App />
├── components/                 # All client components live here
│   ├── App.tsx                 # Top-level shell + toast + refresh state
│   ├── SmartInput.tsx          # Paste → /api/extract → editable preview → save
│   ├── StructuredForm.tsx      # Two form sections: outreach + applied
│   ├── ContactsList.tsx        # Last 20 rows + side panel + status update
│   ├── Field.tsx               # Reusable Label/TextInput/TextArea/Toggle/Tier
│   └── Toast.tsx               # 4-second auto-dismissing notification
└── lib/
    ├── supabase.ts             # Anon-key browser client
    └── types.ts                # Contact + ReplyStatus + stage arrays
```

## Coding conventions

- **Server vs. client.** Default to server components. `page.tsx` is a server
  component that just imports the client `App`. Client components must start
  with `"use client";` on line 1 (no blank line above).
- **No emojis in shipped code or copy.** This applies to UI text, console
  logs, and comments.
- **No em dashes inside email-related copy or prompts.** This is enforced by
  the agent's prompt rules, mirror it here.
- **Tailwind utility classes only** — no inline `style` props except the
  side-panel pill colors which use `color-mix(in srgb, ...)` for subtle
  alpha blending. Do not introduce CSS modules or styled-components.
- **Theme tokens.** Use the custom palette: `bg-bg`, `bg-surface`,
  `bg-surface-2`, `border-border`, `border-border-strong`, `text-fg`,
  `text-fg-muted`, `text-fg-dim`, plus the indigo scale for the primary.
  These are defined in `globals.css`.
- **No new top-level CSS files.** Extend `globals.css` if needed.
- **No additional state libraries** (Redux, Zustand, etc.). Local `useState`
  + prop drilling is enough for this app.

## Component patterns

- Forms use the helper components in `Field.tsx` (`Label`, `TextInput`,
  `TextArea`, `ToggleSwitch`, `TierSelector`). Don't reinvent these.
- Required fields show a `*` via `<Label required>...</Label>`.
- Validation runs on the click handler (not on blur). On failure, call the
  `onError` prop instead of throwing — toasts render through `App`.
- After a successful insert, call `onAdded()` so `App` bumps `refreshKey`
  and the contacts list re-fetches.

## API route conventions

- Server-only routes live in `src/app/api/<name>/route.ts`.
- Use `export const runtime = "nodejs"` (Anthropic SDK requires Node).
- Never expose `ANTHROPIC_API_KEY` to the browser. Anything that uses it
  must go through a server route.
- Validate the body shape and return `400` for missing input, `502` if a
  downstream service returns malformed data, `500` for unexpected SDK errors.
- Strip ` ```json` code fences from Claude responses before `JSON.parse`.

## Supabase patterns

- All client-side reads/writes go through the singleton `supabase` client
  exported from `src/lib/supabase.ts`.
- Inserts always set `stage: "new"` and `reply_status: "no_reply"`.
- The contacts list reads with `.order("created_at", { ascending: false }).limit(20)`.
- The side-panel update writes `.update({ stage, reply_status }).eq("id", id).select().single()`
  to get the updated row back for optimistic local state.

## Color and stage display rules (kept in sync with the Python agent)

- Stage `new` → grey
- Stage contains `_drafted` → blue
- Stage contains `_sent` → green
- Stage `closed` → dim grey
- `reply_status` ∈ {`replied`, `interested`, `call_scheduled`} → bright green
- `reply_status === "dead"` → dim red

These map to CSS custom properties in `globals.css`. If you add a stage,
update both `OUTREACH_STAGES` / `APPLIED_STAGES` in `src/lib/types.ts` and
the color rules in `ContactsList.tsx::stageStyles`.

## Tests (Vitest)

- Run with `npm test`. Watch mode: `npm run test:watch`.
- Test files live next to the code: `Foo.test.tsx` next to `Foo.tsx`.
- Vitest config: `vitest.config.ts` (jsdom + React plugin + `@/` alias).
- Setup: `vitest.setup.ts` registers `@testing-library/jest-dom` and stubs
  the three env vars.
- Test files are excluded from the production `tsconfig.json` build but
  included in `tsconfig.test.json` for IDE support.

### Mocking conventions

- Mock the supabase client at the import boundary:
  ```ts
  vi.mock("@/lib/supabase", () => ({
    supabase: { from: vi.fn(() => ({ insert: insertMock })) },
  }));
  ```
- Mock the Anthropic SDK as a class via `vi.hoisted`:
  ```ts
  const { mockCreate } = vi.hoisted(() => ({ mockCreate: vi.fn() }));
  vi.mock("@anthropic-ai/sdk", () => ({
    default: class FakeAnthropic { messages = { create: mockCreate }; },
  }));
  ```
- Mock `fetch` per-test with `vi.spyOn(global, "fetch").mockResolvedValueOnce(...)`.
- Reset mocks in `beforeEach`.

### Coverage expectations

- New form fields require validation tests in the relevant form component test.
- New stage values require an entry in `types.test.ts`'s sanity checks.
- New API routes require a `route.test.ts` covering: valid input, malformed
  input (400), upstream failure (5xx), and downstream parse failure (502).

## When changing things

- **Don't introduce server actions** for inserts/updates. The current pattern
  uses the supabase-js client from the browser with the anon key. Switching
  would require auth/RLS changes that are out of scope.
- **Don't add a service-role key path.** The anon key + RLS is the security
  model.
- **Don't introduce a state-management library.** If state-sharing gets
  awkward, lift state to `App.tsx` first.
- **Don't break the `/api/extract` JSON contract** without updating the
  `ExtractedContact` type in `src/lib/types.ts` and the SmartInput preview
  card.

## Build / deploy

- `npm run build` runs typecheck + production build. Must pass before deploy.
- `vercel deploy --prod` to deploy. Env vars are stored in Vercel dashboard.
- Three env vars: `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY`,
  `ANTHROPIC_API_KEY`. The first two are intentionally public (RLS protects
  the data); the third is server-only.

## Style: comments and docs

- Don't add comments that restate the code.
- Do add a short comment when documenting a non-obvious workaround (e.g.,
  why `vi.hoisted` is necessary in route.test.ts).
- Don't write docstrings on tiny helper components.
