import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { EmailMessage } from "@/lib/types";

const { selectThenMock } = vi.hoisted(() => ({
  selectThenMock: vi.fn(),
}));

vi.mock("@/lib/supabase", () => {
  const chain: Record<string, unknown> = {};
  for (const m of ["eq", "order"]) {
    chain[m] = vi.fn(() => chain);
  }
  chain.then = selectThenMock;
  return {
    supabase: {
      from: vi.fn(() => ({ select: vi.fn(() => chain) })),
    },
  };
});

import { ThreadView } from "./ThreadView";

function makeMsg(overrides: Partial<EmailMessage> = {}): EmailMessage {
  return {
    id: 1,
    contact_id: 42,
    direction: "outgoing",
    subject: "quick intro",
    body: "Hi Alice, hope you're well.",
    sent_at: "2026-05-10T10:00:00Z",
    message_id: "<msg1@gmail.com>",
    in_reply_to: null,
    stage_at_send: "first_touch_drafted",
    raw_headers: null,
    ...overrides,
  };
}

beforeEach(() => {
  vi.resetAllMocks();
});

describe("ThreadView", () => {
  it("shows loading state initially then renders messages", async () => {
    selectThenMock.mockImplementation((cb: (r: { data: EmailMessage[] }) => void) => {
      setTimeout(() => cb({ data: [makeMsg()] }), 0);
      return Promise.resolve();
    });

    render(<ThreadView contactId={42} />);
    expect(screen.getByText(/loading thread/i)).toBeTruthy();

    await waitFor(() => {
      expect(screen.queryByText(/loading thread/i)).toBeNull();
    });
  });

  it("shows empty state when no messages", async () => {
    selectThenMock.mockImplementation((cb: (r: { data: EmailMessage[] }) => void) => {
      cb({ data: [] });
      return Promise.resolve();
    });

    render(<ThreadView contactId={42} />);

    await waitFor(() => {
      expect(screen.getByText(/no emails recorded yet/i)).toBeTruthy();
    });
  });

  it("renders outgoing and incoming messages distinctly", async () => {
    selectThenMock.mockImplementation((cb: (r: { data: EmailMessage[] }) => void) => {
      cb({
        data: [
          makeMsg({ direction: "outgoing" }),
          makeMsg({ id: 2, direction: "incoming", body: "Sounds great!" }),
        ],
      });
      return Promise.resolve();
    });

    render(<ThreadView contactId={42} />);

    await waitFor(() => {
      expect(screen.getByText("You")).toBeTruthy();
      expect(screen.getByText("Them")).toBeTruthy();
    });
  });

  it("shows human-readable stage label for known stage_at_send values", async () => {
    selectThenMock.mockImplementation((cb: (r: { data: EmailMessage[] }) => void) => {
      cb({
        data: [
          makeMsg({ stage_at_send: "new" }),
          makeMsg({ id: 2, stage_at_send: "first_touch_sent" }),
          makeMsg({ id: 3, stage_at_send: "followup1_sent" }),
          makeMsg({ id: 4, stage_at_send: "followup2_sent" }),
        ],
      });
      return Promise.resolve();
    });

    render(<ThreadView contactId={42} />);

    await waitFor(() => {
      expect(screen.getByText("First Touch")).toBeTruthy();
      expect(screen.getByText("Follow-up 1")).toBeTruthy();
      expect(screen.getByText("Follow-up 2")).toBeTruthy();
      expect(screen.getByText("Breakup")).toBeTruthy();
    });
  });

  it("omits stage label for incoming messages and unknown stage values", async () => {
    selectThenMock.mockImplementation((cb: (r: { data: EmailMessage[] }) => void) => {
      cb({
        data: [
          makeMsg({ direction: "incoming", stage_at_send: "new" }),
          makeMsg({ id: 2, direction: "outgoing", stage_at_send: "some_unknown_stage" }),
        ],
      });
      return Promise.resolve();
    });

    render(<ThreadView contactId={42} />);

    await waitFor(() => screen.getByText("You"));
    // "First Touch" label must not appear: incoming direction suppresses it, and
    // the unknown stage has no mapping.
    expect(screen.queryByText("First Touch")).toBeNull();
  });

  it("truncates long bodies and shows expand toggle", async () => {
    const longBody = "x".repeat(400);
    selectThenMock.mockImplementation((cb: (r: { data: EmailMessage[] }) => void) => {
      cb({ data: [makeMsg({ body: longBody })] });
      return Promise.resolve();
    });

    const user = userEvent.setup();
    render(<ThreadView contactId={42} />);

    await waitFor(() => {
      expect(screen.getByText(/show more/i)).toBeTruthy();
    });

    await user.click(screen.getByText(/show more/i));
    expect(screen.getByText(/show less/i)).toBeTruthy();
  });

  // ── Body sanitization ───────────────────────────────────────────────────────

  it("shows garbled-body fallback for raw MIME structure (Samsung-style)", async () => {
    const mimeBody =
      "----_com.samsung.android.email_9552101976775920\r\nContent-Type: text/plain; charset=utf-8\r\nContent-Transfer-Encoding: base64\r\n\r\nSGkgS2lzaG9yZQ==";
    selectThenMock.mockImplementation((cb: (r: { data: EmailMessage[] }) => void) => {
      cb({ data: [makeMsg({ direction: "incoming", body: mimeBody })] });
      return Promise.resolve();
    });

    render(<ThreadView contactId={42} />);

    await waitFor(() =>
      expect(screen.getByText(/message encoding not supported/i)).toBeTruthy()
    );
    // Raw MIME content must NOT appear
    expect(screen.queryByText(/Content-Type/)).toBeNull();
    expect(screen.queryByText(/SGkgS2lz/)).toBeNull();
    // "Open in Gmail" link shown
    expect(screen.getByRole("button", { name: /open in gmail/i })).toBeTruthy();
  });

  it("strips HTML tags and decodes quoted-printable for raw HTML body", async () => {
    const htmlBody =
      "<!DOCTYPE html><html><body>Hi Kishore,=3D nice to meet you.</body></html>";
    selectThenMock.mockImplementation((cb: (r: { data: EmailMessage[] }) => void) => {
      cb({ data: [makeMsg({ direction: "incoming", body: htmlBody })] });
      return Promise.resolve();
    });

    render(<ThreadView contactId={42} />);

    await waitFor(() => {
      // Tags stripped, QP decoded: =3D → =
      expect(screen.getByText(/nice to meet you/)).toBeTruthy();
    });
    // Raw markup must not appear verbatim
    expect(screen.queryByText(/<!DOCTYPE/)).toBeNull();
  });

  it("renders normal body as-is without sanitization", async () => {
    selectThenMock.mockImplementation((cb: (r: { data: EmailMessage[] }) => void) => {
      cb({ data: [makeMsg({ direction: "incoming", body: "Happy to connect, Kishore!" })] });
      return Promise.resolve();
    });

    render(<ThreadView contactId={42} />);

    await waitFor(() =>
      expect(screen.getByText("Happy to connect, Kishore!")).toBeTruthy()
    );
    expect(screen.queryByText(/message encoding not supported/i)).toBeNull();
  });

  // ── Quoted-content stripping ───────────────────────────────────────────────

  describe("stripQuotedContent via sanitizeBody", () => {
    async function renderBody(body: string) {
      selectThenMock.mockImplementation((cb: (r: { data: EmailMessage[] }) => void) => {
        cb({ data: [makeMsg({ direction: "incoming", body })] });
        return Promise.resolve();
      });
      render(<ThreadView contactId={42} />);
      await waitFor(() => screen.queryByText(/loading thread/i) === null);
    }

    it("strips === mobile separator and everything after it (Marcel scenario)", async () => {
      const body =
        "Good reply here. ====================Kort, want mobiel verstuurd!\n" +
        "--------- Oorspronkelijk bericht -------- Van: Kishore <k@example.com>";
      await renderBody(body);
      expect(screen.getByText(/Good reply here\./)).toBeTruthy();
      expect(screen.queryByText(/Kort, want mobiel/)).toBeNull();
      expect(screen.queryByText(/Oorspronkelijk bericht/)).toBeNull();
      expect(screen.queryByText(/Van: Kishore/)).toBeNull();
    });

    it("strips Dutch Oorspronkelijk bericht separator", async () => {
      const body =
        "Thanks for reaching out!\n" +
        "--------- Oorspronkelijk bericht -------- Van: Kishore\nHi Marcel,\nOriginal email...";
      await renderBody(body);
      expect(screen.getByText(/Thanks for reaching out!/)).toBeTruthy();
      expect(screen.queryByText(/Oorspronkelijk bericht/)).toBeNull();
      expect(screen.queryByText(/Original email/)).toBeNull();
    });

    it("strips English Original Message separator", async () => {
      const body =
        "Got it, thanks.\n" +
        "----- Original Message ----- From: Kishore\nHi there,\nOriginal content.";
      await renderBody(body);
      expect(screen.getByText(/Got it, thanks\./)).toBeTruthy();
      expect(screen.queryByText(/Original Message/)).toBeNull();
    });

    it("strips Gmail On-date-wrote quoting", async () => {
      const body =
        "Sure, let's connect.\nOn 20 May 2026 Kishore wrote:\n> Hi Alice, hope you're well.";
      await renderBody(body);
      expect(screen.getByText(/Sure, let's connect\./)).toBeTruthy();
      expect(screen.queryByText(/On 20 May 2026/)).toBeNull();
      expect(screen.queryByText(/hope you're well/)).toBeNull();
    });

    it("strips Outlook-style From/Sent/To/Subject header block", async () => {
      const body =
        "OK sounds good.\nFrom: Kishore Theeraj <k@dartmouth.edu>\nSent: Monday, 20 May 2026\nTo: alice@example.com\nSubject: Quick intro";
      await renderBody(body);
      expect(screen.getByText(/OK sounds good\./)).toBeTruthy();
      expect(screen.queryByText(/Kishore Theeraj/)).toBeNull();
    });

    it("leaves clean bodies unchanged", async () => {
      const body = "Hi Kishore, happy to chat. Let me know your availability.";
      await renderBody(body);
      expect(screen.getByText(body)).toBeTruthy();
    });
  });
});
