# Cold Email Ops — Contact Manager

A Next.js app that adds contacts to the Supabase table the Python cold-email
agent reads every morning at 8am EST.

**Live:** https://contact-manager-steel-eight.vercel.app

## What it does

- **Smart Input mode** — paste a LinkedIn bio, JD, or casual description.
  Claude extracts structured fields and shows an editable preview before save.
- **Structured Form mode** — separate sections for outreach contacts and
  hiring-manager (applied) contacts.
- **Contacts list** — last 20 added, with a side panel for stage and
  `reply_status` updates. Color-coded by stage and reply state.

## Stack

- Next.js 16 (App Router) + React 19
- Tailwind CSS v4 with custom dark theme (indigo primary)
- `@supabase/supabase-js` for client-side reads and writes
- `@anthropic-ai/sdk` server-only via `/api/extract` route
- Vitest + React Testing Library + jsdom for tests

## Run locally

```bash
npm install
cp .env.example .env.local   # fill in the three values
npm run dev
```

Three env vars are required:

| Variable | Where it's used |
|---|---|
| `NEXT_PUBLIC_SUPABASE_URL` | Browser + server |
| `NEXT_PUBLIC_SUPABASE_ANON_KEY` | Browser + server |
| `ANTHROPIC_API_KEY` | Server-only (`/api/extract`) |

## Tests

```bash
npm test                # 52 tests, ~85% line coverage
npm run test:coverage   # full coverage report
```

All Supabase and Anthropic calls are mocked — tests run hermetically.

## Deploy

This subdirectory is configured as a Vercel project with `Root Directory`
set to `contact-manager`. Pushes to `main` that touch files inside this
directory trigger an auto-deploy.

To deploy manually from your machine:

```bash
vercel deploy --prod
```

## Conventions

See [`CLAUDE.md`](./CLAUDE.md) and [`AGENTS.md`](./AGENTS.md) for the rules
this project follows. Future changes (by humans or by Claude) should
respect both files.
