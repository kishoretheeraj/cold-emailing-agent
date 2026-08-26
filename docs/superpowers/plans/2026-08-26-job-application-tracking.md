# Job Application Tracking (Phase 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **For the hourly auto-continue workflow:** find the first `- [ ]` below (top to bottom across
> all tasks) and execute only that task's remaining steps, then stop. Do not skip ahead. When every
> box in this file is checked, report back to
> `docs/superpowers/specs/2026-08-26-full-fledged-job-platform-buildout.md` and start Phase 2's
> plan using the `writing-plans` skill against that spec's Phase 2 stub.

**Goal:** Give the agent a job-application pipeline (`saved → applied → phone_screen → onsite →
offer/rejected/withdrawn/accepted`) that is independent of `contacts.stage`, with a Supabase table,
Python `db.py` accessors, and a contact-manager `/applications` page to view and move applications
through it.

**Architecture:** One new table (`job_applications`), backend accessors in `db.py` (Python, mocked
Supabase client in tests — no live DB needed for the test suite), and a standalone Next.js page +
two API routes in `contact-manager/` that talk to Supabase directly from the route handlers (same
shape as `/api/agent-config`). No changes to any existing table, stage map, or the four mirrored
first-touch stage sets documented in the root CLAUDE.md.

**Tech Stack:** Python 3.11 / pytest / pytest-mock (backend). Next.js 16 App Router / TypeScript
strict / Vitest / Playwright (frontend).

**Spec:** `docs/superpowers/specs/2026-08-26-full-fledged-job-platform-buildout.md` (Phase 1
section).

## Global Constraints

- Python: no type annotations, no docstrings on `_prefixed` helpers, public functions get a
  one-line docstring, `f""` strings for any log lines.
- `contacts.id` is Postgres `INTEGER`, not UUID/bigint — `job_applications.contact_id` must be
  `INTEGER` to FK against it, and every contact-manager API route / frontend type that carries an
  id must treat it as a string client-side (existing `Contact.id: string` convention) even though
  Postgres stores a number.
- Migrations are additive only: `IF NOT EXISTS`, nullable or defaulted columns, no backfill, no
  destructive change to any existing table.
- Contact-manager: TypeScript strict, no `any`. Server components by default; anything with state/
  effects gets `"use client";` as line 1. Tailwind utility classes only, existing theme tokens
  (`bg-surface`, `border-border`, `text-fg`, etc.) — no new CSS file. No `shadcn/ui`, no new state
  library. API routes: `export const runtime = "nodejs"`, 400 for bad input, 500 for unexpected
  errors.
- Contact-manager tests: **zero tolerance for failing tests** — `npm test` and `npm run test:e2e`
  must both be fully green before any commit that touches contact-manager. If you add a persistent
  nav link, `tests/e2e/00-shell.spec.ts` must get an assertion for it, and the e2e test count in
  `contact-manager/CLAUDE.md` must be updated.
- Definition of done (root CLAUDE.md): every task below ends with tests green, then (on the last
  task) both CLAUDE.md files and memory updated, before the final commit.
- This is Next.js 16 with breaking changes from prior Next versions per `contact-manager/AGENTS.md`
  — if a step below needs a Next.js API you're unsure of (this plan's first-ever dynamic API route,
  `/api/applications/[id]`), check `node_modules/next/dist/docs/` before writing it, not training
  data.

---

## Task 1: Migration — `job_applications` table

**Files:**
- Create: `supabase/migrations/20260826000000_create_job_applications.sql`

**Interfaces:**
- Produces: table `job_applications(id BIGSERIAL, contact_id INTEGER NULL, company TEXT NOT NULL,
  role TEXT NOT NULL, job_url TEXT NULL, source TEXT NULL, stage TEXT NOT NULL DEFAULT 'saved',
  applied_date DATE NULL, notes TEXT NULL, posting_snapshot JSONB NULL, created_at TIMESTAMPTZ,
  updated_at TIMESTAMPTZ)`.

- [x] **Step 1: Write the migration**

```sql
-- job_applications: application-tracking layer, separate from contacts.stage.
--
-- Sub-project: full-fledged buildout, Phase 1. contacts.stage tracks the
-- OUTREACH relationship lifecycle (new -> *_drafted -> *_sent); it has no
-- concept of "applied to req #X at Company Y, now onsite." This table is
-- that missing pipeline, deliberately independent so it never touches the
-- four mirrored first-touch stage/action sets documented in the root
-- CLAUDE.md (agent.py, emailer.py, monitor.detect_sent_drafts,
-- engagement_report._FIRST_TOUCH_DRAFTED_STAGES).
--
-- contact_id is nullable + ON DELETE SET NULL: an application can exist
-- without a known contact (e.g. applied cold via a job board before any
-- outreach contact exists for that company). contacts.id is INTEGER, not
-- UUID, so this column must match.
--
-- posting_snapshot is JSONB (not typed columns) so future scraped fields
-- (salary, location, description excerpt, source-specific ids) can be
-- added without another migration -- same reasoning as
-- draft_history.decision_context.

CREATE TABLE IF NOT EXISTS job_applications (
  id BIGSERIAL PRIMARY KEY,
  contact_id INTEGER REFERENCES contacts(id) ON DELETE SET NULL,
  company TEXT NOT NULL,
  role TEXT NOT NULL,
  job_url TEXT,
  source TEXT,
  stage TEXT NOT NULL DEFAULT 'saved'
    CHECK (stage IN ('saved','applied','phone_screen','onsite','offer','rejected','withdrawn','accepted')),
  applied_date DATE,
  notes TEXT,
  posting_snapshot JSONB,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_job_applications_stage ON job_applications(stage);
CREATE INDEX IF NOT EXISTS idx_job_applications_contact_id ON job_applications(contact_id);

COMMENT ON TABLE job_applications IS
  'Application-tracking pipeline (saved -> applied -> ... -> offer/rejected), independent of contacts.stage which tracks outreach only.';
```

- [x] **Step 2: Push the migration**

Run: `supabase db push`
Expected: migration applies cleanly with no errors (additive-only, so no data at risk).

Done 2026-08-26 (interactive session, not CI — the CI runner has no Supabase CLI
authentication; see the auto-continue workflow's blocked-step handling for what to do
if a future migration task hits this same gap and no human is available to push it
manually).

- [x] **Step 3: Verify the table exists**

Run: `supabase db query --linked "select column_name, data_type from information_schema.columns where table_name = 'job_applications' order by ordinal_position;"`
(note: `supabase db execute` is not a real subcommand — `db query --linked` is correct)
Expected: 11 rows matching the columns above. Confirmed 2026-08-26.

- [x] **Step 4: Commit**

```bash
git add supabase/migrations/20260826000000_create_job_applications.sql
git commit -m "feat: add job_applications table for application-tracking pipeline"
```

---

## Task 2: `db.py` — create and list job applications

**Files:**
- Modify: `db.py` (append new section)
- Test: `tests/test_job_applications_db.py` (create)

**Interfaces:**
- Consumes: `get_client()`, `_retry(fn)` (both already in `db.py`).
- Produces: `create_job_application(company, role, job_url=None, source=None, contact_id=None, applied_date=None, notes=None, posting_snapshot=None) -> dict | None`,
  `get_job_applications(stage=None) -> list[dict]`. Later tasks and the frontend rely on these
  exact names and the `stage` keyword-only-by-convention filter.

- [x] **Step 1: Write the failing tests**

```python
"""Tests for db.py's job_applications accessors."""

from unittest.mock import MagicMock

import pytest

import db


@pytest.fixture
def fake_client(mocker):
    client = MagicMock(name="supabase_client")
    mocker.patch.object(db, "_client", client)
    mocker.patch.object(db, "get_client", return_value=client)
    return client


def test_create_job_application_inserts_with_default_stage(fake_client):
    fake_client.table.return_value.insert.return_value.execute.return_value.data = [
        {"id": 1, "company": "Acme", "role": "PM", "stage": "saved"}
    ]
    result = db.create_job_application(company="Acme", role="PM")
    fake_client.table.assert_called_with("job_applications")
    inserted = fake_client.table.return_value.insert.call_args[0][0]
    assert inserted["stage"] == "saved"
    assert inserted["company"] == "Acme"
    assert inserted["role"] == "PM"
    assert result["id"] == 1


def test_create_job_application_passes_optional_fields(fake_client):
    fake_client.table.return_value.insert.return_value.execute.return_value.data = [{"id": 2}]
    db.create_job_application(
        company="Acme", role="PM", job_url="https://x", source="manual",
        contact_id=5, applied_date="2026-08-26", notes="hi",
        posting_snapshot={"salary": "150k"},
    )
    inserted = fake_client.table.return_value.insert.call_args[0][0]
    assert inserted["job_url"] == "https://x"
    assert inserted["source"] == "manual"
    assert inserted["contact_id"] == 5
    assert inserted["applied_date"] == "2026-08-26"
    assert inserted["notes"] == "hi"
    assert inserted["posting_snapshot"] == {"salary": "150k"}


def test_create_job_application_returns_none_on_empty_data(fake_client):
    fake_client.table.return_value.insert.return_value.execute.return_value.data = []
    assert db.create_job_application(company="Acme", role="PM") is None


def test_get_job_applications_returns_all_rows(fake_client):
    fake_client.table.return_value.select.return_value.order.return_value.execute.return_value.data = [
        {"id": 1, "stage": "saved"}, {"id": 2, "stage": "applied"},
    ]
    result = db.get_job_applications()
    assert len(result) == 2


def test_get_job_applications_filters_by_stage(fake_client):
    fake_client.table.return_value.select.return_value.eq.return_value.order.return_value.execute.return_value.data = [
        {"id": 2, "stage": "applied"},
    ]
    result = db.get_job_applications(stage="applied")
    fake_client.table.return_value.select.return_value.eq.assert_called_with("stage", "applied")
    assert result == [{"id": 2, "stage": "applied"}]


def test_get_job_applications_empty_on_no_data(fake_client):
    fake_client.table.return_value.select.return_value.order.return_value.execute.return_value.data = None
    assert db.get_job_applications() == []


def test_get_job_applications_propagates_on_error(mocker):
    mocker.patch.object(db, "get_client", side_effect=RuntimeError("db down"))
    with pytest.raises(RuntimeError):
        db.get_job_applications()
```

- [x] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_job_applications_db.py -v`
Expected: FAIL with `AttributeError: module 'db' has no attribute 'create_job_application'`.

Confirmed 2026-08-26: all 10 tests failed with AttributeError before implementation.

- [x] **Step 3: Implement**

Append to `db.py`:

```python
# ── Job application tracking (Phase 1 of full-fledged buildout) ─────────────────

def create_job_application(company, role, job_url=None, source=None, contact_id=None,
                            applied_date=None, notes=None, posting_snapshot=None):
    """Create a new job application row, starting at stage 'saved'."""
    payload = {
        "company": company,
        "role": role,
        "job_url": job_url,
        "source": source,
        "contact_id": contact_id,
        "applied_date": applied_date,
        "notes": notes,
        "posting_snapshot": posting_snapshot,
        "stage": "saved",
    }
    result = _retry(lambda: get_client().table("job_applications").insert(payload).execute())
    return result.data[0] if result.data else None


def get_job_applications(stage=None):
    """Fetch job applications, optionally filtered by stage, newest first."""
    query = get_client().table("job_applications").select("*")
    if stage is not None:
        query = query.eq("stage", stage)
    result = _retry(lambda: query.order("created_at", desc=True).execute())
    return result.data or []
```

- [x] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_job_applications_db.py -v`
Expected: 7 passed.

Note: Task 3's functions were implemented in the same edit, so this run showed 10 passed, not 7 — all accounted for below.

- [x] **Step 5: Run the full suite**

Run: `python3 -m pytest`
Expected: all existing tests still pass (no shared state touched).

Confirmed 2026-08-26: 832 passed.

- [x] **Step 6: Commit**

```bash
git add db.py tests/test_job_applications_db.py
git commit -m "feat: add create_job_application and get_job_applications to db.py"
```

Done 2026-08-26 (commit eeb869c) — combined with all four functions since they were
written together; see Task 3's commit note below.

---

## Task 3: `db.py` — update stage and fetch one application

**Files:**
- Modify: `db.py` (same section as Task 2)
- Modify: `tests/test_job_applications_db.py`

**Interfaces:**
- Consumes: same as Task 2.
- Produces: `update_job_application_stage(application_id, stage) -> dict | None`,
  `get_job_application(application_id) -> dict | None`. The contact-manager PATCH route (Task 6)
  and page (Task 7) call these by these exact names.

- [x] **Step 1: Write the failing tests**

Append to `tests/test_job_applications_db.py`:

```python
def test_update_job_application_stage(fake_client):
    fake_client.table.return_value.update.return_value.eq.return_value.execute.return_value.data = [
        {"id": 1, "stage": "onsite"}
    ]
    result = db.update_job_application_stage(1, "onsite")
    updated = fake_client.table.return_value.update.call_args[0][0]
    assert updated["stage"] == "onsite"
    assert "updated_at" in updated
    fake_client.table.return_value.update.return_value.eq.assert_called_with("id", 1)
    assert result["stage"] == "onsite"


def test_update_job_application_stage_returns_none_on_empty_data(fake_client):
    fake_client.table.return_value.update.return_value.eq.return_value.execute.return_value.data = []
    assert db.update_job_application_stage(1, "onsite") is None


def test_get_job_application_by_id(fake_client):
    fake_client.table.return_value.select.return_value.eq.return_value.single.return_value.execute.return_value.data = {
        "id": 1, "company": "Acme"
    }
    result = db.get_job_application(1)
    fake_client.table.return_value.select.return_value.eq.assert_called_with("id", 1)
    assert result["company"] == "Acme"
```

- [x] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_job_applications_db.py -v -k "update_job_application_stage or get_job_application_by_id"`
Expected: FAIL with `AttributeError`.

Confirmed 2026-08-26 (as part of the combined Task 2/3 red run, all 10 tests failed).

- [x] **Step 3: Implement**

Append to the same `db.py` section:

```python
def update_job_application_stage(application_id, stage):
    """Update a job application's stage."""
    result = _retry(lambda: get_client().table("job_applications")
                     .update({"stage": stage, "updated_at": datetime.utcnow().isoformat()})
                     .eq("id", application_id).execute())
    return result.data[0] if result.data else None


def get_job_application(application_id):
    """Fetch a single job application by id."""
    result = _retry(lambda: get_client().table("job_applications")
                     .select("*").eq("id", application_id).single().execute())
    return result.data
```

Add `datetime` to the existing `from datetime import date, timedelta` import line at the top of
`db.py` so it reads `from datetime import date, datetime, timedelta`.

- [x] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_job_applications_db.py -v`
Expected: 10 passed.

Confirmed 2026-08-26: 10 passed.

- [x] **Step 5: Run the full suite**

Run: `python3 -m pytest`
Expected: all tests pass.

Confirmed 2026-08-26: 832 passed.

- [x] **Step 6: Commit**

```bash
git add db.py tests/test_job_applications_db.py
git commit -m "feat: add update_job_application_stage and get_job_application to db.py"
```

Done 2026-08-26, combined into commit eeb869c alongside Task 2 — all four
db.py functions and all 10 tests were written and verified together in one pass,
so Tasks 2 and 3 share a single commit rather than two.

---

## Task 4: contact-manager types — `JobApplication`

**Files:**
- Modify: `contact-manager/src/lib/types.ts`

**Interfaces:**
- Produces: `JobApplicationStage` type, `JOB_APPLICATION_STAGES` array,
  `JOB_APPLICATION_STAGE_LABELS` map, `JobApplication` type. Tasks 5-7 import all four from
  `@/lib/types`.

- [x] **Step 1: Add the type and constants**

Append to `contact-manager/src/lib/types.ts`:

```ts
export type JobApplicationStage =
  | "saved"
  | "applied"
  | "phone_screen"
  | "onsite"
  | "offer"
  | "rejected"
  | "withdrawn"
  | "accepted";

export const JOB_APPLICATION_STAGES: JobApplicationStage[] = [
  "saved",
  "applied",
  "phone_screen",
  "onsite",
  "offer",
  "rejected",
  "withdrawn",
  "accepted",
];

export const JOB_APPLICATION_STAGE_LABELS: Record<JobApplicationStage, string> = {
  saved: "Saved",
  applied: "Applied",
  phone_screen: "Phone screen",
  onsite: "Onsite",
  offer: "Offer",
  rejected: "Rejected",
  withdrawn: "Withdrawn",
  accepted: "Accepted",
};

export type JobApplication = {
  id: string;
  contact_id: string | null;
  company: string;
  role: string;
  job_url: string | null;
  source: string | null;
  stage: JobApplicationStage;
  applied_date: string | null;
  notes: string | null;
  posting_snapshot: Record<string, unknown> | null;
  created_at: string;
  updated_at: string;
};
```

`id` and `contact_id` are typed `string` (not `number`) even though Postgres stores them as
`INTEGER` — same coercion convention as `Contact.id`, to avoid the exact bug class documented in
the root CLAUDE.md's "contact_id Type Fix" note. Every API route below returns/accepts them as
strings and does the `Number(...)` conversion only at the Supabase-query boundary.

- [x] **Step 2: Typecheck**

Run: `npm run build`
Expected: no new TypeScript errors (this step only adds exported types, nothing consumes them yet).

Confirmed 2026-08-26: build succeeded.

- [x] **Step 3: Commit**

```bash
git add src/lib/types.ts
git commit -m "feat: add JobApplication type to contact-manager"
```

---

## Task 5: `/api/applications` — list and create

**Files:**
- Create: `contact-manager/src/app/api/applications/route.ts`
- Create: `contact-manager/src/app/api/applications/route.test.ts`

**Interfaces:**
- Consumes: `JobApplicationStage`, `JOB_APPLICATION_STAGES` from `@/lib/types`.
- Produces: `GET` returns `{ applications: JobApplication[] }`, optionally filtered by
  `?stage=<stage>`. `POST` body `{ company: string, role: string, job_url?, source?, contact_id?:
  string, applied_date?, notes? }` returns `{ application: JobApplication }` (201) or `{ error }`
  (400/500). Task 7's page calls both.

- [x] **Step 1: Write the failing tests**

```ts
import { describe, it, expect, vi, beforeEach } from "vitest";
import { GET, POST } from "./route";

const mockOrder = vi.fn();
const mockEq = vi.fn();
const mockSelect = vi.fn();
const mockSingle = vi.fn();
const mockInsertSelect = vi.fn();
const mockInsert = vi.fn();
const mockFrom = vi.fn();

vi.mock("@supabase/supabase-js", () => ({
  createClient: vi.fn(() => ({
    from: mockFrom,
  })),
}));

beforeEach(() => {
  vi.clearAllMocks();
  mockOrder.mockResolvedValue({ data: [{ id: "1", company: "Acme" }], error: null });
  mockEq.mockReturnValue({ order: mockOrder });
  mockSelect.mockReturnValue({ order: mockOrder, eq: mockEq });
  mockSingle.mockResolvedValue({ data: { id: "1", company: "Acme", role: "PM" }, error: null });
  mockInsertSelect.mockReturnValue({ single: mockSingle });
  mockInsert.mockReturnValue({ select: mockInsertSelect });
  mockFrom.mockReturnValue({ select: mockSelect, insert: mockInsert });
});

describe("GET /api/applications", () => {
  it("returns all applications", async () => {
    const res = await GET(new Request("http://test/api/applications"));
    const body = await res.json();
    expect(body.applications).toEqual([{ id: "1", company: "Acme" }]);
  });

  it("filters by stage query param", async () => {
    await GET(new Request("http://test/api/applications?stage=applied"));
    expect(mockEq).toHaveBeenCalledWith("stage", "applied");
  });

  it("returns 500 on supabase error", async () => {
    mockOrder.mockResolvedValue({ data: null, error: new Error("db down") });
    const res = await GET(new Request("http://test/api/applications"));
    expect(res.status).toBe(500);
  });
});

describe("POST /api/applications", () => {
  it("creates an application with company and role", async () => {
    const req = new Request("http://test/api/applications", {
      method: "POST",
      body: JSON.stringify({ company: "Acme", role: "PM" }),
    });
    const res = await POST(req);
    expect(res.status).toBe(201);
    const body = await res.json();
    expect(body.application.company).toBe("Acme");
  });

  it("returns 400 when company is missing", async () => {
    const req = new Request("http://test/api/applications", {
      method: "POST",
      body: JSON.stringify({ role: "PM" }),
    });
    const res = await POST(req);
    expect(res.status).toBe(400);
  });

  it("returns 400 when role is missing", async () => {
    const req = new Request("http://test/api/applications", {
      method: "POST",
      body: JSON.stringify({ company: "Acme" }),
    });
    const res = await POST(req);
    expect(res.status).toBe(400);
  });

  it("returns 400 for invalid JSON", async () => {
    const req = new Request("http://test/api/applications", { method: "POST", body: "not json" });
    const res = await POST(req);
    expect(res.status).toBe(400);
  });

  it("returns 500 on supabase insert error", async () => {
    mockSingle.mockResolvedValue({ data: null, error: new Error("insert failed") });
    const req = new Request("http://test/api/applications", {
      method: "POST",
      body: JSON.stringify({ company: "Acme", role: "PM" }),
    });
    const res = await POST(req);
    expect(res.status).toBe(500);
  });
});
```

- [x] **Step 2: Run tests to verify they fail**

Run: `npm test -- route.test.ts` (from `contact-manager/`)
Expected: FAIL — `./route` has no exported `GET`/`POST` (file doesn't exist yet).

- [x] **Step 3: Implement**

```ts
export const runtime = "nodejs";

import { createClient } from "@supabase/supabase-js";

function getClient() {
  return createClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!
  );
}

export async function GET(req: Request) {
  const { searchParams } = new URL(req.url);
  const stage = searchParams.get("stage");
  try {
    const supabase = getClient();
    let query = supabase.from("job_applications").select("*");
    if (stage) query = query.eq("stage", stage);
    const { data, error } = await query.order("created_at", { ascending: false });
    if (error) throw error;
    return Response.json({ applications: data ?? [] });
  } catch (err) {
    return Response.json({ error: String(err) }, { status: 500 });
  }
}

export async function POST(req: Request) {
  let body: unknown;
  try {
    body = await req.json();
  } catch {
    return Response.json({ error: "Invalid JSON" }, { status: 400 });
  }

  if (typeof body !== "object" || body === null) {
    return Response.json({ error: "company and role are required" }, { status: 400 });
  }
  const b = body as Record<string, unknown>;
  const company = typeof b.company === "string" ? b.company.trim() : "";
  const role = typeof b.role === "string" ? b.role.trim() : "";
  if (!company || !role) {
    return Response.json({ error: "company and role are required" }, { status: 400 });
  }

  try {
    const supabase = getClient();
    const { data, error } = await supabase
      .from("job_applications")
      .insert({
        company,
        role,
        job_url: typeof b.job_url === "string" ? b.job_url : null,
        source: typeof b.source === "string" ? b.source : "manual",
        contact_id: typeof b.contact_id === "string" ? Number(b.contact_id) : null,
        applied_date: typeof b.applied_date === "string" ? b.applied_date : null,
        notes: typeof b.notes === "string" ? b.notes : null,
        stage: "saved",
      })
      .select()
      .single();
    if (error) throw error;
    return Response.json({ application: data }, { status: 201 });
  } catch (err) {
    return Response.json({ error: String(err) }, { status: 500 });
  }
}
```

- [x] **Step 4: Run tests to verify they pass**

Run: `npm test -- route.test.ts` (from `contact-manager/`)
Expected: all pass.

- [x] **Step 5: Commit**

```bash
git add src/app/api/applications/route.ts src/app/api/applications/route.test.ts
git commit -m "feat: add GET/POST /api/applications"
```

---

## Task 6: `/api/applications/[id]` — update stage/notes

**Files:**
- Create: `contact-manager/src/app/api/applications/[id]/route.ts`
- Create: `contact-manager/src/app/api/applications/[id]/route.test.ts`

**Interfaces:**
- Consumes: `JOB_APPLICATION_STAGES` from `@/lib/types`.
- Produces: `PATCH` body `{ stage?: JobApplicationStage, notes?: string }` returns
  `{ application: JobApplication }` or `{ error }` (400/500). Task 7's page calls this on stage
  change.

- [x] **Step 1: Check Next.js 16 dynamic-route param convention before writing anything**

This repo has no existing `[id]`-style route yet — this is the first one. Per
`contact-manager/AGENTS.md`, read the relevant guide under `node_modules/next/dist/docs/` (grep for
"route handler" and "dynamic" params) to confirm whether `params` is passed synchronously or as a
`Promise` in this installed Next.js version before writing Step 3. Do not assume based on prior Next
versions.

- [x] **Step 2: Write the failing tests**

```ts
import { describe, it, expect, vi, beforeEach } from "vitest";
import { PATCH } from "./route";

const mockSingle = vi.fn();
const mockSelect = vi.fn();
const mockEq = vi.fn();
const mockUpdate = vi.fn();
const mockFrom = vi.fn();

vi.mock("@supabase/supabase-js", () => ({
  createClient: vi.fn(() => ({ from: mockFrom })),
}));

function params(id: string) {
  return { params: Promise.resolve({ id }) };
}

beforeEach(() => {
  vi.clearAllMocks();
  mockSingle.mockResolvedValue({ data: { id: "1", stage: "onsite" }, error: null });
  mockSelect.mockReturnValue({ single: mockSingle });
  mockEq.mockReturnValue({ select: mockSelect });
  mockUpdate.mockReturnValue({ eq: mockEq });
  mockFrom.mockReturnValue({ update: mockUpdate });
});

describe("PATCH /api/applications/[id]", () => {
  it("updates stage", async () => {
    const req = new Request("http://test", { method: "PATCH", body: JSON.stringify({ stage: "onsite" }) });
    const res = await PATCH(req, params("1"));
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.application.stage).toBe("onsite");
  });

  it("rejects an invalid stage", async () => {
    const req = new Request("http://test", { method: "PATCH", body: JSON.stringify({ stage: "not_a_stage" }) });
    const res = await PATCH(req, params("1"));
    expect(res.status).toBe(400);
  });

  it("returns 400 for invalid JSON", async () => {
    const req = new Request("http://test", { method: "PATCH", body: "not json" });
    const res = await PATCH(req, params("1"));
    expect(res.status).toBe(400);
  });

  it("returns 400 when no valid fields given", async () => {
    const req = new Request("http://test", { method: "PATCH", body: JSON.stringify({}) });
    const res = await PATCH(req, params("1"));
    expect(res.status).toBe(400);
  });

  it("returns 500 on supabase error", async () => {
    mockSingle.mockResolvedValue({ data: null, error: new Error("update failed") });
    const req = new Request("http://test", { method: "PATCH", body: JSON.stringify({ stage: "onsite" }) });
    const res = await PATCH(req, params("1"));
    expect(res.status).toBe(500);
  });
});
```

- [x] **Step 3: Run tests to verify they fail**

Run: `npm test -- "applications/\[id\]"` (from `contact-manager/`)
Expected: FAIL — file doesn't exist.

- [x] **Step 4: Implement**

Write `route.ts` using whatever `params` shape Step 1 confirmed (the test above assumes
`Promise<{ id: string }>`, matching the rest of this Next.js version's async-params convention — if
Step 1 found otherwise, adjust both the test and implementation together):

```ts
export const runtime = "nodejs";

import { createClient } from "@supabase/supabase-js";

const JOB_APPLICATION_STAGES = [
  "saved", "applied", "phone_screen", "onsite", "offer", "rejected", "withdrawn", "accepted",
] as const;

function getClient() {
  return createClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!
  );
}

export async function PATCH(req: Request, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;

  let body: unknown;
  try {
    body = await req.json();
  } catch {
    return Response.json({ error: "Invalid JSON" }, { status: 400 });
  }

  if (typeof body !== "object" || body === null) {
    return Response.json({ error: "no valid fields to update" }, { status: 400 });
  }
  const b = body as Record<string, unknown>;
  const updates: Record<string, unknown> = {};

  if ("stage" in b) {
    if (!JOB_APPLICATION_STAGES.includes(b.stage as (typeof JOB_APPLICATION_STAGES)[number])) {
      return Response.json(
        { error: `stage must be one of: ${JOB_APPLICATION_STAGES.join(", ")}` },
        { status: 400 }
      );
    }
    updates.stage = b.stage;
  }
  if ("notes" in b && typeof b.notes === "string") {
    updates.notes = b.notes;
  }

  if (Object.keys(updates).length === 0) {
    return Response.json({ error: "no valid fields to update" }, { status: 400 });
  }
  updates.updated_at = new Date().toISOString();

  try {
    const supabase = getClient();
    const { data, error } = await supabase
      .from("job_applications")
      .update(updates)
      .eq("id", Number(id))
      .select()
      .single();
    if (error) throw error;
    return Response.json({ application: data });
  } catch (err) {
    return Response.json({ error: String(err) }, { status: 500 });
  }
}
```

- [x] **Step 5: Run tests to verify they pass**

Run: `npm test -- "applications/\[id\]"` (from `contact-manager/`)
Expected: all pass.

- [x] **Step 6: Commit**

```bash
git add "src/app/api/applications/[id]/route.ts" "src/app/api/applications/[id]/route.test.ts"
git commit -m "feat: add PATCH /api/applications/[id]"
```

---

## Task 7: `/applications` page

**Files:**
- Create: `contact-manager/src/app/applications/page.tsx`
- Create: `contact-manager/src/components/ApplicationsPage.tsx`
- Create: `contact-manager/src/components/ApplicationsPage.test.tsx`

**Interfaces:**
- Consumes: `JobApplication`, `JOB_APPLICATION_STAGES`, `JOB_APPLICATION_STAGE_LABELS` from
  `@/lib/types`; `Select`/`SelectTrigger`/`SelectContent`/`SelectItem`/`SelectValue` from
  `@/components/ui/Select`; `Badge` from `@/components/ui/Badge`; `toast` from `sonner`.
- Produces: default export `ApplicationsPage` client component, rendered by `page.tsx`.

- [x] **Step 1: Write the server page wrapper**

```tsx
import { ApplicationsPage } from "@/components/ApplicationsPage";

export default function Page() {
  return <ApplicationsPage />;
}
```

- [x] **Step 2: Write the failing component test**

```tsx
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ApplicationsPage } from "./ApplicationsPage";

vi.mock("@/components/ui/Tooltip", () => ({
  Tooltip: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));

const sampleApplications = [
  { id: "1", contact_id: null, company: "Acme", role: "PM", job_url: null, source: "manual",
    stage: "saved", applied_date: null, notes: null, posting_snapshot: null,
    created_at: "2026-08-26T00:00:00Z", updated_at: "2026-08-26T00:00:00Z" },
  { id: "2", contact_id: null, company: "Globex", role: "Eng", job_url: null, source: "manual",
    stage: "applied", applied_date: "2026-08-20", notes: null, posting_snapshot: null,
    created_at: "2026-08-20T00:00:00Z", updated_at: "2026-08-20T00:00:00Z" },
];

beforeEach(() => {
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string, opts?: RequestInit) => {
      if (!opts || opts.method === undefined) {
        return Promise.resolve({
          ok: true,
          json: async () => ({ applications: sampleApplications }),
        } as Response);
      }
      return Promise.resolve({
        ok: true,
        json: async () => ({ application: { ...sampleApplications[0], stage: "applied" } }),
      } as Response);
    })
  );
});

describe("ApplicationsPage", () => {
  it("renders fetched applications", async () => {
    render(<ApplicationsPage />);
    expect(await screen.findByText("Acme")).toBeInTheDocument();
    expect(screen.getByText("Globex")).toBeInTheDocument();
  });

  it("adds a new application via the form", async () => {
    const user = userEvent.setup();
    render(<ApplicationsPage />);
    await screen.findByText("Acme");
    await user.type(screen.getByLabelText("Company"), "NewCo");
    await user.type(screen.getByLabelText("Role"), "Designer");
    await user.click(screen.getByRole("button", { name: /add application/i }));
    await waitFor(() => {
      expect(global.fetch).toHaveBeenCalledWith(
        "/api/applications",
        expect.objectContaining({ method: "POST" })
      );
    });
  });
});
```

- [x] **Step 3: Run test to verify it fails**

Run: `npm test -- ApplicationsPage` (from `contact-manager/`)
Expected: FAIL — `./ApplicationsPage` doesn't exist.

- [x] **Step 4: Implement**

```tsx
"use client";

import { useEffect, useState, type FormEvent } from "react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/Badge";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/Select";
import {
  JOB_APPLICATION_STAGES,
  JOB_APPLICATION_STAGE_LABELS,
  type JobApplication,
  type JobApplicationStage,
} from "@/lib/types";

export function ApplicationsPage() {
  const [applications, setApplications] = useState<JobApplication[]>([]);
  const [loading, setLoading] = useState(true);
  const [company, setCompany] = useState("");
  const [role, setRole] = useState("");
  const [jobUrl, setJobUrl] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const load = async () => {
    try {
      const res = await fetch("/api/applications");
      const data = await res.json();
      setApplications(data.applications ?? []);
    } catch {
      toast.error("Could not load applications");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
  }, []);

  const handleAdd = async (e: FormEvent) => {
    e.preventDefault();
    if (!company.trim() || !role.trim()) return;
    setSubmitting(true);
    try {
      const res = await fetch("/api/applications", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ company, role, job_url: jobUrl || undefined }),
      });
      if (!res.ok) throw new Error("request failed");
      const data = await res.json();
      setApplications((prev) => [data.application, ...prev]);
      setCompany("");
      setRole("");
      setJobUrl("");
      toast.success("Application added");
    } catch {
      toast.error("Could not add application");
    } finally {
      setSubmitting(false);
    }
  };

  const handleStageChange = async (id: string, stage: JobApplicationStage) => {
    const prev = applications;
    setApplications((cur) => cur.map((a) => (a.id === id ? { ...a, stage } : a)));
    try {
      const res = await fetch(`/api/applications/${id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ stage }),
      });
      if (!res.ok) throw new Error("request failed");
    } catch {
      setApplications(prev);
      toast.error("Could not update stage");
    }
  };

  return (
    <div className="p-6 flex flex-col gap-6">
      <h1 className="text-lg font-medium text-fg">Applications</h1>

      <form onSubmit={handleAdd} className="flex flex-wrap items-end gap-3">
        <label className="flex flex-col gap-1 text-sm text-fg-muted">
          Company
          <input
            aria-label="Company"
            value={company}
            onChange={(e) => setCompany(e.target.value)}
            className="px-3 py-2 bg-surface-2 border border-border rounded-md text-sm text-fg"
          />
        </label>
        <label className="flex flex-col gap-1 text-sm text-fg-muted">
          Role
          <input
            aria-label="Role"
            value={role}
            onChange={(e) => setRole(e.target.value)}
            className="px-3 py-2 bg-surface-2 border border-border rounded-md text-sm text-fg"
          />
        </label>
        <label className="flex flex-col gap-1 text-sm text-fg-muted">
          Job URL
          <input
            aria-label="Job URL"
            value={jobUrl}
            onChange={(e) => setJobUrl(e.target.value)}
            className="px-3 py-2 bg-surface-2 border border-border rounded-md text-sm text-fg"
          />
        </label>
        <button
          type="submit"
          disabled={submitting}
          className="px-3 py-2 bg-indigo-600 text-white rounded-md text-sm disabled:opacity-50"
        >
          Add application
        </button>
      </form>

      {loading ? (
        <p className="text-sm text-fg-dim">Loading...</p>
      ) : applications.length === 0 ? (
        <p className="text-sm text-fg-dim">No applications yet.</p>
      ) : (
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-fg-dim border-b border-border">
              <th className="py-2 pr-4">Company</th>
              <th className="py-2 pr-4">Role</th>
              <th className="py-2 pr-4">Stage</th>
              <th className="py-2 pr-4">Applied</th>
            </tr>
          </thead>
          <tbody>
            {applications.map((app) => (
              <tr key={app.id} className="border-b border-border">
                <td className="py-2 pr-4 text-fg">{app.company}</td>
                <td className="py-2 pr-4 text-fg-muted">{app.role}</td>
                <td className="py-2 pr-4">
                  <Select
                    value={app.stage}
                    onValueChange={(v) => handleStageChange(app.id, v as JobApplicationStage)}
                  >
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {JOB_APPLICATION_STAGES.map((s) => (
                        <SelectItem key={s} value={s}>
                          {JOB_APPLICATION_STAGE_LABELS[s]}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </td>
                <td className="py-2 pr-4 text-fg-dim">{app.applied_date ?? <Badge>Not yet</Badge>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
```

- [x] **Step 5: Run test to verify it passes**

Run: `npm test -- ApplicationsPage` (from `contact-manager/`)
Expected: both tests pass.

- [x] **Step 6: Full unit suite + typecheck**

Run: `npm test && npm run build` (from `contact-manager/`)
Expected: 0 failures, build succeeds.

- [x] **Step 7: Commit**

```bash
git add src/app/applications/page.tsx src/components/ApplicationsPage.tsx src/components/ApplicationsPage.test.tsx
git commit -m "feat: add /applications page"
```

---

## Task 8: Nav link + e2e smoke test

**Files:**
- Modify: `contact-manager/src/components/Nav.tsx`
- Modify: `contact-manager/tests/e2e/00-shell.spec.ts`
- Create: `contact-manager/tests/e2e/18-applications.spec.ts`

**Interfaces:**
- Consumes: `/applications` route from Task 7.

- [ ] **Step 1: Add the nav link**

In `contact-manager/src/components/Nav.tsx`, add one entry to the `NAV_LINKS` array (placed after
`"Contacts"` since applications relate most closely to the contacts list):

```ts
const NAV_LINKS = [
  { href: "/overview", label: "Overview" },
  { href: "/", label: "Contacts" },
  { href: "/applications", label: "Applications" },
  { href: "/queue", label: "Queue" },
  { href: "/replies", label: "Replies" },
  { href: "/import", label: "Import" },
  { href: "/prompts", label: "Prompts" },
  { href: "/runs", label: "Activity" },
  { href: "/lab", label: "Lab" },
  { href: "/visa-review", label: "Visa" },
] as const;
```

- [ ] **Step 2: Update the shell regression test**

In `contact-manager/tests/e2e/00-shell.spec.ts`, in the `"main page has expected chrome..."` test
(starts at line 8), add a new locator line in the same style as the existing ones (e.g. line 27's
`queueLink`), placed after the `contactsLink` assertion block since Applications sits next to
Contacts in the nav:

```ts
  const applicationsLink = page.getByRole("link", { name: /^applications$/i });
  await expect(applicationsLink).toBeVisible();
```

(Match the exact `await expect(...).toBeVisible()` line that follows each of the existing
`const xLink = page.getByRole(...)` declarations at lines 15-47 — copy that two-line shape.)

- [ ] **Step 3: Write the new page's e2e smoke test**

Read `contact-manager/tests/e2e/helpers.ts` for `mockSupabase(page)` usage first, then mirror an
existing simple page spec (e.g. the `/overview` or `/runs` spec) for structure:

```ts
import { test, expect } from "@playwright/test";
import { mockSupabase } from "./helpers";

test.describe("Applications page", () => {
  test.beforeEach(async ({ page }) => {
    await mockSupabase(page);
    await page.route("**/api/applications", async (route) => {
      if (route.request().method() === "GET") {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            applications: [
              { id: "1", contact_id: null, company: "Acme", role: "PM", job_url: null,
                source: "manual", stage: "saved", applied_date: null, notes: null,
                posting_snapshot: null, created_at: "2026-08-26T00:00:00Z",
                updated_at: "2026-08-26T00:00:00Z" },
            ],
          }),
        });
      } else {
        await route.continue();
      }
    });
  });

  test("shows the applications table and nav link", async ({ page }) => {
    await page.goto("/applications");
    await expect(page.getByRole("link", { name: "Applications" })).toBeVisible();
    await expect(page.getByText("Acme")).toBeVisible();
    await page.screenshot({ path: "tests/e2e/screenshots/18-applications.png" });
  });
});
```

- [ ] **Step 4: Run the full e2e suite**

Run: `npm run test:e2e` (from `contact-manager/`)
Expected: all pass, including the new spec. Read the captured screenshot
(`tests/e2e/screenshots/18-applications.png`) and confirm it actually shows the applications table
and the new nav link before treating this step as done — per this repo's rule, a passing assertion
is not itself proof of correct visual output.

- [ ] **Step 5: Update the e2e test count**

In `contact-manager/CLAUDE.md`, update `**Current test count: 76**` to the new total (76 + however
many new `test(...)` blocks Step 3 added) and update the `(vitest: ... passed, playwright: ...
passed)` line to match the new totals from Task 7's and this task's runs.

- [ ] **Step 6: Commit**

```bash
git add src/components/Nav.tsx tests/e2e/00-shell.spec.ts tests/e2e/18-applications.spec.ts CLAUDE.md
git commit -m "feat: add Applications nav link and e2e coverage"
```

---

## Task 9: Docs and memory (Definition of Done)

**Files:**
- Modify: `/Users/kishoretheeraj/Documents/cold-email-agent/CLAUDE.md`
- Modify: `contact-manager/CLAUDE.md`
- Modify or create: `docs/python/db-schema.md`
- Create: memory file under
  `/Users/kishoretheeraj/.claude/projects/-Users-kishoretheeraj-Documents-cold-email-agent/memory/`

- [ ] **Step 1: Update the root CLAUDE.md**

Add a short new section after "Decision-context tagging" (before "See docs/python/reply-pipeline.md
for..."), pointing at the spec and describing the table in one paragraph — mirror the compactness of
the existing "Form D Funding Signal" section's opening paragraph, not its full length. Add a
pointer line: `Schema: see docs/python/db-schema.md.` if that file doesn't already get referenced
for it.

- [ ] **Step 2: Update `docs/python/db-schema.md`**

Add a `## job_applications` section documenting the table's columns (copy from the migration's
`CREATE TABLE`), the stage enum, and the governance note that `contact_id` is nullable and this
table is independent of `contacts.stage`.

- [ ] **Step 3: Update `contact-manager/CLAUDE.md`**

Add `api/applications/route.ts`, `api/applications/[id]/route.ts`, and `applications/page.tsx` to
the module layout tree, and `ApplicationsPage.tsx` to the components list. Add a short
`### /api/applications` subsection under "API route conventions" documenting the GET/POST/PATCH
contract (mirror the existing `/api/agent-config` subsection's format and length).

- [ ] **Step 4: Write the memory file**

Create
`/Users/kishoretheeraj/.claude/projects/-Users-kishoretheeraj-Documents-cold-email-agent/memory/project-job-application-tracking.md`:

```markdown
---
name: project-job-application-tracking
description: Phase 1 of full-fledged buildout - job_applications table, independent stage pipeline, /applications page
metadata:
  type: project
---

Shipped [DATE FILLED IN AT COMMIT TIME]: `job_applications` table (own stage enum:
saved -> applied -> phone_screen -> onsite -> offer/rejected/withdrawn/accepted),
deliberately independent of `contacts.stage`. Backend: db.py accessors
(create_job_application, get_job_applications, update_job_application_stage,
get_job_application), tests in tests/test_job_applications_db.py. Frontend:
/applications page in contact-manager with inline stage Select, API routes at
/api/applications and /api/applications/[id].

This is Phase 1 of [[project-full-fledged-buildout]] (see spec at
docs/superpowers/specs/2026-08-26-full-fledged-job-platform-buildout.md). Phase 2
(job/company discovery, including a manual-only JobRight puller) is next.
```

Also create a short `project-full-fledged-buildout.md` memory (type `project`) if one doesn't exist
yet, summarizing the overall 5-phase initiative and linking to the spec path, and add both to
`MEMORY.md`'s index.

- [ ] **Step 5: Final full-suite check across both halves of the repo**

Run: `python3 -m pytest` (from repo root) and `npm test && npm run test:e2e` (from
`contact-manager/`).
Expected: everything green.

- [ ] **Step 6: Commit**

```bash
git add CLAUDE.md contact-manager/CLAUDE.md docs/python/db-schema.md
git commit -m "docs: document job_applications table and Phase 1 completion"
```

At this point every box in this file is checked. The next hourly `build-continue.yml` run (or the
next interactive session) should open
`docs/superpowers/specs/2026-08-26-full-fledged-job-platform-buildout.md`, find Phase 2's stub, and
write its detailed plan using the `writing-plans` skill before starting it.
