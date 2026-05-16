# Cold Email Ops — Contact Manager

A Next.js web app for managing the contacts that feed into the [cold-emailing-agent](https://github.com/kishoretheeraj/cold-emailing-agent) pipeline.

## What it does

- **Add contacts** via Smart Input (paste raw text, Claude extracts fields) or a Structured Form
- **Browse contacts** with infinite-scroll pagination, full-text search, and tier/mode/stage filters
- **Update status** — change stage and reply status optimistically from a Vaul side sheet
- **Soft delete** contacts (recoverable; preserves draft history)

The Python agent reads from the same Supabase table every morning at 8am and drafts Gmail emails for any contacts in a draftable stage.

## Stack

| Layer | Technology |
|---|---|
| Framework | Next.js 16 + React 19 (App Router) |
| Styling | Tailwind CSS v4 |
| Database | Supabase (shared with the agent) |
| Toasts | Sonner |
| Side sheet | Vaul (`direction="right"`) |
| Primitives | Radix UI (Tooltip, Select, Dialog) |
| Icons | Lucide React |
| Unit tests | Vitest + Testing Library (82 tests) |
| E2E tests | Playwright (6 smoke tests) |
| AI extraction | Anthropic Claude via `/api/extract` |

## Setup

```bash
npm install
```

Create `.env.local`:

```
NEXT_PUBLIC_SUPABASE_URL=your_supabase_url
NEXT_PUBLIC_SUPABASE_ANON_KEY=your_anon_key
ANTHROPIC_API_KEY=your_anthropic_key
```

```bash
npm run dev
```

## Tests

```bash
npm test            # Vitest unit tests (82 tests)
npm run test:e2e    # Playwright e2e smoke tests (6 tests)
npm run test:all    # both
```

## Deploy

Connect this repo to Vercel and set the three env vars in the Vercel dashboard.

## Related

- [cold-emailing-agent](https://github.com/kishoretheeraj/cold-emailing-agent) — Python automation that reads this Supabase table and drafts Gmail emails daily
