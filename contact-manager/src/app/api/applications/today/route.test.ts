import { describe, it, expect, vi, beforeEach } from "vitest";
import { GET } from "./route";
import { dailyCap, DEFAULT_DAILY_CAP } from "@/lib/dailyCap";

const mockGte = vi.fn();
const mockMaybeSingle = vi.fn();
const mockFrom = vi.fn();

vi.mock("@supabase/supabase-js", () => ({ createClient: vi.fn(() => ({ from: mockFrom })) }));

beforeEach(() => {
  vi.clearAllMocks();
  mockGte.mockResolvedValue({ count: 12, error: null });
  mockMaybeSingle.mockResolvedValue({ data: { value: JSON.stringify({ daily_submit_cap: 40 }) }, error: null });
  mockFrom.mockImplementation((table: string) => table === "prompts"
    ? { select: () => ({ eq: () => ({ maybeSingle: mockMaybeSingle }) }) }
    : { select: () => ({ gte: mockGte }) });
});

describe("GET /api/applications/today", () => {
  it("counts submit attempts since midnight in New York and reads the cap", async () => {
    const res = await GET();
    expect(await res.json()).toEqual({ submitted: 12, cap: 40 });
    const [column, since] = mockGte.mock.calls[0];
    expect(column).toBe("submit_attempted_at");
    expect(new Date(since).getUTCHours() === 4 || new Date(since).getUTCHours() === 5).toBe(true);
  });

  it("returns 500 when the count fails", async () => {
    mockGte.mockResolvedValue({ count: null, error: new Error("db down") });
    expect((await GET()).status).toBe(500);
  });
});

describe("dailyCap", () => {
  it.each([
    [JSON.stringify({ daily_submit_cap: 20 }), 20],
    [JSON.stringify({ daily_submit_cap: 0 }), 0],
    [JSON.stringify({ daily_submit_cap: -1 }), DEFAULT_DAILY_CAP],
    [JSON.stringify({ daily_submit_cap: "lots" }), DEFAULT_DAILY_CAP],
    ["not json", DEFAULT_DAILY_CAP],
    [null, DEFAULT_DAILY_CAP],
  ])("%s -> %s", (raw, cap) => {
    expect(dailyCap(raw)).toBe(cap);
  });
});
