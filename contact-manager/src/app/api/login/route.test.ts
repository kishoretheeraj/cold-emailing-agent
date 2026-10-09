import { describe, it, expect, vi, afterEach, beforeEach } from "vitest";
import { POST } from "./route";
import { POST as LOGOUT } from "../logout/route";
import { verifySessionToken } from "@/lib/operatorAuth";

const SECRET = "s".repeat(32);

beforeEach(() => vi.useFakeTimers());
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllEnvs();
});

function configure() {
  vi.stubEnv("OPERATOR_PASSWORD", "correct horse battery");
  vi.stubEnv("SESSION_SECRET", SECRET);
}

const login = (body: unknown) =>
  POST(new Request("http://localhost/api/login", { method: "POST", body: JSON.stringify(body) }));

describe("POST /api/login", () => {
  it("is 503 while login is not configured", async () => {
    vi.stubEnv("OPERATOR_PASSWORD", "");
    vi.stubEnv("SESSION_SECRET", "");
    const res = await login({ password: "anything" });
    expect(res.status).toBe(503);
    expect(res.headers.get("set-cookie")).toBeNull();
  });

  it("sets a valid session cookie for the right password", async () => {
    configure();
    const res = await login({ password: "correct horse battery" });
    expect(res.status).toBe(200);
    const cookie = res.headers.get("set-cookie")!;
    expect(cookie).toContain("HttpOnly");
    const token = /cm_session=([^;]+)/.exec(cookie)![1];
    expect(verifySessionToken(token, SECRET)).toBe(true);
  });

  it("is 401 with no cookie for a wrong or missing password, after a delay", async () => {
    configure();
    for (const body of [{ password: "wrong" }, {}, "not-json"]) {
      const pending = login(body);
      await vi.advanceTimersByTimeAsync(750);
      const res = await pending;
      expect(res.status).toBe(401);
      expect(res.headers.get("set-cookie")).toBeNull();
    }
  });
});

describe("POST /api/logout", () => {
  it("clears the cookie", async () => {
    const res = await LOGOUT();
    expect(res.headers.get("set-cookie")).toContain("Max-Age=0");
  });
});
