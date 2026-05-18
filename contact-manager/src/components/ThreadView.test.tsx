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
});
