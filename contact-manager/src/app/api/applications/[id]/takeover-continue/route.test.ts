import { describe, it, expect, vi, beforeEach } from "vitest";
import { POST } from "./route";

const mockRpc = vi.fn();
vi.mock("@supabase/supabase-js", () => ({ createClient: vi.fn(() => ({ rpc: mockRpc })) }));

beforeEach(() => mockRpc.mockReset());

const call = (id: string) =>
  POST(new Request(`http://localhost/api/applications/${id}/takeover-continue`, { method: "POST" }), {
    params: Promise.resolve({ id }),
  });

describe("POST /api/applications/[id]/takeover-continue", () => {
  it("answers the open request", async () => {
    mockRpc.mockResolvedValue({ data: true, error: null });
    const res = await call("7");
    expect(res.status).toBe(200);
    expect(mockRpc).toHaveBeenCalledWith("takeover_continue", { p_id: 7 });
  });

  it("is 409 when nothing is waiting any more", async () => {
    mockRpc.mockResolvedValue({ data: false, error: null });
    expect((await call("7")).status).toBe(409);
  });

  it("is 500 on an RPC error", async () => {
    mockRpc.mockResolvedValue({ data: null, error: { message: "boom" } });
    expect((await call("7")).status).toBe(500);
  });

  it.each(["abc", "1;drop", ""])("rejects a non-numeric id (%s) without calling the RPC", async (id) => {
    expect((await call(id)).status).toBe(400);
    expect(mockRpc).not.toHaveBeenCalled();
  });
});
