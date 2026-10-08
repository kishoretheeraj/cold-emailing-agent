import { describe, it, expect, vi, afterEach } from "vitest";
import { NextRequest } from "next/server";
import { proxy } from "./proxy";
import { createSessionToken } from "@/lib/operatorAuth";

const SECRET = "s".repeat(32);

afterEach(() => vi.unstubAllEnvs());

function configure() {
  vi.stubEnv("OPERATOR_PASSWORD", "correct horse battery");
  vi.stubEnv("SESSION_SECRET", SECRET);
}

function request(path: string, cookie?: string) {
  return new NextRequest(`http://localhost:3000${path}`, { headers: cookie ? { cookie } : {} });
}

const passesThrough = (res: Response) => res.headers.get("x-middleware-next") === "1";

describe("proxy", () => {
  it("passes everything through while login is not configured", () => {
    vi.stubEnv("OPERATOR_PASSWORD", "");
    vi.stubEnv("SESSION_SECRET", "");
    expect(passesThrough(proxy(request("/applications")))).toBe(true);
    expect(passesThrough(proxy(request("/api/applications")))).toBe(true);
  });

  it("redirects pages to /login with the original path", () => {
    configure();
    const res = proxy(request("/applications?stage=saved"));
    expect(res.status).toBe(307);
    const location = new URL(res.headers.get("location")!);
    expect(location.pathname).toBe("/login");
    expect(location.searchParams.get("next")).toBe("/applications?stage=saved");
  });

  it("answers API calls with 401 instead of a redirect", async () => {
    configure();
    const res = proxy(request("/api/applications/5/submit"));
    expect(res.status).toBe(401);
    expect(await res.json()).toEqual({ error: "Sign in required" });
  });

  it("lets the login page and login/logout routes through", () => {
    configure();
    for (const path of ["/login", "/api/login", "/api/logout"]) {
      expect(passesThrough(proxy(request(path)))).toBe(true);
    }
  });

  it("does not treat look-alike paths as public", () => {
    configure();
    expect(proxy(request("/login-help")).status).toBe(307);
    expect(proxy(request("/api/login/x")).status).toBe(401);
  });

  it("lets a valid session through and rejects a forged one", () => {
    configure();
    expect(passesThrough(proxy(request("/applications", `cm_session=${createSessionToken(SECRET)}`)))).toBe(true);
    expect(proxy(request("/applications", `cm_session=${createSessionToken("t".repeat(32))}`)).status).toBe(307);
  });
});
