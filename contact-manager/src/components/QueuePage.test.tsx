import React from "react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import {
  render,
  screen,
  waitFor,
  act,
  fireEvent,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";

// ── Sonner mock ───────────────────────────────────────────────────────────────

const { toastMock } = vi.hoisted(() => {
  const toastFn = vi.fn() as ReturnType<typeof vi.fn> & {
    success: ReturnType<typeof vi.fn>;
    error: ReturnType<typeof vi.fn>;
    info: ReturnType<typeof vi.fn>;
    dismiss: ReturnType<typeof vi.fn>;
  };
  toastFn.success = vi.fn();
  toastFn.error = vi.fn();
  toastFn.info = vi.fn();
  toastFn.dismiss = vi.fn();
  return { toastMock: toastFn };
});

vi.mock("sonner", () => ({
  toast: toastMock,
}));

// ── Tooltip mock (Radix requires TooltipProvider; mock the wrapper in tests) ──

vi.mock("@/components/ui/Tooltip", () => ({
  Tooltip: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));

// ── Supabase mock ─────────────────────────────────────────────────────────────

const { selectContactsMock, selectDraftsMock, selectEventsMock, updateMock, neqMock } =
  vi.hoisted(() => ({
    selectContactsMock: vi.fn(),
    selectDraftsMock: vi.fn(),
    selectEventsMock: vi.fn(),
    updateMock: vi.fn(),
    neqMock: vi.fn(),
  }));

vi.mock("@/lib/supabase", () => {
  function makeReadChain(terminalMock: ReturnType<typeof vi.fn>) {
    const chain: Record<string, unknown> = {};
    for (const m of ["select", "in", "is", "order", "eq", "limit"]) {
      chain[m] = vi.fn(() => chain);
    }
    // The last `.order()` in the drafts/events chain returns a Promise
    // but QueuePage uses Promise.all with the whole chain — we need the
    // chain to be thenable. We make `order` return a new object for the
    // contacts chain (which actually calls the terminal).
    // Simplification: make the chain itself thenable via `then`.
    chain.neq = vi.fn((...args: unknown[]) => { neqMock(...args); return chain; });
    chain.then = terminalMock;
    return chain;
  }

  let callCount = 0;
  return {
    supabase: {
      from: vi.fn((table: string) => {
        if (table === "contacts") {
          const chain = makeReadChain(selectContactsMock);
          return { select: vi.fn(() => chain), update: vi.fn(() => ({ eq: updateMock })) };
        }
        if (table === "draft_history") {
          return { select: vi.fn(() => makeReadChain(selectDraftsMock)) };
        }
        if (table === "agent_events") {
          return { select: vi.fn(() => makeReadChain(selectEventsMock)) };
        }
        // default: contacts update path
        callCount++;
        return {
          select: vi.fn(() => makeReadChain(selectContactsMock)),
          update: vi.fn(() => ({ eq: updateMock })),
        };
      }),
    },
  };
});

// ── Component import (after mocks) ────────────────────────────────────────────

import { QueuePage } from "./QueuePage";
import type { Contact, DraftHistory, AgentEvent } from "@/lib/types";

// ── Fixtures ──────────────────────────────────────────────────────────────────

function makeContact(overrides: Partial<Contact> = {}): Contact {
  return {
    id: "2",
    name: "Bob Martinez",
    email: "bob@bolt.com",
    company: "Bolt Inc",
    role: "VP Engineering",
    detail: null,
    tier: 1,
    mode: "outreach",
    stage: "first_touch_drafted",
    reply_status: "no_reply",
    classifier_status: null,
    dartmouth: true,
    job_title: null,
    job_description: null,
    company_applied: null,
    applied_date: null,
    followup_date: null,
    notes: null,
    resume_url: null,
    created_at: "2026-05-20T10:00:00Z",
    message_id: "msg-thread-2",
    last_emailed: null,
    deleted_at: null,
    ...overrides,
  };
}

function makeDraft(overrides: Partial<DraftHistory> = {}): DraftHistory {
  return {
    id: 1,
    contact_id: 2,
    stage: "first_touch_drafted",
    subject: "Quick intro",
    body: "Hi Bob,\n\nI noticed Bolt Inc and wanted to reach out. T'22 Dartmouth alum here.\n\nKishore",
    message_id: "msg-thread-2",
    gmail_draft_id: "draft-id-1",
    drafted_at: "2026-05-20T10:00:00.000Z",
    sent_subject: null,
    sent_body: null,
    sent_at: null,
    edit_detected: null,
    ...overrides,
  };
}

const bob = makeContact();
const bobDraft = makeDraft();

const dave = makeContact({
  id: "4",
  name: "Dave Johnson",
  company: "Delta Corp",
  tier: 2,
  stage: "followup1_drafted",
  dartmouth: false,
});
const daveDraft = makeDraft({
  id: 2,
  contact_id: 4,
  stage: "followup1_drafted",
  subject: "Re: Quick intro",
  gmail_draft_id: "draft-id-2",
});

// ── Helpers ───────────────────────────────────────────────────────────────────

function mockSupabaseData(
  contacts: Contact[],
  drafts: DraftHistory[] = [],
  events: AgentEvent[] = []
) {
  selectContactsMock.mockImplementation(
    (resolve: (v: unknown) => void) => resolve({ data: contacts })
  );
  selectDraftsMock.mockImplementation(
    (resolve: (v: unknown) => void) => resolve({ data: drafts })
  );
  selectEventsMock.mockImplementation(
    (resolve: (v: unknown) => void) => resolve({ data: events })
  );
}

// ── Tests ─────────────────────────────────────────────────────────────────────

beforeEach(() => {
  vi.clearAllMocks();
  mockSupabaseData([bob, dave], [bobDraft, daveDraft]);
  vi.spyOn(global, "fetch").mockResolvedValue(
    new Response(JSON.stringify({ ok: true, stage: "first_touch_sent" }), {
      status: 200,
    })
  );
});

afterEach(() => {
  vi.useRealTimers();
});

describe("QueuePage — rendering", () => {
  it("renders queue rows after data load", async () => {
    render(<QueuePage />);
    await waitFor(() => {
      const items = screen.getAllByText("Bob Martinez");
      expect(items.length).toBeGreaterThan(0);
    });
    // Dave should also appear in the list
    expect(screen.getAllByText("Dave Johnson").length).toBeGreaterThan(0);
  });

  it("shows empty state when no contacts", async () => {
    mockSupabaseData([], []);
    render(<QueuePage />);
    await waitFor(() => {
      const els = screen.getAllByText(/queue is empty/i);
      expect(els.length).toBeGreaterThan(0);
    });
  });

  it("shows contact count badge in left rail", async () => {
    render(<QueuePage />);
    await waitFor(() => {
      expect(screen.getAllByText("Bob Martinez").length).toBeGreaterThan(0);
    });
    // Count badge in left rail should show 2
    const badge = screen.getAllByText("2").find(
      (el) => el.className?.includes("indigo")
    );
    expect(badge).toBeTruthy();
  });

  it("renders subject and body first line in row", async () => {
    render(<QueuePage />);
    await waitFor(() => {
      expect(screen.getAllByText("Quick intro").length).toBeGreaterThan(0);
    });
    // Body first line appears in list row
    expect(screen.getAllByText("Hi Bob,").length).toBeGreaterThan(0);
  });
});

describe("QueuePage — keyboard navigation", () => {
  it("j moves focus to next row", async () => {
    render(<QueuePage />);
    await waitFor(() => screen.getByRole("heading", { name: "Bob Martinez" }));

    fireEvent.keyDown(document, { key: "j" });
    await waitFor(() =>
      expect(
        screen.getByRole("heading", { name: "Dave Johnson" })
      ).toBeInTheDocument()
    );
  });

  it("k moves focus to previous row", async () => {
    render(<QueuePage />);
    await waitFor(() => screen.getByRole("heading", { name: "Bob Martinez" }));

    fireEvent.keyDown(document, { key: "j" });
    await waitFor(() =>
      screen.getByRole("heading", { name: "Dave Johnson" })
    );

    fireEvent.keyDown(document, { key: "k" });
    await waitFor(() =>
      expect(
        screen.getByRole("heading", { name: "Bob Martinez" })
      ).toBeInTheDocument()
    );
  });

  it("G jumps to last row", async () => {
    render(<QueuePage />);
    await waitFor(() => screen.getByRole("heading", { name: "Bob Martinez" }));

    fireEvent.keyDown(document, { key: "G" });
    await waitFor(() =>
      screen.getByRole("heading", { name: "Dave Johnson" })
    );
  });

  it("g g jumps to first row", async () => {
    render(<QueuePage />);
    await waitFor(() => screen.getByRole("heading", { name: "Bob Martinez" }));

    // Move to last
    fireEvent.keyDown(document, { key: "G" });
    await waitFor(() => screen.getByRole("heading", { name: "Dave Johnson" }));

    // g g sequence within 500ms
    fireEvent.keyDown(document, { key: "g" });
    fireEvent.keyDown(document, { key: "g" });
    await waitFor(() =>
      screen.getByRole("heading", { name: "Bob Martinez" })
    );
  });

  it("early-returns when input is focused", async () => {
    render(<QueuePage />);
    await waitFor(() => screen.getByRole("heading", { name: "Bob Martinez" }));

    // Open quick fix to get an input focused
    fireEvent.keyDown(document, { key: "E" });
    await waitFor(() => screen.getAllByRole("textbox").length >= 2);

    // Focus the subject input
    const input = screen.getAllByRole("textbox")[0];
    input.focus();

    // Press j — should NOT navigate since input is focused
    fireEvent.keyDown(document, { key: "j" });

    // Right column still shows Bob (quick fix is for Bob)
    expect(screen.queryByRole("heading", { name: "Dave Johnson" })).toBeNull();
  });
});

describe("QueuePage — 5-second undo flow", () => {
  it("e key triggers approve: shows toast, does NOT call API immediately", async () => {
    vi.useFakeTimers();
    render(<QueuePage />);
    await act(async () => {
      await Promise.resolve(); // flush microtasks for data load
    });

    // Trigger approve
    await act(async () => {
      fireEvent.keyDown(document, { key: "e" });
    });

    expect(toastMock).toHaveBeenCalled();
    expect(global.fetch).not.toHaveBeenCalled();
  });

  it("undo cancels the send — API never called", async () => {
    vi.useFakeTimers();

    let capturedAction: ((e: MouseEvent) => void) | undefined;
    toastMock.mockImplementation(
      (_msg: string, opts?: { action?: { onClick: (e: MouseEvent) => void } }) => {
        capturedAction = opts?.action?.onClick;
        return "toast-id-1";
      }
    );

    render(<QueuePage />);
    await act(async () => {
      await Promise.resolve();
    });

    await act(async () => {
      fireEvent.keyDown(document, { key: "e" });
    });

    // Trigger undo
    await act(async () => {
      capturedAction?.(new MouseEvent("click") as unknown as MouseEvent);
    });

    // Advance past 5s — API should NOT fire
    await act(async () => {
      vi.advanceTimersByTime(6000);
    });

    expect(global.fetch).not.toHaveBeenCalled();
    expect(toastMock.dismiss).toHaveBeenCalled();
    expect(toastMock.info).toHaveBeenCalledWith("Send canceled");
  });

  it("timer expiry fires POST /api/send-draft", async () => {
    vi.useFakeTimers();
    render(<QueuePage />);
    await act(async () => {
      await Promise.resolve();
    });

    await act(async () => {
      fireEvent.keyDown(document, { key: "e" });
    });

    // Advance 5 seconds
    await act(async () => {
      vi.advanceTimersByTime(5001);
      await Promise.resolve(); // flush post-timer microtasks
    });

    expect(global.fetch).toHaveBeenCalledWith(
      "/api/send-draft",
      expect.objectContaining({ method: "POST" })
    );
  });
});

describe("QueuePage — Quick Fix", () => {
  it("E key opens quick fix with textareas", async () => {
    render(<QueuePage />);
    await waitFor(() => screen.getByRole("heading", { name: "Bob Martinez" }));

    fireEvent.keyDown(document, { key: "E" });
    await waitFor(() => {
      const textareas = screen.getAllByRole("textbox");
      expect(textareas.length).toBeGreaterThanOrEqual(2);
    });
  });

  it("Cancel restores read-only view", async () => {
    render(<QueuePage />);
    await waitFor(() => screen.getByRole("heading", { name: "Bob Martinez" }));

    fireEvent.keyDown(document, { key: "E" });
    await waitFor(() => screen.getAllByRole("textbox"));

    const cancelBtn = screen.getByRole("button", { name: /cancel/i });
    await userEvent.click(cancelBtn);

    await waitFor(() =>
      expect(screen.queryAllByRole("textbox").length).toBe(0)
    );
  });

  it("Save and Send calls /api/update-draft then enters undo flow", async () => {
    vi.useFakeTimers();
    render(<QueuePage />);
    await act(async () => {
      await Promise.resolve();
    });

    act(() => {
      fireEvent.keyDown(document, { key: "E" });
    });
    await act(async () => {
      await Promise.resolve();
    });

    const saveBtn = screen.getByRole("button", { name: /save and send/i });
    await act(async () => {
      fireEvent.click(saveBtn);
      await Promise.resolve();
    });

    expect(global.fetch).toHaveBeenCalledWith(
      "/api/update-draft",
      expect.objectContaining({ method: "POST" })
    );
    // Should also enter the undo flow (toast shown)
    expect(toastMock).toHaveBeenCalled();
  });
});

describe("QueuePage — Skip and Mark Dead", () => {
  it("x key skips the row (removes from visible list)", async () => {
    render(<QueuePage />);
    await waitFor(() => screen.getByRole("heading", { name: "Bob Martinez" }));

    // Focus Bob, press x — adds to skippedIds
    fireEvent.keyDown(document, { key: "x" });

    // After skip, Dave should take focus (or empty state)
    await waitFor(() => {
      // Bob's heading should no longer be visible (focus moved to Dave or empty)
      const bobs = screen.queryAllByRole("heading", { name: "Bob Martinez" });
      expect(bobs.length).toBe(0);
    });
  });

  it("D key triggers mark dead undo flow", async () => {
    vi.useFakeTimers();
    render(<QueuePage />);
    await act(async () => {
      await Promise.resolve();
    });

    await act(async () => {
      fireEvent.keyDown(document, { key: "D" });
    });

    expect(toastMock).toHaveBeenCalledWith(
      expect.stringContaining("dead"),
      expect.objectContaining({ duration: 5000 })
    );
    // Supabase update should NOT fire immediately
    expect(updateMock).not.toHaveBeenCalled();
  });

  it("fetchData excludes reply_status=dead contacts via neq filter", async () => {
    render(<QueuePage />);
    await waitFor(() => screen.getByRole("heading", { name: "Bob Martinez" }));

    expect(neqMock).toHaveBeenCalledWith("reply_status", "dead");
  });
});

describe("QueuePage — Tier filters", () => {
  it("1/2/3 toggle tier filters", async () => {
    render(<QueuePage />);
    await waitFor(() => screen.getByRole("heading", { name: "Bob Martinez" }));

    // Press 2 to filter T2 only (Dave is T2)
    fireEvent.keyDown(document, { key: "2" });

    await waitFor(() => {
      // Dave (T2) should be the focused heading
      expect(screen.getByRole("heading", { name: "Dave Johnson" })).toBeInTheDocument();
      // Bob (T1) heading should not be shown
      expect(screen.queryByRole("heading", { name: "Bob Martinez" })).toBeNull();
    });

    // Press 2 again to remove T2 filter
    fireEvent.keyDown(document, { key: "2" });
    await waitFor(() =>
      screen.getByRole("heading", { name: /bob martinez|dave johnson/i })
    );
  });
});

describe("QueuePage — Esc behavior", () => {
  it("Esc closes Quick Fix", async () => {
    render(<QueuePage />);
    await waitFor(() => screen.getByRole("heading", { name: "Bob Martinez" }));

    fireEvent.keyDown(document, { key: "E" });
    await waitFor(() => screen.getAllByRole("textbox"));

    fireEvent.keyDown(document, { key: "Escape" });
    await waitFor(() =>
      expect(screen.queryAllByRole("textbox").length).toBe(0)
    );
  });
});

describe("QueuePage — Approve and Send disabled when no gmail_draft_id", () => {
  it("button is disabled and e key does not trigger send when gmail_draft_id is null", async () => {
    vi.useFakeTimers();
    const draftNoId = makeDraft({ gmail_draft_id: null });
    mockSupabaseData([bob], [draftNoId]);

    render(<QueuePage />);
    // Flush initial data load
    await act(async () => {
      await Promise.resolve();
    });

    // Button should be disabled
    const btn = screen.getByRole("button", { name: /approve and send/i });
    expect(btn).toBeDisabled();

    // e key should NOT trigger toast / send
    await act(async () => {
      fireEvent.keyDown(document, { key: "e" });
    });
    expect(toastMock).not.toHaveBeenCalled();

    await act(async () => {
      vi.advanceTimersByTime(6000);
      await Promise.resolve();
    });
    expect(global.fetch).not.toHaveBeenCalled();
  });

  it("button is enabled when gmail_draft_id is present", async () => {
    render(<QueuePage />);
    await waitFor(() => screen.getByRole("heading", { name: "Bob Martinez" }));

    const btn = screen.getByRole("button", { name: /approve and send/i });
    expect(btn).not.toBeDisabled();
  });
});

describe("QueuePage — unmount", () => {
  it("clears pending timers on unmount", async () => {
    vi.useFakeTimers();
    const { unmount } = render(<QueuePage />);
    await act(async () => {
      await Promise.resolve();
    });

    // Start a send
    await act(async () => {
      fireEvent.keyDown(document, { key: "e" });
    });

    // Unmount before timer fires
    unmount();

    // Advance past 5s — no error, no API call (timer was cleared)
    await act(async () => {
      vi.advanceTimersByTime(6000);
      await Promise.resolve();
    });

    expect(global.fetch).not.toHaveBeenCalled();
  });
});

// ── Timezone display ───────────────────────────────────────────────────────────

describe("QueuePage — timezone display", () => {
  it("header always shows 'Your time:' with sender time", async () => {
    render(<QueuePage />);
    await waitFor(() => {
      expect(screen.getAllByText("Bob Martinez").length).toBeGreaterThan(0);
    });
    expect(screen.getByText(/your time:/i)).toBeInTheDocument();
  });

  it("queue row with state shows state code and local time", async () => {
    const bobWithState = makeContact({ state: "NY" });
    mockSupabaseData([bobWithState, dave], [bobDraft, daveDraft]);
    render(<QueuePage />);
    await waitFor(() => {
      expect(screen.getAllByText("Bob Martinez").length).toBeGreaterThan(0);
    });

    // NY should appear in the location label inside a list item
    const locationEl = screen.getByText(/^NY · /);
    expect(locationEl).toBeInTheDocument();
    expect(locationEl.textContent).toMatch(/NY · \d{1,2}:\d{2} [AP]M/);
  });

  it("queue row without state shows no location element", async () => {
    // bob and dave both have no state in default fixtures
    render(<QueuePage />);
    await waitFor(() => {
      expect(screen.getAllByText("Bob Martinez").length).toBeGreaterThan(0);
    });

    // Dave Johnson has no state — his list row should have no AM/PM marker
    const daveListRows = screen.getAllByText("Dave Johnson");
    const daveRow = daveListRows[0].closest("li");
    expect(daveRow?.textContent).not.toMatch(/[AP]M/);
  });

  it("header distribution includes ET label when contacts have NY state", async () => {
    const bobWithState = makeContact({ state: "NY" });
    mockSupabaseData([bobWithState, dave], [bobDraft, daveDraft]);
    render(<QueuePage />);
    await waitFor(() => {
      expect(screen.getAllByText("Bob Martinez").length).toBeGreaterThan(0);
    });

    // "1 ET" should appear in header distribution since NY maps to ET
    const header = document.querySelector("aside");
    expect(header?.textContent).toMatch(/1 ET/);
  });

  it("header shows no distribution when all contacts have null state", async () => {
    // Default contacts (bob, dave) have no state
    render(<QueuePage />);
    await waitFor(() => {
      expect(screen.getAllByText("Bob Martinez").length).toBeGreaterThan(0);
    });

    const header = document.querySelector("aside");
    // Only "Your time: X:XX XX <label>" with no distribution numbers
    expect(header?.textContent).not.toMatch(/\d+ ET/);
  });
});
