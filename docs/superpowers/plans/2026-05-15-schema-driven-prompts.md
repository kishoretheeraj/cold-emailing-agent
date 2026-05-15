# Schema-Driven Prompts & Profile Page — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move all display metadata (title, description, sort order, default value) for prompt sections into the Supabase `prompts` table so the UI renders sections automatically from rows — adding a new prompt requires only an INSERT, no UI changes.

**Architecture:** Extend the `prompts` table with four new columns, add a generic `Prompt` type to replace the hardcoded `PromptMeta`/`DEFAULT_PROMPTS` constants, extract a pure `extractVariables()` helper to replace hardcoded chip arrays, and replace the monolithic `PromptsEditor` component with a page-level `PromptsPage` and a per-row `PromptSection`.

**Tech Stack:** Next.js 16 App Router, React 19, TypeScript strict, Tailwind CSS v4, @supabase/supabase-js (browser anon key), Vitest + Testing Library (jsdom).

---

## Codebase snapshot (read before touching anything)

```
contact-manager/
├── src/app/prompts/page.tsx          ← server wrapper; imports PromptsEditor
├── src/components/PromptsEditor.tsx  ← REPLACE (monolithic client component)
├── src/lib/defaultPrompts.ts         ← DELETE after migration
├── src/lib/types.ts                  ← ADD Prompt type
├── src/lib/supabase.ts               ← singleton client; do not change
└── src/components/
    ├── Field.tsx                     ← TextArea (use this, not raw <textarea>)
    └── Toast.tsx                     ← Toast + ToastTone; standalone pattern
```

**Files to create:**
- `src/lib/promptVariables.ts` — pure `extractVariables()` utility
- `src/lib/promptVariables.test.ts` — unit tests
- `src/components/PromptSection.tsx` — per-row edit/save/reset component
- `src/components/PromptSection.test.tsx` — component tests
- `src/components/PromptsPage.tsx` — replaces PromptsEditor
- `src/components/PromptsPage.test.tsx` — component tests

**Files to modify:**
- `src/lib/types.ts` — add `Prompt` type
- `src/app/prompts/page.tsx` — point at `PromptsPage`

**Files to delete (after grep):**
- `src/components/PromptsEditor.tsx`
- `src/lib/defaultPrompts.ts`

---

## Task 1 — Supabase migration: extend prompts table

**Files:**
- (none — SQL executed via Supabase MCP tool)

The current prompts table schema is: `key TEXT, value TEXT, updated_at TIMESTAMPTZ`.
The spec's migration omits `description` from the `ADD COLUMN` list but the `UPDATE` statements set it — add it here.

- [ ] **Step 1: Find the Supabase project ref**

  Use the Supabase MCP tool to list projects and identify the one for this app.
  The project URL comes from `NEXT_PUBLIC_SUPABASE_URL` in `contact-manager/.env.local`
  (or Vercel env vars). Match the hostname slug to the project ref.

  Tool: `mcp__plugin_supabase_supabase__list_projects`

- [ ] **Step 2: Run the migration SQL**

  Tool: `mcp__plugin_supabase_supabase__execute_sql` with the project ref from Step 1.

  ```sql
  -- Add new columns (all nullable for safe rollout)
  ALTER TABLE prompts ADD COLUMN description text;
  ALTER TABLE prompts ADD COLUMN display_title text;
  ALTER TABLE prompts ADD COLUMN default_value text;
  ALTER TABLE prompts ADD COLUMN sort_order integer NOT NULL DEFAULT 0;

  -- Populate display metadata from current values
  UPDATE prompts SET
    display_title = 'Sender Profile',
    description   = 'Injected as {profile} into every email template.',
    sort_order    = 10,
    default_value = value
  WHERE key = 'sender_profile';

  UPDATE prompts SET
    display_title = 'Outreach Email',
    description   = 'Used for cold intro, follow-up 1 & 2, and breakup emails.',
    sort_order    = 20,
    default_value = value
  WHERE key = 'outreach_prompt';

  UPDATE prompts SET
    display_title = 'Applied Intro',
    description   = 'Sent after submitting an ATS application.',
    sort_order    = 30,
    default_value = value
  WHERE key = 'applied_intro_prompt';

  UPDATE prompts SET
    display_title = 'Applied Follow-up',
    description   = 'Brief follow-up sent 5 days after the intro.',
    sort_order    = 40,
    default_value = value
  WHERE key = 'applied_followup_prompt';

  UPDATE prompts SET
    display_title = 'Subject Line',
    description   = 'Called once per first-touch email to generate the subject.',
    sort_order    = 50,
    default_value = value
  WHERE key = 'subject_prompt';

  -- Enforce NOT NULL on display_title now that all rows are populated
  ALTER TABLE prompts ALTER COLUMN display_title SET NOT NULL;
  ```

- [ ] **Step 3: Verify migration**

  Tool: `mcp__plugin_supabase_supabase__execute_sql`

  ```sql
  SELECT key, display_title, description, sort_order,
         left(default_value, 30) AS default_value_preview
  FROM prompts
  ORDER BY sort_order;
  ```

  Expected: 5 rows, all with non-null `display_title`, `description`, `sort_order`, and `default_value`.
  If any row has a null `display_title`, the UPDATE for that key failed — re-run just that UPDATE.

---

## Task 2 — Add `Prompt` type to types.ts

**Files:**
- Modify: `contact-manager/src/lib/types.ts`

- [ ] **Step 1: Add the Prompt type**

  Open `src/lib/types.ts`. Add this block after the `BulkImportWindow` type (before the re-exports line):

  ```typescript
  export type Prompt = {
    key: string;
    value: string;
    description: string | null;
    display_title: string;
    default_value: string | null;
    sort_order: number;
    updated_at: string;
  };
  ```

  Do not remove any existing types. Do not touch `Contact`, `ExtractedContact`, `ReviewContact`, `BulkImportWindow`, `ContactReviewStatus`, `Mode`, or `ReplyStatus`.

- [ ] **Step 2: Commit**

  Run from `contact-manager/`:
  ```bash
  npm run build
  ```
  Expected: no TypeScript errors on this file alone yet (other files still import `defaultPrompts.ts` — that is fine for now).

  ```bash
  git add src/lib/types.ts
  git commit -m "feat: add Prompt type to types.ts"
  ```

---

## Task 3 — Create `src/lib/promptVariables.ts` + test

**Files:**
- Create: `contact-manager/src/lib/promptVariables.ts`
- Create: `contact-manager/src/lib/promptVariables.test.ts`

- [ ] **Step 1: Write the failing test**

  Create `src/lib/promptVariables.test.ts`:

  ```typescript
  import { describe, it, expect } from "vitest";
  import { extractVariables } from "./promptVariables";

  describe("extractVariables", () => {
    it("extracts named variables in order", () => {
      expect(
        extractVariables("Hello {name}, you are at {company}")
      ).toEqual(["name", "company"]);
    });

    it("returns empty array when no placeholders", () => {
      expect(extractVariables("No placeholders")).toEqual([]);
    });

    it("deduplicates preserving first-occurrence order", () => {
      expect(extractVariables("{a} {b} {a} {c}")).toEqual(["a", "b", "c"]);
    });

    it("returns empty array for empty string", () => {
      expect(extractVariables("")).toEqual([]);
    });

    it("ignores patterns with spaces (e.g. double-brace content), matches valid identifiers", () => {
      // {{not a single}} has a space so \w+ won't match; {valid} will
      expect(extractVariables("{{not a single}} {valid}")).toEqual(["valid"]);
    });
  });
  ```

- [ ] **Step 2: Run test — confirm it fails**

  ```bash
  cd contact-manager && npm test -- promptVariables
  ```

  Expected: FAIL — "Cannot find module './promptVariables'"

- [ ] **Step 3: Implement `promptVariables.ts`**

  Create `src/lib/promptVariables.ts`:

  ```typescript
  // Matches single-brace {identifier} only; \w+ requires word chars so
  // patterns with spaces (e.g., {{multi word}}) never match.
  export function extractVariables(template: string): string[] {
    const matches = template.matchAll(/\{(\w+)\}/g);
    const seen = new Set<string>();
    const out: string[] = [];
    for (const m of matches) {
      if (!seen.has(m[1])) {
        seen.add(m[1]);
        out.push(m[1]);
      }
    }
    return out;
  }
  ```

- [ ] **Step 4: Run test — confirm it passes**

  ```bash
  npm test -- promptVariables
  ```

  Expected: PASS — 5 tests passing.

- [ ] **Step 5: Commit**

  ```bash
  git add src/lib/promptVariables.ts src/lib/promptVariables.test.ts
  git commit -m "feat: add extractVariables utility"
  ```

---

## Task 4 — Create `PromptSection.tsx` + test

**Files:**
- Create: `contact-manager/src/components/PromptSection.tsx`
- Create: `contact-manager/src/components/PromptSection.test.tsx`

- [ ] **Step 1: Write the failing tests**

  Create `src/components/PromptSection.test.tsx`:

  ```tsx
  import { describe, it, expect, vi, beforeEach } from "vitest";
  import { render, screen, waitFor } from "@testing-library/react";
  import userEvent from "@testing-library/user-event";
  import type { Prompt } from "@/lib/types";

  // Build the mock supabase chain for:
  //   supabase.from("prompts").update({...}).eq("key", key).select().single()
  const singleMock = vi.fn();
  const selectAfterEqMock = vi.fn(() => ({ single: singleMock }));
  const eqMock = vi.fn(() => ({ select: selectAfterEqMock }));
  const updateMock = vi.fn(() => ({ eq: eqMock }));

  vi.mock("@/lib/supabase", () => ({
    supabase: {
      from: vi.fn(() => ({ update: updateMock })),
    },
  }));

  import { PromptSection } from "./PromptSection";

  const basePrompt: Prompt = {
    key: "outreach_prompt",
    value: "Hello {name} at {company}",
    display_title: "Outreach Email",
    description: "Used for cold intro emails.",
    default_value: "Default outreach value",
    sort_order: 20,
    updated_at: "2026-05-01T10:00:00.000Z",
  };

  beforeEach(() => {
    updateMock.mockReset();
    singleMock.mockReset();
    updateMock.mockReturnValue({ eq: eqMock });
    eqMock.mockReturnValue({ select: selectAfterEqMock });
    selectAfterEqMock.mockReturnValue({ single: singleMock });
  });

  describe("PromptSection", () => {
    it("renders display_title, description, value in textarea, formatted updated_at", () => {
      render(
        <PromptSection prompt={basePrompt} onSaved={vi.fn()} onError={vi.fn()} />
      );
      expect(screen.getByText("Outreach Email")).toBeInTheDocument();
      expect(screen.getByText("Used for cold intro emails.")).toBeInTheDocument();
      expect(screen.getByRole("textbox")).toHaveValue("Hello {name} at {company}");
      expect(screen.getByText(/Last saved:/)).toBeInTheDocument();
    });

    it("hides variables row when value has no placeholders", () => {
      render(
        <PromptSection
          prompt={{ ...basePrompt, value: "No placeholders here" }}
          onSaved={vi.fn()}
          onError={vi.fn()}
        />
      );
      // No code chips rendered
      expect(document.querySelector("code")).toBeNull();
    });

    it("shows variables row with deduplicated placeholders from draft", () => {
      render(
        <PromptSection
          prompt={{ ...basePrompt, value: "{x} {y} {x}" }}
          onSaved={vi.fn()}
          onError={vi.fn()}
        />
      );
      // Only {x} and {y}, not a second {x}
      expect(screen.getByText("{x}")).toBeInTheDocument();
      expect(screen.getByText("{y}")).toBeInTheDocument();
      expect(screen.getAllByText("{x}")).toHaveLength(1);
    });

    it("Save button disabled when draft equals prompt.value", () => {
      render(
        <PromptSection prompt={basePrompt} onSaved={vi.fn()} onError={vi.fn()} />
      );
      expect(screen.getByRole("button", { name: "Save changes" })).toBeDisabled();
    });

    it("Save button enabled after editing textarea", async () => {
      const user = userEvent.setup();
      render(
        <PromptSection prompt={basePrompt} onSaved={vi.fn()} onError={vi.fn()} />
      );
      await user.type(screen.getByRole("textbox"), " extra");
      expect(screen.getByRole("button", { name: "Save changes" })).not.toBeDisabled();
    });

    it("Save calls supabase update with new value and correct key, calls onSaved with returned row", async () => {
      const user = userEvent.setup();
      const onSaved = vi.fn();
      const updatedRow: Prompt = {
        ...basePrompt,
        value: "Hello {name} at {company} extra",
        updated_at: "2026-05-15T12:00:00.000Z",
      };
      singleMock.mockResolvedValueOnce({ data: updatedRow, error: null });

      render(
        <PromptSection prompt={basePrompt} onSaved={onSaved} onError={vi.fn()} />
      );
      await user.type(screen.getByRole("textbox"), " extra");
      await user.click(screen.getByRole("button", { name: "Save changes" }));

      await waitFor(() => {
        expect(updateMock).toHaveBeenCalledWith(
          expect.objectContaining({ value: "Hello {name} at {company} extra" })
        );
        expect(eqMock).toHaveBeenCalledWith("key", "outreach_prompt");
        expect(onSaved).toHaveBeenCalledWith(updatedRow);
      });
    });

    it("Save calls onError on supabase failure", async () => {
      const user = userEvent.setup();
      const onError = vi.fn();
      singleMock.mockResolvedValueOnce({ data: null, error: { message: "DB error" } });

      render(
        <PromptSection prompt={basePrompt} onSaved={vi.fn()} onError={onError} />
      );
      await user.type(screen.getByRole("textbox"), " extra");
      await user.click(screen.getByRole("button", { name: "Save changes" }));

      await waitFor(() => {
        expect(onError).toHaveBeenCalledWith(
          "Save failed — check Supabase connection"
        );
      });
    });

    it("Reset button disabled when default_value is null", () => {
      render(
        <PromptSection
          prompt={{ ...basePrompt, default_value: null }}
          onSaved={vi.fn()}
          onError={vi.fn()}
        />
      );
      expect(screen.getByRole("button", { name: "Reset to default" })).toBeDisabled();
    });

    it("Reset button disabled when draft already equals default_value", () => {
      // value and default_value are identical so draft starts equal to default
      render(
        <PromptSection
          prompt={{ ...basePrompt, value: "Default outreach value" }}
          onSaved={vi.fn()}
          onError={vi.fn()}
        />
      );
      expect(screen.getByRole("button", { name: "Reset to default" })).toBeDisabled();
    });

    it("Reset shows confirm dialog then sets draft to default_value, does NOT auto-save", async () => {
      const user = userEvent.setup();
      vi.spyOn(window, "confirm").mockReturnValueOnce(true);

      render(
        <PromptSection prompt={basePrompt} onSaved={vi.fn()} onError={vi.fn()} />
      );

      // basePrompt.value !== basePrompt.default_value so reset button is already enabled
      await user.click(screen.getByRole("button", { name: "Reset to default" }));

      expect(window.confirm).toHaveBeenCalledWith(
        "Reset this prompt to its default? Your current changes will be lost."
      );
      expect(screen.getByRole("textbox")).toHaveValue("Default outreach value");
      // No supabase call — Reset does NOT auto-save
      expect(updateMock).not.toHaveBeenCalled();
    });

    it("resets draft to new prompt.value when prop changes", () => {
      const { rerender } = render(
        <PromptSection prompt={basePrompt} onSaved={vi.fn()} onError={vi.fn()} />
      );
      expect(screen.getByRole("textbox")).toHaveValue("Hello {name} at {company}");

      rerender(
        <PromptSection
          prompt={{ ...basePrompt, value: "New value from parent" }}
          onSaved={vi.fn()}
          onError={vi.fn()}
        />
      );
      expect(screen.getByRole("textbox")).toHaveValue("New value from parent");
    });
  });
  ```

- [ ] **Step 2: Run test — confirm it fails**

  ```bash
  npm test -- PromptSection
  ```

  Expected: FAIL — "Cannot find module './PromptSection'"

- [ ] **Step 3: Implement `PromptSection.tsx`**

  Create `src/components/PromptSection.tsx`:

  ```tsx
  "use client";

  import { useEffect, useState } from "react";
  import { supabase } from "@/lib/supabase";
  import { TextArea } from "./Field";
  import { extractVariables } from "@/lib/promptVariables";
  import type { Prompt } from "@/lib/types";

  export function PromptSection({
    prompt,
    onSaved,
    onError,
  }: {
    prompt: Prompt;
    onSaved: (updated: Prompt) => void;
    onError: (message: string) => void;
  }) {
    const [draft, setDraft] = useState(prompt.value);
    const [saving, setSaving] = useState(false);

    useEffect(() => {
      setDraft(prompt.value);
    }, [prompt.value]);

    const variables = extractVariables(draft);
    const isDirty = draft !== prompt.value;
    const canReset =
      prompt.default_value !== null && draft !== prompt.default_value;

    async function handleSave() {
      setSaving(true);
      const now = new Date().toISOString();
      const { data, error } = await supabase
        .from("prompts")
        .update({ value: draft, updated_at: now })
        .eq("key", prompt.key)
        .select()
        .single();
      if (error || !data) {
        onError("Save failed — check Supabase connection");
        setSaving(false);
        return;
      }
      onSaved(data as Prompt);
      setSaving(false);
    }

    function handleReset() {
      if (!prompt.default_value) return;
      if (
        !window.confirm(
          "Reset this prompt to its default? Your current changes will be lost."
        )
      )
        return;
      setDraft(prompt.default_value);
    }

    return (
      <div className="rounded-xl border border-border bg-surface p-6 mb-6">
        <div className="flex flex-wrap items-start justify-between gap-3 mb-3">
          <div>
            <h3 className="text-xl font-semibold text-fg">
              {prompt.display_title}
            </h3>
            {prompt.description && (
              <p className="text-sm text-fg-muted mt-1">{prompt.description}</p>
            )}
          </div>
          <div className="flex items-center gap-2 shrink-0">
            <button
              onClick={handleReset}
              disabled={!canReset}
              className="rounded-md border border-border px-3 py-1.5 text-xs text-fg-muted hover:text-fg hover:border-border-strong transition disabled:opacity-40 disabled:cursor-not-allowed"
            >
              Reset to default
            </button>
            <button
              onClick={handleSave}
              disabled={!isDirty || saving}
              className="rounded-md bg-indigo-500 px-3 py-1.5 text-xs font-medium text-white transition hover:bg-indigo-400 disabled:bg-indigo-500/30 disabled:cursor-not-allowed"
            >
              {saving ? "Saving..." : "Save changes"}
            </button>
          </div>
        </div>

        {variables.length > 0 && (
          <div className="flex flex-wrap gap-1.5 mb-3">
            {variables.map((v) => (
              <code
                key={v}
                className="rounded border border-border bg-surface-2 px-1.5 py-0.5 text-xs text-fg-muted font-mono"
              >
                {`{${v}}`}
              </code>
            ))}
          </div>
        )}

        <TextArea
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          spellCheck={false}
          className="font-mono text-xs leading-relaxed min-h-[24rem] resize-y"
        />

        <p className="text-xs text-fg-dim mt-3">
          Last saved: {formatTimestamp(prompt.updated_at)}
          {isDirty && (
            <span className="ml-2 text-yellow-400">unsaved changes</span>
          )}
        </p>
      </div>
    );
  }

  function formatTimestamp(iso: string) {
    const d = new Date(iso);
    if (!iso || Number.isNaN(d.getTime())) return "never";
    return new Intl.DateTimeFormat(undefined, {
      month: "short",
      day: "numeric",
      hour: "numeric",
      minute: "2-digit",
      hour12: true,
    }).format(d);
  }
  ```

- [ ] **Step 4: Run test — confirm it passes**

  ```bash
  npm test -- PromptSection
  ```

  Expected: PASS — 10 tests passing.

- [ ] **Step 5: Commit**

  ```bash
  git add src/components/PromptSection.tsx src/components/PromptSection.test.tsx
  git commit -m "feat: add PromptSection component"
  ```

---

## Task 5 — Create `PromptsPage.tsx` + test

**Files:**
- Create: `contact-manager/src/components/PromptsPage.tsx`
- Create: `contact-manager/src/components/PromptsPage.test.tsx`

- [ ] **Step 1: Write the failing tests**

  Create `src/components/PromptsPage.test.tsx`:

  ```tsx
  import { describe, it, expect, vi, beforeEach } from "vitest";
  import { render, screen, waitFor } from "@testing-library/react";
  import type { Prompt } from "@/lib/types";

  // Mock supabase chain: supabase.from("prompts").select("*").order(...)
  const orderMock = vi.fn();

  vi.mock("@/lib/supabase", () => ({
    supabase: {
      from: vi.fn(() => ({
        select: vi.fn(() => ({
          order: orderMock,
        })),
      })),
    },
  }));

  // Mock PromptSection to avoid deep supabase update chain in page-level tests
  vi.mock("./PromptSection", () => ({
    PromptSection: ({ prompt }: { prompt: Prompt }) => (
      <div data-testid="prompt-section">{prompt.display_title}</div>
    ),
  }));

  import { PromptsPage } from "./PromptsPage";

  const mockPrompts: Prompt[] = [
    {
      key: "sender_profile",
      value: "My profile",
      display_title: "Sender Profile",
      description: "Injected as {profile}",
      default_value: "My profile",
      sort_order: 10,
      updated_at: "2026-05-01T10:00:00.000Z",
    },
    {
      key: "outreach_prompt",
      value: "Outreach body",
      display_title: "Outreach Email",
      description: "Used for cold intro",
      default_value: "Outreach body",
      sort_order: 20,
      updated_at: "2026-05-02T10:00:00.000Z",
    },
  ];

  beforeEach(() => {
    orderMock.mockReset();
  });

  describe("PromptsPage", () => {
    it("shows loading state initially", () => {
      // Return a never-resolving promise to hold the loading state
      orderMock.mockReturnValue(new Promise(() => {}));
      render(<PromptsPage />);
      expect(screen.getByText("Loading...")).toBeInTheDocument();
    });

    it("fetches prompts ordered by sort_order and renders one PromptSection per row", async () => {
      orderMock.mockResolvedValueOnce({ data: mockPrompts, error: null });
      render(<PromptsPage />);

      await waitFor(() => {
        expect(screen.queryByText("Loading...")).toBeNull();
      });

      expect(orderMock).toHaveBeenCalledWith("sort_order", { ascending: true });
      const sections = screen.getAllByTestId("prompt-section");
      expect(sections).toHaveLength(2);
      expect(sections[0]).toHaveTextContent("Sender Profile");
      expect(sections[1]).toHaveTextContent("Outreach Email");
    });

    it("shows empty state when fetch returns []", async () => {
      orderMock.mockResolvedValueOnce({ data: [], error: null });
      render(<PromptsPage />);

      await waitFor(() => {
        expect(
          screen.getByText(/No prompts configured/)
        ).toBeInTheDocument();
      });
    });

    it("shows error state when fetch errors", async () => {
      orderMock.mockResolvedValueOnce({
        data: null,
        error: { message: "connection refused" },
      });
      render(<PromptsPage />);

      await waitFor(() => {
        expect(screen.getByText("connection refused")).toBeInTheDocument();
      });
    });
  });
  ```

- [ ] **Step 2: Run test — confirm it fails**

  ```bash
  npm test -- PromptsPage
  ```

  Expected: FAIL — "Cannot find module './PromptsPage'"

- [ ] **Step 3: Implement `PromptsPage.tsx`**

  Create `src/components/PromptsPage.tsx`:

  ```tsx
  "use client";

  import { useEffect, useState } from "react";
  import Link from "next/link";
  import { supabase } from "@/lib/supabase";
  import { PromptSection } from "./PromptSection";
  import { Toast, type ToastTone } from "./Toast";
  import type { Prompt } from "@/lib/types";

  export function PromptsPage() {
    const [prompts, setPrompts] = useState<Prompt[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [toast, setToast] = useState<{
      kind: ToastTone;
      message: string;
    } | null>(null);

    useEffect(() => {
      async function load() {
        const { data, error: err } = await supabase
          .from("prompts")
          .select("*")
          .order("sort_order", { ascending: true });
        if (err) {
          setError(err.message);
          setLoading(false);
          return;
        }
        setPrompts((data as Prompt[]) ?? []);
        setLoading(false);
      }
      load();
    }, []);

    function handleSaved(updated: Prompt) {
      setPrompts((prev) =>
        prev.map((p) => (p.key === updated.key ? updated : p))
      );
      setToast({ kind: "success", message: "Saved" });
    }

    function handleError(message: string) {
      setToast({ kind: "error", message });
    }

    return (
      <div className="mx-auto max-w-4xl px-4 sm:px-6 py-8 sm:py-12">
        <header className="flex items-start justify-between gap-4 mb-10">
          <div className="space-y-1">
            <h1 className="text-2xl font-semibold text-fg">Prompts & Profile</h1>
            <p className="text-sm text-fg-muted">
              Changes take effect on the next agent run (8am EST).
            </p>
          </div>
          <Link
            href="/"
            className="shrink-0 flex items-center gap-1 rounded-lg border border-border px-3 py-1.5 text-xs text-fg-muted hover:text-fg hover:border-border-strong transition"
          >
            <svg
              xmlns="http://www.w3.org/2000/svg"
              width="12"
              height="12"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
              aria-hidden="true"
            >
              <polyline points="15 18 9 12 15 6" />
            </svg>
            Contacts
          </Link>
        </header>

        {loading && <p className="text-sm text-fg-muted">Loading...</p>}

        {error && <p className="text-sm text-red-400">{error}</p>}

        {!loading && !error && prompts.length === 0 && (
          <p className="text-sm text-fg-muted">
            No prompts configured. Run the seed migration in the agent repo.
          </p>
        )}

        {!loading &&
          !error &&
          prompts.map((prompt) => (
            <PromptSection
              key={prompt.key}
              prompt={prompt}
              onSaved={handleSaved}
              onError={handleError}
            />
          ))}

        {toast && (
          <Toast
            message={toast.message}
            tone={toast.kind}
            onClose={() => setToast(null)}
          />
        )}
      </div>
    );
  }
  ```

- [ ] **Step 4: Run test — confirm it passes**

  ```bash
  npm test -- PromptsPage
  ```

  Expected: PASS — 4 tests passing.

- [ ] **Step 5: Commit**

  ```bash
  git add src/components/PromptsPage.tsx src/components/PromptsPage.test.tsx
  git commit -m "feat: add PromptsPage component"
  ```

---

## Task 6 — Wire `src/app/prompts/page.tsx` to use `PromptsPage`

**Files:**
- Modify: `contact-manager/src/app/prompts/page.tsx`

- [ ] **Step 1: Update the route file**

  Replace the entire file content:

  ```tsx
  import { PromptsPage } from "@/components/PromptsPage";

  export const metadata = { title: "Prompts & Profile — Cold Email Ops" };

  export default function Page() {
    return <PromptsPage />;
  }
  ```

- [ ] **Step 2: Commit**

  ```bash
  git add src/app/prompts/page.tsx
  git commit -m "feat: wire prompts route to PromptsPage"
  ```

---

## Task 7 — Delete obsolete hardcoded files

**Files:**
- Delete: `contact-manager/src/components/PromptsEditor.tsx`
- Delete: `contact-manager/src/lib/defaultPrompts.ts`

- [ ] **Step 1: Confirm no other imports of PromptsEditor**

  ```bash
  grep -r "PromptsEditor" contact-manager/src --include="*.ts" --include="*.tsx"
  ```

  Expected: no output (only `page.tsx` imported it, and we updated that in Task 6).
  If any other file still imports it, update that file before deleting.

- [ ] **Step 2: Confirm no other imports of defaultPrompts**

  ```bash
  grep -r "defaultPrompts" contact-manager/src --include="*.ts" --include="*.tsx"
  ```

  Expected: no output (only `PromptsEditor.tsx` imported it, and it's being deleted).
  If any other file imports it, update that file before deleting.

- [ ] **Step 3: Delete the files**

  ```bash
  rm contact-manager/src/components/PromptsEditor.tsx
  rm contact-manager/src/lib/defaultPrompts.ts
  ```

- [ ] **Step 4: Commit**

  ```bash
  git add -A
  git commit -m "chore: delete PromptsEditor and defaultPrompts (replaced by PromptsPage + PromptSection)"
  ```

---

## Task 8 — Build check and full test run

**Files:** (none — verification only)

- [ ] **Step 1: Run full build**

  ```bash
  cd contact-manager && npm run build
  ```

  Expected: exit 0, no TypeScript errors. Common issues to watch for:
  - `data as Prompt` cast — if supabase types complain, use `data as unknown as Prompt`
  - `err.message` on supabase error — `error` from supabase-js has `.message: string`; if TypeScript disagrees, narrow with `String(err.message ?? err)`

  Fix any errors before proceeding.

- [ ] **Step 2: Run full test suite**

  ```bash
  npm test
  ```

  Expected: all tests pass. Confirm tests from these files are present and green:
  - `src/lib/promptVariables.test.ts` — 5 tests
  - `src/components/PromptSection.test.tsx` — 10 tests
  - `src/components/PromptsPage.test.tsx` — 4 tests
  - All pre-existing tests (App, ContactsList, Field, ReviewFlow, SmartInput, StructuredForm, Toast)

- [ ] **Step 3: STOP — report results. Do not deploy.**

  Report:
  - Build exit code
  - Test count: passing / failing / skipped
  - Any TypeScript errors encountered and how they were fixed
  - Files deleted (confirm: `PromptsEditor.tsx`, `defaultPrompts.ts`)

---

## Manual verification checklist (after merge — do NOT run during plan execution)

1. Open `/prompts`. Five sections appear in order: Sender Profile, Outreach Email, Applied Intro, Applied Follow-up, Subject Line. Each shows title, description, variables chips from live content, textarea, "Last saved" timestamp.
2. Edit Sender Profile (add a space). Save button enables. Click Save. Toast "Saved" appears. `updated_at` refreshes.
3. Click Reset to default on any prompt. Confirm dialog appears. Confirm. Textarea reverts to seeded default. Click Save to persist.
4. In Supabase dashboard, INSERT a new row: `key='test_prompt', value='test', display_title='Test Prompt', description='A test.', sort_order=100, default_value='test'`. Refresh `/prompts`. New section appears at bottom — no UI changes deployed. Delete the test row when done.
5. Trigger agent run. Confirm agent logs show prompts loaded from Supabase (existing `load_prompts()` call — regression check only).

---

## Files to delete (summary for PR description)

| File | Reason |
|---|---|
| `src/components/PromptsEditor.tsx` | Replaced by `PromptsPage.tsx` + `PromptSection.tsx` |
| `src/lib/defaultPrompts.ts` | `DEFAULT_PROMPTS` is now `prompts.default_value` in Supabase; `PROMPT_META` is now the rows themselves |

After this ships, new prompts require only an INSERT into the `prompts` table — sort_order convention: use gaps of 10, future prompts planned at 25 (`critic_prompt`), 28 (`research_injection`), 90 (`retrospective_analyze_prompt`), 95 (`retrospective_propose_prompt`).
