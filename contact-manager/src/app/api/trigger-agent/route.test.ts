import { describe, it, expect, vi, beforeEach } from "vitest";
import { operatorRequest, stubOperatorLogin } from "@/lib/testing/operatorSession";
import { POST } from "./route";

beforeEach(() => {
  stubOperatorLogin();
  vi.stubEnv("GITHUB_DISPATCH_TOKEN", "test-token");
  vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(new Response(null, { status: 204 }))));
});

const req = () => operatorRequest("http://localhost/api/trigger-agent", { method: "POST" });

describe("POST /api/trigger-agent", () => {
  it("dispatches daily_agent.yml on main", async () => {
    const res = await POST(req());
    expect(res.status).toBe(200);
    const [url, init] = vi.mocked(global.fetch).mock.calls[0];
    expect(url).toBe(
      "https://api.github.com/repos/kishoretheeraj/cold-emailing-agent/actions/workflows/daily_agent.yml/dispatches"
    );
    expect(JSON.parse((init as RequestInit).body as string)).toEqual({ ref: "main" });
  });

  it("returns 500 without a dispatch token", async () => {
    vi.stubEnv("GITHUB_DISPATCH_TOKEN", "");
    const res = await POST(req());
    expect(res.status).toBe(500);
    expect(global.fetch).not.toHaveBeenCalled();
  });

  it("passes GitHub's error through", async () => {
    vi.mocked(global.fetch).mockResolvedValueOnce(new Response("Not Found", { status: 404 }));
    const res = await POST(req());
    expect(res.status).toBe(404);
    expect(await res.json()).toEqual({ error: "Not Found" });
  });
});
