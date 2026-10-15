import { describe, it, expect, vi, beforeEach } from "vitest";
import { NextRequest } from "next/server";
import { POST } from "./route";

const mockRpc = vi.fn();
const mockFrom = vi.fn();

vi.mock("@/lib/supabase-server", () => ({ getServerSupabase: () => ({ rpc: mockRpc, from: mockFrom }) }));

function jsonRequest(body: unknown) {
  return new NextRequest("http://localhost/api/pipeline/link-reply", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
}

beforeEach(() => {
  vi.clearAllMocks();
  mockRpc.mockResolvedValue({ data: { linked: true, event: "link:x", event_written: true }, error: null });
});

describe("POST /api/pipeline/link-reply", () => {
  it("links through the RPC with the reply row id and a numeric application id, never a table write", async () => {
    const res = await POST(jsonRequest({ reply_id: "abc123", application_id: "482" }));
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ ok: true, event: true });
    expect(mockRpc).toHaveBeenCalledWith("pipeline_link_reply", { p_reply_id: "abc123", p_application_id: 482 });
    expect(mockFrom).not.toHaveBeenCalled();
  });

  it("redirects a form post back to /pipeline", async () => {
    const form = new FormData();
    form.set("reply_id", "abc123");
    form.set("application_id", "7");
    const res = await POST(new NextRequest("http://localhost/api/pipeline/link-reply", { method: "POST", body: form }));
    expect(res.status).toBe(303);
    expect(res.headers.get("location")).toBe("http://localhost/pipeline");
  });

  it.each([
    [{ reply_id: "", application_id: "1" }],
    [{ reply_id: "abc", application_id: "" }],
    [{ reply_id: "abc", application_id: "job-12" }],
    [{ reply_message_id: "<m@x>", application_id: "1" }],
  ])("rejects %j with 400 before any database call", async (body) => {
    const res = await POST(jsonRequest(body));
    expect(res.status).toBe(400);
    expect(mockRpc).not.toHaveBeenCalled();
  });

  it.each([
    ["P0002", 404],
    ["P0001", 409],
    ["42501", 500],
  ])("maps RPC error %s to %i", async (code, status) => {
    mockRpc.mockResolvedValue({ data: null, error: { code, message: "x" } });
    const res = await POST(jsonRequest({ reply_id: "abc", application_id: 3 }));
    expect(res.status).toBe(status);
  });
});
