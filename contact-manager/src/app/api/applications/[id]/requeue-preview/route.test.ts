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

function makeRequest() {
  return new Request("http://test", { method: "POST" });
}

function makeParams(id = "5") {
  return { params: Promise.resolve({ id }) };
}

describe("POST /api/applications/[id]/requeue-preview", () => {
  it("calls requeue_preview with the numeric id and returns ok", async () => {
    const res = await POST(makeRequest(), makeParams());
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ ok: true });
    expect(mockRpc).toHaveBeenCalledWith("requeue_preview", { p_id: 5 });
  });

  it("rejects a non-numeric id without calling the RPC", async () => {
    const res = await POST(makeRequest(), makeParams("abc"));
    expect(res.status).toBe(400);
    expect(mockRpc).not.toHaveBeenCalled();
  });

  it("returns 409 with the message when the RPC rejects the row", async () => {
    mockRpc.mockResolvedValue({
      data: null,
      error: { message: "requeue_preview: row 5 is not awaiting input" },
    });
    const res = await POST(makeRequest(), makeParams());
    expect(res.status).toBe(409);
    expect((await res.json()).error).toContain("not awaiting input");
  });
});
