import { describe, it, expect, vi, beforeEach } from "vitest";
import { GET } from "./route";

const mockOrder = vi.fn();
const mockSelect = vi.fn();
const mockFrom = vi.fn();

vi.mock("@supabase/supabase-js", () => ({
  createClient: vi.fn(() => ({ from: mockFrom })),
}));

beforeEach(() => {
  vi.clearAllMocks();
  mockOrder.mockResolvedValue({
    data: [
      { source: "agent", status: "success", ran_at: "2026-09-24T10:00:00Z", failure_reason: null },
      { source: "cu_linkedin", status: "blocked", ran_at: "2026-09-24T09:00:00Z",
        failure_reason: "CAPTCHA_OR_CHALLENGE: needs a human at the VNC console for display slot 0" },
    ],
    error: null,
  });
  mockSelect.mockReturnValue({ order: mockOrder });
  mockFrom.mockReturnValue({ select: mockSelect });
});

describe("GET /api/system-health", () => {
  it("returns the latest row per source", async () => {
    const res = await GET();
    const body = await res.json();
    expect(body.health).toHaveLength(2);
    expect(body.health[1].status).toBe("blocked");
  });

  it("queries the agent_runs_latest_by_source view", async () => {
    await GET();
    expect(mockFrom).toHaveBeenCalledWith("agent_runs_latest_by_source");
  });

  it("returns 500 on a supabase error", async () => {
    mockOrder.mockResolvedValue({ data: null, error: new Error("db down") });
    const res = await GET();
    expect(res.status).toBe(500);
  });
});
