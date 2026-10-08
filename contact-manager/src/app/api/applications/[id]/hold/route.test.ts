import { beforeEach, describe, expect, it, vi } from "vitest";
import { DELETE, POST } from "./route";

const mockRpc = vi.fn();
const mockMaybeSingle = vi.fn();

vi.mock("@supabase/supabase-js", () => ({
  createClient: vi.fn(() => ({
    rpc: mockRpc,
    from: () => ({ select: () => ({ eq: () => ({ maybeSingle: mockMaybeSingle }) }) }),
  })),
}));

const params = (id = "7") => ({ params: Promise.resolve({ id }) });

beforeEach(() => {
  vi.clearAllMocks();
  mockMaybeSingle.mockResolvedValue({ data: null, error: null });
  mockRpc.mockResolvedValue({ data: "2026-10-19T12:00:00Z", error: null });
});

describe("POST /api/applications/[id]/hold", () => {
  it("holds for the default ten days", async () => {
    const res = await POST(new Request("http://x", { method: "POST" }), params());
    expect(await res.json()).toEqual({ referral_hold_until: "2026-10-19T12:00:00Z", days: 10 });
    expect(mockRpc).toHaveBeenCalledWith("hold_for_referral", { p_id: 7, p_days: 10 });
  });

  it("reads the hold length from the preferences, clamped to 14", async () => {
    mockMaybeSingle.mockResolvedValue({ data: { value: JSON.stringify({ referral_hold_days: 60 }) }, error: null });
    await POST(new Request("http://x", { method: "POST" }), params());
    expect(mockRpc).toHaveBeenCalledWith("hold_for_referral", { p_id: 7, p_days: 14 });
  });

  it("409s when the row is not an unapproved preview", async () => {
    mockRpc.mockResolvedValue({ data: null, error: { message: "hold_for_referral: row 7 is not an unapproved preview" } });
    expect((await POST(new Request("http://x", { method: "POST" }), params())).status).toBe(409);
  });

  it("400s a bad id without touching the database", async () => {
    expect((await POST(new Request("http://x", { method: "POST" }), params("x"))).status).toBe(400);
    expect(mockRpc).not.toHaveBeenCalled();
  });
});

describe("DELETE /api/applications/[id]/hold", () => {
  it("releases the hold", async () => {
    mockRpc.mockResolvedValue({ data: null, error: null });
    expect((await DELETE(new Request("http://x", { method: "DELETE" }), params())).status).toBe(200);
    expect(mockRpc).toHaveBeenCalledWith("release_referral_hold", { p_id: 7 });
  });

  it("409s an unknown row", async () => {
    mockRpc.mockResolvedValue({ data: null, error: { message: "release_referral_hold: no row 7" } });
    expect((await DELETE(new Request("http://x", { method: "DELETE" }), params())).status).toBe(409);
  });
});
