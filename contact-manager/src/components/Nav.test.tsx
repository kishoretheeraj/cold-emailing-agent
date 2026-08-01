import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
  Toaster: () => null,
}));

vi.mock("next/navigation", () => ({
  usePathname: vi.fn(() => "/"),
}));

// Stub Tooltip as a passthrough — TooltipProvider isn't wired in unit test renders.
vi.mock("@/components/ui/Tooltip", () => ({
  Tooltip: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));

const { visaReviewCountMock } = vi.hoisted(() => ({
  visaReviewCountMock: vi.fn(() => Promise.resolve({ data: [] })),
}));
vi.mock("@/lib/supabase", () => ({
  supabase: {
    from: vi.fn(() => ({
      select: vi.fn(() => ({
        eq: vi.fn(() => visaReviewCountMock()),
      })),
    })),
  },
}));

import { usePathname } from "next/navigation";
import { Nav } from "./Nav";

// Helper: resolve GET /api/agent-config with a given scope.
function mockAgentConfig(scope: "none" | "agent" | "all") {
  global.fetch = vi.fn().mockImplementation((url: string) => {
    if (url === "/api/agent-config" && typeof url === "string") {
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({ scope }),
      });
    }
    return Promise.resolve({ ok: true, json: () => Promise.resolve({}) });
  });
}

beforeEach(() => {
  vi.mocked(usePathname).mockReturnValue("/");
  mockAgentConfig("none");
  visaReviewCountMock.mockReset();
  visaReviewCountMock.mockResolvedValue({ data: [] });
});

describe("Nav — static structure", () => {
  it("renders the app title linking to home", async () => {
    render(<Nav />);
    await waitFor(() => screen.getByRole("link", { name: /cold email ops/i }));
    expect(screen.getByRole("link", { name: /cold email ops/i })).toHaveAttribute("href", "/");
  });

  it("renders all nav links", async () => {
    render(<Nav />);
    await waitFor(() => screen.getByRole("link", { name: /^overview$/i }));
    expect(screen.getByRole("link", { name: /^queue$/i })).toHaveAttribute("href", "/queue");
    expect(screen.getByRole("link", { name: /^replies$/i })).toHaveAttribute("href", "/replies");
    expect(screen.getByRole("link", { name: /^prompts$/i })).toHaveAttribute("href", "/prompts");
    expect(screen.getByRole("link", { name: /activity/i })).toHaveAttribute("href", "/runs");
    expect(screen.getByRole("link", { name: /^lab$/i })).toHaveAttribute("href", "/lab");
    expect(screen.getByRole("link", { name: /^visa$/i })).toHaveAttribute("href", "/visa-review");
  });

  it("highlights the active route link", async () => {
    vi.mocked(usePathname).mockReturnValue("/queue");
    render(<Nav />);
    await waitFor(() => screen.getByRole("link", { name: /^queue$/i }));
    expect(screen.getByRole("link", { name: /^queue$/i }).className).toMatch(/indigo/);
    expect(screen.getByRole("link", { name: /^overview$/i }).className).not.toMatch(/indigo/);
  });

  it("shows no pending-count badge on the Visa link when the queue is empty", async () => {
    visaReviewCountMock.mockResolvedValue({ data: [] });
    render(<Nav />);
    await waitFor(() => screen.getByRole("link", { name: /^visa$/i }));
    expect(screen.getByRole("link", { name: /^visa$/i }).textContent).toBe("Visa");
  });

  it("shows the pending-count badge on the Visa link when reviews are queued", async () => {
    visaReviewCountMock.mockResolvedValue({ data: [{ id: 1 }, { id: 2 }, { id: 3 }] });
    render(<Nav />);
    await waitFor(() =>
      expect(screen.getByRole("link", { name: /^visa/i }).textContent).toContain("3")
    );
  });
});

describe("Nav — not paused state", () => {
  it("shows Pause Agent button when not paused", async () => {
    render(<Nav />);
    await waitFor(() => screen.getByRole("button", { name: /pause agent/i }));
    expect(screen.getByRole("button", { name: /pause agent/i })).toBeInTheDocument();
  });

  it("shows Run Agent button when not paused", async () => {
    render(<Nav />);
    await waitFor(() => screen.getByRole("button", { name: /run agent/i }));
    expect(screen.getByRole("button", { name: /run agent/i })).not.toBeDisabled();
  });

  it("opens Run Agent confirmation modal on click", async () => {
    const user = userEvent.setup();
    render(<Nav />);
    await waitFor(() => screen.getByRole("button", { name: /run agent/i }));
    await user.click(screen.getByRole("button", { name: /run agent/i }));
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(screen.getByText(/trigger agent now/i)).toBeInTheDocument();
  });

  it("opens Pause scope modal on Pause Agent click", async () => {
    const user = userEvent.setup();
    render(<Nav />);
    await waitFor(() => screen.getByRole("button", { name: /pause agent/i }));
    await user.click(screen.getByRole("button", { name: /pause agent/i }));
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(screen.getByText(/pause the agent/i)).toBeInTheDocument();
    expect(screen.getByText(/pause outbound only/i)).toBeInTheDocument();
    expect(screen.getByText(/pause everything/i)).toBeInTheDocument();
  });

  it("calls trigger-agent API after confirming Run Agent", async () => {
    const user = userEvent.setup();
    render(<Nav />);
    await waitFor(() => screen.getByRole("button", { name: /run agent/i }));
    await user.click(screen.getByRole("button", { name: /run agent/i }));
    const confirmBtn = screen.getByRole("button", { name: /^run agent$/i });
    await user.click(confirmBtn);
    const calls = vi.mocked(global.fetch).mock.calls;
    const triggerCall = calls.find(([url]) => url === "/api/trigger-agent");
    expect(triggerCall).toBeDefined();
    expect(triggerCall![1]).toEqual({ method: "POST" });
  });

  it("sends POST /api/agent-config with selected scope on pause confirm", async () => {
    const user = userEvent.setup();
    render(<Nav />);
    await waitFor(() => screen.getByRole("button", { name: /pause agent/i }));
    await user.click(screen.getByRole("button", { name: /pause agent/i }));
    // Select "Pause everything"
    await user.click(screen.getByText(/pause everything/i));
    await user.click(screen.getByRole("button", { name: /^pause agent$/i }));
    const calls = vi.mocked(global.fetch).mock.calls;
    const postCall = calls.find(([url, opts]) => url === "/api/agent-config" && (opts as RequestInit)?.method === "POST");
    expect(postCall).toBeDefined();
    const body = JSON.parse((postCall![1] as RequestInit).body as string);
    expect(body).toEqual({ scope: "all" });
  });
});

describe("Nav — paused state (agent)", () => {
  beforeEach(() => mockAgentConfig("agent"));

  it("shows Resume Agent button when paused", async () => {
    render(<Nav />);
    await waitFor(() => screen.getByRole("button", { name: /resume agent/i }));
    expect(screen.getByRole("button", { name: /resume agent/i })).toBeInTheDocument();
  });

  it("shows amber paused banner", async () => {
    render(<Nav />);
    await waitFor(() => screen.getByText(/outbound drafts paused/i));
    expect(screen.getByText(/outbound drafts paused/i)).toBeInTheDocument();
  });

  it("Run Agent button is disabled when paused", async () => {
    render(<Nav />);
    await waitFor(() => screen.getByRole("button", { name: /resume agent/i }));
    const runBtn = screen.getByRole("button", { name: /run agent/i });
    expect(runBtn).toBeDisabled();
  });

  it("opens Resume confirmation modal on Resume Agent click", async () => {
    const user = userEvent.setup();
    render(<Nav />);
    await waitFor(() => screen.getByRole("button", { name: /resume agent/i }));
    await user.click(screen.getByRole("button", { name: /resume agent/i }));
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(screen.getByText(/resume the agent/i)).toBeInTheDocument();
  });

  it("sends POST with scope=none on Resume confirm", async () => {
    const user = userEvent.setup();
    render(<Nav />);
    await waitFor(() => screen.getByRole("button", { name: /resume agent/i }));
    await user.click(screen.getByRole("button", { name: /resume agent/i }));
    await user.click(screen.getByRole("button", { name: /^resume$/i }));
    const calls = vi.mocked(global.fetch).mock.calls;
    const postCall = calls.find(([url, opts]) => url === "/api/agent-config" && (opts as RequestInit)?.method === "POST");
    expect(postCall).toBeDefined();
    const body = JSON.parse((postCall![1] as RequestInit).body as string);
    expect(body).toEqual({ scope: "none" });
  });
});

describe("Nav — paused state (all)", () => {
  beforeEach(() => mockAgentConfig("all"));

  it("shows red paused banner for full pause", async () => {
    render(<Nav />);
    await waitFor(() => screen.getByText(/agent and monitor fully paused/i));
    const banner = screen.getByText(/agent and monitor fully paused/i).closest("div");
    expect(banner?.className).toMatch(/red/);
  });
});
