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

function makeRequest(body: unknown = { submitted: true }) {
  return new Request("http://test", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body === null ? undefined : typeof body === "string" ? body : JSON.stringify(body),
  });
}

function makeParams(id = "5") {
  return { params: Promise.resolve({ id }) };
}

describe("POST /api/applications/[id]/resolve-confirmation", () => {
  it.each([true, false])("passes submitted=%s through to the RPC and returns ok", async (submitted) => {
    const res = await POST(makeRequest({ submitted }), makeParams());
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ ok: true });
    expect(mockRpc).toHaveBeenCalledWith("resolve_confirmation", { p_id: 5, p_submitted: submitted });
  });

  it("rejects a non-numeric id without calling the RPC", async () => {
    const res = await POST(makeRequest(), makeParams("abc"));
    expect(res.status).toBe(400);
    expect(mockRpc).not.toHaveBeenCalled();
  });

  it.each([
    [{ submitted: "yes" }, "string"],
    [{ submitted: 1 }, "number"],
    [{}, "missing"],
    ["not json{", "malformed body"],
    [null, "no body"],
  ])("rejects %j (%s) with 400 without calling the RPC", async (body) => {
    const res = await POST(makeRequest(body), makeParams());
    expect(res.status).toBe(400);
    expect(mockRpc).not.toHaveBeenCalled();
  });

  it("returns 409 when the RPC rejects the row (not awaiting confirmation)", async () => {
    mockRpc.mockResolvedValue({
      data: null,
      error: { message: "resolve_confirmation: row 5 is not awaiting confirmation" },
    });
    const res = await POST(makeRequest(), makeParams());
    expect(res.status).toBe(409);
    expect((await res.json()).error).toContain("not awaiting confirmation");
  });
});
