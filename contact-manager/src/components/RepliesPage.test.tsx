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

// ── ThreadView mock ───────────────────────────────────────────────────────────

vi.mock("./ThreadView", () => ({
  ThreadView: ({ contactId }: { contactId: string | number }) => (
    <div data-testid={`thread-${contactId}`}>Thread for {contactId}</div>
  ),
}));

// ── Supabase mock ─────────────────────────────────────────────────────────────

const {
  selectContactsMock,
  selectDraftsMock,
  selectMsgsMock,
  updateMock,
} = vi.hoisted(() => ({
  selectContactsMock: vi.fn(),
  selectDraftsMock: vi.fn(),
  selectMsgsMock: vi.fn(),
  updateMock: vi.fn(),
}));

vi.mock("@/lib/supabase", () => {
  function makeReadChain(terminalMock: ReturnType<typeof vi.fn>) {
    const chain: Record<string, unknown> = {};
    for (const m of [
      "select",
      "in",
      "is",
      "order",
      "eq",
      "limit",
      "not",
      "lt",
    ]) {
      chain[m] = vi.fn(() => chain);
    }
    chain.then = terminalMock;
    return chain;
  }

  return {
    supabase: {
      from: vi.fn((table: string) => {
        if (table === "contacts") {
          const chain = makeReadChain(selectContactsMock);
          return {
            select: vi.fn(() => chain),
            update: vi.fn(() => ({ eq: updateMock })),
          };
        }
        if (table === "draft_history") {
          return { select: vi.fn(() => makeReadChain(selectDraftsMock)) };
        }
        if (table === "email_messages") {
          return { select: vi.fn(() => makeReadChain(selectMsgsMock)) };
        }
        return {
          select: vi.fn(() => makeReadChain(selectContactsMock)),
          update: vi.fn(() => ({ eq: updateMock })),
        };
      }),
    },
  };
});

// ── Component import ──────────────────────────────────────────────────────────

import { RepliesPage } from "./RepliesPage";
import type { Contact, DraftHistory, EmailMessage } from "@/lib/types";

// ── Fixtures ──────────────────────────────────────────────────────────────────

function makeContact(overrides: Partial<Contact> = {}): Contact {
  return {
    id: "7",
    name: "Grace Lee",
    email: "grace@groveworks.com",
    company: "Groveworks",
    role: "VP Engineering",
    detail: null,
    tier: 1,
    mode: "outreach",
    stage: "followup2_sent",
    reply_status: "no_reply",
    classifier_status: "positive_reply",
    dartmouth: false,
    job_title: null,
    job_description: null,
    company_applied: null,
    applied_date: null,
    followup_date: null,
    notes: null,
    resume_url: null,
    created_at: "2026-05-12T14:00:00Z",
    message_id: "msg-thread-7",
    last_emailed: null,
    deleted_at: null,
    ...overrides,
  };
}

function makeDraft(overrides: Partial<DraftHistory> = {}): DraftHistory {
  return {
    id: 7,
    contact_id: 7,
    stage: "reply_drafted",
    subject: "Re: Quick intro",
    body: "Hi Grace,\n\nThanks for getting back to me! Would a 15-minute call work?\n\nKishore",
    message_id: "msg-reply-7",
    gmail_draft_id: "draft-reply-id-7",
    drafted_at: "2026-05-20T15:00:00.000Z",
    sent_subject: null,
    sent_body: null,
    sent_at: null,
    edit_detected: null,
    ...overrides,
  };
}

function makeIncomingMsg(overrides: Partial<EmailMessage> = {}): EmailMessage {
  return {
    id: 2,
    contact_id: 7,
    direction: "incoming",
    subject: "Re: Quick intro",
    body: "Hi Kishore, happy to chat.",
    sent_at: "2026-05-12T14:00:00.000Z",
    message_id: "msg-reply-incoming-7",
    in_reply_to: "msg-thread-7",
    stage_at_send: null,
    raw_headers: null,
    ...overrides,
  };
}

const grace = makeContact();
const graceDraft = makeDraft();
const graceMsg = makeIncomingMsg();

const iris = makeContact({
  id: "9",
  name: "Iris Moore",
  company: "Iris Tech",
  classifier_status: "soft_yes",
  created_at: "2026-05-10T00:00:00Z",
});

const quinn = makeContact({
  id: "17",
  name: "Quinn Thompson",
  company: "Quantify",
  classifier_status: "hard_no",
  created_at: "2026-05-08T00:00:00Z",
});

// ── Mock helper ───────────────────────────────────────────────────────────────

function mockSupabaseData(
  contacts: Contact[],
  drafts: DraftHistory[] = [],
  msgs: EmailMessage[] = []
) {
  selectContactsMock.mockImplementation(
    (resolve: (v: unknown) => void) => resolve({ data: contacts })
  );
  selectDraftsMock.mockImplementation(
    (resolve: (v: unknown) => void) => resolve({ data: drafts })
  );
  selectMsgsMock.mockImplementation(
    (resolve: (v: unknown) => void) => resolve({ data: msgs })
  );
}

// ── Tests ─────────────────────────────────────────────────────────────────────

beforeEach(() => {
  vi.clearAllMocks();
  mockSupabaseData([grace, iris, quinn], [graceDraft], [graceMsg]);
  vi.spyOn(global, "fetch").mockResolvedValue(
    new Response(JSON.stringify({ ok: true, stage: "reply_sent" }), {
      status: 200,
    })
  );
});

afterEach(() => {
  vi.useRealTimers();
});

describe("RepliesPage — rendering", () => {
  it("renders contacts sorted: positive first, soft_yes second", async () => {
    render(<RepliesPage />);
    await waitFor(() => {
      expect(screen.getAllByText("Grace Lee").length).toBeGreaterThan(0);
    });
    expect(screen.getAllByText("Iris Moore").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Quinn Thompson").length).toBeGreaterThan(0);

    // Grace (positive) should be focused first
    expect(
      screen.getByRole("heading", { name: "Grace Lee" })
    ).toBeInTheDocument();
  });

  it("shows empty state when no contacts", async () => {
    mockSupabaseData([], [], []);
    render(<RepliesPage />);
    await waitFor(() => {
      const els = screen.getAllByText(/no replies to triage/i);
      expect(els.length).toBeGreaterThan(0);
    });
  });

  it("shows suggested reply block for positive contact with draft", async () => {
    render(<RepliesPage />);
    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "Grace Lee" })).toBeInTheDocument();
    });
    expect(screen.getByText(/suggested reply/i)).toBeInTheDocument();
    expect(screen.getByText(/re: quick intro/i)).toBeInTheDocument();
  });

  it("shows no-draft explanation for hard_no contact", async () => {
    render(<RepliesPage />);
    await waitFor(() => screen.getByRole("heading", { name: "Grace Lee" }));

    // Click Quinn (hard_no)
    const quinnItem = screen.getAllByText("Quinn Thompson")[0].closest("li");
    if (quinnItem) fireEvent.click(quinnItem);

    await waitFor(() =>
      screen.getByRole("heading", { name: "Quinn Thompson" })
    );
    expect(screen.getByText(/no suggested reply drafted/i)).toBeInTheDocument();
    expect(
      screen.getByText(/agent only drafts replies for positive/i)
    ).toBeInTheDocument();
  });

  it("shows thread for focused contact", async () => {
    render(<RepliesPage />);
    await waitFor(() => {
      expect(screen.getByTestId("thread-7")).toBeInTheDocument();
    });
  });

  it("shows NEEDS RESPONSE count badge", async () => {
    render(<RepliesPage />);
    await waitFor(() =>
      screen.getByRole("heading", { name: "Grace Lee" })
    );
    // 3 contacts loaded
    const badge = screen.getAllByText("3").find((el) =>
      el.className?.includes("indigo")
    );
    expect(badge).toBeTruthy();
  });
});

describe("RepliesPage — keyboard navigation", () => {
  it("j moves focus to next row", async () => {
    render(<RepliesPage />);
    await waitFor(() => screen.getByRole("heading", { name: "Grace Lee" }));

    fireEvent.keyDown(document, { key: "j" });
    await waitFor(() =>
      expect(
        screen.getByRole("heading", { name: "Iris Moore" })
      ).toBeInTheDocument()
    );
  });

  it("k moves focus to previous row", async () => {
    render(<RepliesPage />);
    await waitFor(() => screen.getByRole("heading", { name: "Grace Lee" }));

    fireEvent.keyDown(document, { key: "j" });
    await waitFor(() => screen.getByRole("heading", { name: "Iris Moore" }));

    fireEvent.keyDown(document, { key: "k" });
    await waitFor(() =>
      expect(
        screen.getByRole("heading", { name: "Grace Lee" })
      ).toBeInTheDocument()
    );
  });

  it("early-returns when textarea is focused", async () => {
    render(<RepliesPage />);
    await waitFor(() => screen.getByRole("heading", { name: "Grace Lee" }));

    // Open Quick Fix to get a textarea
    fireEvent.keyDown(document, { key: "E" });
    await waitFor(() => screen.getAllByRole("textbox").length >= 2);

    const textarea = screen.getAllByRole("textbox")[1];
    textarea.focus();

    fireEvent.keyDown(document, { key: "j" });
    // Should NOT navigate — still on Grace's Quick Fix
    expect(screen.queryByRole("heading", { name: "Iris Moore" })).toBeNull();
  });
});

describe("RepliesPage — 5-second undo (Approve and Send)", () => {
  it("e key shows toast but does NOT call API immediately", async () => {
    vi.useFakeTimers();
    render(<RepliesPage />);
    await act(async () => {
      await Promise.resolve();
    });

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
      (
        _msg: string,
        opts?: { action?: { onClick: (e: MouseEvent) => void } }
      ) => {
        capturedAction = opts?.action?.onClick;
        return "toast-id-1";
      }
    );

    render(<RepliesPage />);
    await act(async () => {
      await Promise.resolve();
    });

    await act(async () => {
      fireEvent.keyDown(document, { key: "e" });
    });

    await act(async () => {
      capturedAction?.(new MouseEvent("click") as unknown as MouseEvent);
    });

    await act(async () => {
      vi.advanceTimersByTime(6000);
    });

    expect(global.fetch).not.toHaveBeenCalled();
    expect(toastMock.dismiss).toHaveBeenCalled();
    expect(toastMock.info).toHaveBeenCalledWith("Canceled");
  });

  it("timer expiry fires POST /api/send-draft", async () => {
    vi.useFakeTimers();
    render(<RepliesPage />);
    await act(async () => {
      await Promise.resolve();
    });

    await act(async () => {
      fireEvent.keyDown(document, { key: "e" });
    });

    await act(async () => {
      vi.advanceTimersByTime(5001);
      await Promise.resolve();
    });

    expect(global.fetch).toHaveBeenCalledWith(
      "/api/send-draft",
      expect.objectContaining({ method: "POST" })
    );
  });

  it("e key does nothing for hard_no contact without draft", async () => {
    render(<RepliesPage />);
    await waitFor(() => screen.getByRole("heading", { name: "Grace Lee" }));

    const quinnItem = screen.getAllByText("Quinn Thompson")[0].closest("li");
    if (quinnItem) fireEvent.click(quinnItem);
    await waitFor(() => screen.getByRole("heading", { name: "Quinn Thompson" }));

    fireEvent.keyDown(document, { key: "e" });
    expect(toastMock).not.toHaveBeenCalled();
  });
});

describe("RepliesPage — Quick Fix", () => {
  it("E key opens quick fix with textareas", async () => {
    render(<RepliesPage />);
    await waitFor(() => screen.getByRole("heading", { name: "Grace Lee" }));

    fireEvent.keyDown(document, { key: "E" });
    await waitFor(() => {
      const textareas = screen.getAllByRole("textbox");
      expect(textareas.length).toBeGreaterThanOrEqual(2);
    });
  });

  it("Cancel restores read-only view", async () => {
    render(<RepliesPage />);
    await waitFor(() => screen.getByRole("heading", { name: "Grace Lee" }));

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
    render(<RepliesPage />);
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
    expect(toastMock).toHaveBeenCalled();
  });

  it("E key does nothing for hard_no contact without draft", async () => {
    render(<RepliesPage />);
    await waitFor(() => screen.getByRole("heading", { name: "Grace Lee" }));

    const quinnItem = screen.getAllByText("Quinn Thompson")[0].closest("li");
    if (quinnItem) fireEvent.click(quinnItem);
    await waitFor(() => screen.getByRole("heading", { name: "Quinn Thompson" }));

    fireEvent.keyDown(document, { key: "E" });
    // Quick Fix should not open (no draft for Quinn)
    expect(screen.queryAllByRole("textbox").length).toBe(0);
  });
});

describe("RepliesPage — Mark reply_status", () => {
  it("i key shows undo toast but does not call Supabase immediately", async () => {
    vi.useFakeTimers();
    render(<RepliesPage />);
    await act(async () => {
      await Promise.resolve();
    });

    await act(async () => {
      fireEvent.keyDown(document, { key: "i" });
    });

    expect(toastMock).toHaveBeenCalledWith(
      expect.stringContaining("interested"),
      expect.objectContaining({ duration: 5000 })
    );
    expect(updateMock).not.toHaveBeenCalled();
  });

  it("c key shows undo toast for call_scheduled", async () => {
    vi.useFakeTimers();
    render(<RepliesPage />);
    await act(async () => {
      await Promise.resolve();
    });

    await act(async () => {
      fireEvent.keyDown(document, { key: "c" });
    });

    expect(toastMock).toHaveBeenCalledWith(
      expect.stringContaining("call scheduled"),
      expect.objectContaining({ duration: 5000 })
    );
    expect(updateMock).not.toHaveBeenCalled();
  });

  it("D key shows undo toast for dead", async () => {
    vi.useFakeTimers();
    render(<RepliesPage />);
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
    expect(updateMock).not.toHaveBeenCalled();
  });

  it("i key: timer expiry calls Supabase update", async () => {
    vi.useFakeTimers();
    updateMock.mockResolvedValue({ data: null, error: null });
    render(<RepliesPage />);
    await act(async () => {
      await Promise.resolve();
    });

    await act(async () => {
      fireEvent.keyDown(document, { key: "i" });
    });

    await act(async () => {
      vi.advanceTimersByTime(5001);
      await Promise.resolve();
    });

    expect(updateMock).toHaveBeenCalledWith("id", "7");
  });
});

describe("RepliesPage — Esc", () => {
  it("Esc closes Quick Fix", async () => {
    render(<RepliesPage />);
    await waitFor(() => screen.getByRole("heading", { name: "Grace Lee" }));

    fireEvent.keyDown(document, { key: "E" });
    await waitFor(() => screen.getAllByRole("textbox"));

    fireEvent.keyDown(document, { key: "Escape" });
    await waitFor(() =>
      expect(screen.queryAllByRole("textbox").length).toBe(0)
    );
  });
});

describe("RepliesPage — unmount", () => {
  it("clears pending timers on unmount", async () => {
    vi.useFakeTimers();
    const { unmount } = render(<RepliesPage />);
    await act(async () => {
      await Promise.resolve();
    });

    await act(async () => {
      fireEvent.keyDown(document, { key: "e" });
    });

    unmount();

    await act(async () => {
      vi.advanceTimersByTime(6000);
      await Promise.resolve();
    });

    expect(global.fetch).not.toHaveBeenCalled();
  });
});
