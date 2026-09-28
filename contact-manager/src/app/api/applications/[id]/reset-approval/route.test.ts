import { describe, it, expect, vi, beforeEach } from "vitest";
import { POST } from "./route";

const mockRpc = vi.fn();
vi.mock("@supabase/supabase-js", () => ({
  createClient: vi.fn(() => ({ rpc: mockRpc })),
}));

beforeEach(() => {
  mockRpc.mockReset();
  mockRpc.mockResolvedValue({ data: null, error: null });
});

function makeRequest(id = "5") {
  return { params: Promise.resolve({ id }) };
}

describe("POST /api/applications/[id]/reset-approval", () => {
  it("calls the reset_approval RPC and returns 200", async () => {
    const res = await POST(new Request("http://test", { method: "POST" }), makeRequest());
    expect(res.status).toBe(200);
    expect(mockRpc).toHaveBeenCalledWith("reset_approval", { p_id: 5 });
  });

  // Merge review 2026-09-28, finding 6: reset_approval used to clear approved_at only, with a
  // second, separate anon-permitted UPDATE in this route clearing apply_blocked_reason. If that
  // second write failed, the row stayed visibly blocked and a second "Try again" tap failed at
  // the RPC's own approved_at IS NOT NULL guard (no longer true after the first call's partial
  // success) -- so the advertised retry could never actually reach the cleanup step. Migration
  // 20260928000000 moved both clears into the RPC's single guarded UPDATE, removing the
  // partial-failure window entirely: one RPC call now either clears both fields or clears
  // neither. This route no longer performs any Supabase call beyond the RPC itself.
  it("does not perform any write beyond the single reset_approval RPC call", async () => {
    await POST(new Request("http://test", { method: "POST" }), makeRequest());
    expect(mockRpc).toHaveBeenCalledTimes(1);
  });

  it("returns 409 when the RPC rejects the row (not resettable, or already reset)", async () => {
    mockRpc.mockResolvedValue({ data: null, error: { message: "reset_approval: row 5 is not in a resettable state" } });
    const res = await POST(new Request("http://test", { method: "POST" }), makeRequest());
    expect(res.status).toBe(409);
  });

  // Regression for the exact bug finding 6 described: simulate what a *second* Try again tap
  // used to hit under the old two-step implementation (RPC already succeeded once, so a repeat
  // call correctly 409s under the unchanged guard) -- the fix means this is now a real, useful
  // 409 (nothing left to clean up) rather than a permanent dead end masking an unclean prior
  // partial write.
  it("a repeat call after a successful reset 409s cleanly instead of silently no-oping", async () => {
    const res1 = await POST(new Request("http://test", { method: "POST" }), makeRequest());
    expect(res1.status).toBe(200);

    mockRpc.mockResolvedValue({ data: null, error: { message: "reset_approval: row 5 is not in a resettable state" } });
    const res2 = await POST(new Request("http://test", { method: "POST" }), makeRequest());
    expect(res2.status).toBe(409);
  });

  it("rejects a non-numeric id without calling the RPC", async () => {
    const res = await POST(new Request("http://test", { method: "POST" }), makeRequest("abc"));
    expect(res.status).toBe(400);
    expect(mockRpc).not.toHaveBeenCalled();
  });
});
