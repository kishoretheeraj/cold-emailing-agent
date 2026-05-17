import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, act } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { AgentEvent } from "@/lib/types";

// ── Mocks ──────────────────────────────────────────────────────────────────────

const { limitMock } = vi.hoisted(() => ({ limitMock: vi.fn() }));

vi.mock("@/lib/supabase", () => {
  const listChain: Record<string, unknown> = {};
  listChain.order = vi.fn(() => listChain);
  listChain.limit = limitMock;

  // count query resolves via .then (countChain is a thenable)
  const countChain: Record<string, unknown> = {};
  countChain.in = vi.fn(() => countChain);
  countChain.gte = vi.fn(() => countChain);
  // Will be overridden per-test
  countChain.then = vi.fn((resolve: (v: { count: number }) => void) =>
    resolve({ count: 0 }),
  );

  return {
    supabase: {
      from: vi.fn(() => ({
        select: vi.fn((_col: string, opts?: { count?: string }) =>
          opts?.count ? countChain : listChain,
        ),
      })),
    },
  };
});

vi.mock("next/link", () => ({
  default: ({
    href,
    children,
    className,
  }: {
    href: string;
    children: React.ReactNode;
    className?: string;
  }) => (
    <a href={href} className={className}>
      {children}
    </a>
  ),
}));

import RunsPage from "./page";

function makeEvent(overrides: Partial<AgentEvent> = {}): AgentEvent {
  return {
    id: 1,
    run_id: null,
    event_type: "preflight",
    contact_id: 42,
    status: "success",
    error_message: null,
    blocked_checks: null,
    tokens_used: null,
    started_at: "2026-05-16T10:00:00Z",
    completed_at: "2026-05-16T10:00:01Z",
    ...overrides,
  };
}

beforeEach(() => {
  vi.resetAllMocks();
  // Default: list returns empty, badge count is 0
  limitMock.mockResolvedValue({ data: [] });
});

describe("RunsPage", () => {
  it("renders events after loading", async () => {
    limitMock.mockResolvedValue({ data: [makeEvent()] });

    render(<RunsPage />);

    await waitFor(() => {
      expect(screen.getByText("preflight")).toBeTruthy();
    });
  });

  it("shows empty state when no events", async () => {
    render(<RunsPage />);

    await waitFor(() => {
      expect(screen.getByText(/no events yet/i)).toBeTruthy();
    });
  });

  it("status chip filters events by status", async () => {
    limitMock.mockResolvedValue({
      data: [
        makeEvent({ id: 1, status: "success", event_type: "preflight" }),
        makeEvent({ id: 2, status: "failed", event_type: "classify_reply" }),
      ],
    });

    const user = userEvent.setup();
    render(<RunsPage />);

    await waitFor(() => screen.getByText("preflight"));

    await user.click(screen.getByRole("button", { name: /^failed$/i }));

    await waitFor(() => {
      expect(screen.queryByText("preflight")).toBeNull();
      expect(screen.getByText("classify_reply")).toBeTruthy();
    });
  });

  it("auto-refreshes: sets up a 10-second interval", () => {
    vi.useFakeTimers();
    limitMock.mockResolvedValue({ data: [] });

    const { unmount } = render(<RunsPage />);
    const callsAfterMount = limitMock.mock.calls.length;

    act(() => {
      vi.advanceTimersByTime(10_000);
    });

    expect(limitMock.mock.calls.length).toBeGreaterThan(callsAfterMount);
    unmount();
    vi.useRealTimers();
  });
});
