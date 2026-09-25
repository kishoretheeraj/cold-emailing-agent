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

  it("returns 409 when the RPC rejects the row", async () => {
    mockRpc.mockResolvedValue({ data: null, error: { message: "reset_approval: row 5 is not in a resettable state" } });
    const res = await POST(new Request("http://test", { method: "POST" }), makeRequest());
    expect(res.status).toBe(409);
  });

  it("rejects a non-numeric id without calling the RPC", async () => {
    const res = await POST(new Request("http://test", { method: "POST" }), makeRequest("abc"));
    expect(res.status).toBe(400);
    expect(mockRpc).not.toHaveBeenCalled();
  });
});
