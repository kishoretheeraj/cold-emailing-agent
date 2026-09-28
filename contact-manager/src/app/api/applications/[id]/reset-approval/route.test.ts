import { describe, it, expect, vi, beforeEach } from "vitest";
import { POST } from "./route";

const mockRpc = vi.fn();
const mockEq = vi.fn();
const mockUpdate = vi.fn();
const mockFrom = vi.fn();
vi.mock("@supabase/supabase-js", () => ({
  createClient: vi.fn(() => ({ rpc: mockRpc, from: mockFrom })),
}));

beforeEach(() => {
  mockRpc.mockReset();
  mockRpc.mockResolvedValue({ data: null, error: null });
  mockEq.mockReset();
  mockEq.mockResolvedValue({ data: null, error: null });
  mockUpdate.mockReset();
  mockUpdate.mockReturnValue({ eq: mockEq });
  mockFrom.mockReset();
  mockFrom.mockReturnValue({ update: mockUpdate });
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

  // C1 follow-up: reset_approval only clears approved_at. Without also clearing
  // apply_blocked_reason here, ApplicationsPage.tsx's Try again fallback (which reads
  // apply_blocked_reason straight from the row) would keep showing the blocked state forever
  // after a reset -- the row would never become recoverable via Approve & Submit again.
  it("also clears apply_blocked_reason on the row after a successful reset (C1 follow-up)", async () => {
    const res = await POST(new Request("http://test", { method: "POST" }), makeRequest());
    expect(res.status).toBe(200);
    expect(mockFrom).toHaveBeenCalledWith("job_applications");
    expect(mockUpdate).toHaveBeenCalledWith(
      expect.objectContaining({ apply_blocked_reason: null })
    );
    expect(mockEq).toHaveBeenCalledWith("id", 5);
  });

  it("still returns 200 if clearing apply_blocked_reason fails (best-effort, never blocks the reset)", async () => {
    mockEq.mockResolvedValue({ data: null, error: { message: "row locked" } });
    const res = await POST(new Request("http://test", { method: "POST" }), makeRequest());
    expect(res.status).toBe(200);
  });

  it("returns 409 when the RPC rejects the row, without attempting to clear apply_blocked_reason", async () => {
    mockRpc.mockResolvedValue({ data: null, error: { message: "reset_approval: row 5 is not in a resettable state" } });
    const res = await POST(new Request("http://test", { method: "POST" }), makeRequest());
    expect(res.status).toBe(409);
    expect(mockFrom).not.toHaveBeenCalled();
  });

  it("rejects a non-numeric id without calling the RPC", async () => {
    const res = await POST(new Request("http://test", { method: "POST" }), makeRequest("abc"));
    expect(res.status).toBe(400);
    expect(mockRpc).not.toHaveBeenCalled();
  });
});
