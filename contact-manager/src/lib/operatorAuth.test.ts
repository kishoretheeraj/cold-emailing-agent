import { describe, it, expect, vi, afterEach } from "vitest";
import {
  authConfig,
  clearedSessionCookie,
  createSessionToken,
  isOperatorRequest,
  passwordMatches,
  safeNextPath,
  sessionCookie,
  SESSION_TTL_SECONDS,
  verifySessionToken,
} from "./operatorAuth";

const SECRET = "s".repeat(32);
const NOW = 1791428400000;

afterEach(() => vi.unstubAllEnvs());

function configure() {
  vi.stubEnv("OPERATOR_PASSWORD", "correct horse battery");
  vi.stubEnv("SESSION_SECRET", SECRET);
}

describe("authConfig", () => {
  it("is null unless both values are set and long enough", () => {
    vi.stubEnv("OPERATOR_PASSWORD", "");
    vi.stubEnv("SESSION_SECRET", "");
    expect(authConfig()).toBeNull();
    vi.stubEnv("OPERATOR_PASSWORD", "short");
    vi.stubEnv("SESSION_SECRET", SECRET);
    expect(authConfig()).toBeNull();
    vi.stubEnv("OPERATOR_PASSWORD", "correct horse battery");
    vi.stubEnv("SESSION_SECRET", "short");
    expect(authConfig()).toBeNull();
    configure();
    expect(authConfig()).toEqual({ password: "correct horse battery", secret: SECRET });
  });
});

describe("session tokens", () => {
  it("round-trips and expires after the TTL", () => {
    const token = createSessionToken(SECRET, NOW);
    expect(verifySessionToken(token, SECRET, NOW)).toBe(true);
    expect(verifySessionToken(token, SECRET, NOW + (SESSION_TTL_SECONDS - 1) * 1000)).toBe(true);
    expect(verifySessionToken(token, SECRET, NOW + SESSION_TTL_SECONDS * 1000 + 1000)).toBe(false);
  });

  it("rejects another secret, a moved expiry, a tampered MAC and garbage", () => {
    const token = createSessionToken(SECRET, NOW);
    const [exp, mac] = token.split(".");
    expect(verifySessionToken(token, "t".repeat(32), NOW)).toBe(false);
    expect(verifySessionToken(`${Number(exp) + 86400}.${mac}`, SECRET, NOW)).toBe(false);
    expect(verifySessionToken(`${exp}.${mac.replace(/.$/, mac.endsWith("0") ? "1" : "0")}`, SECRET, NOW)).toBe(false);
    for (const bad of [undefined, null, "", "abc", `${exp}.`, `.${mac}`, `${exp}.${mac}x`, `-1.${mac}`]) {
      expect(verifySessionToken(bad, SECRET, NOW)).toBe(false);
    }
  });
});

describe("passwordMatches", () => {
  it("accepts only the exact password", () => {
    expect(passwordMatches("correct horse battery", "correct horse battery")).toBe(true);
    expect(passwordMatches("correct horse batter", "correct horse battery")).toBe(false);
    expect(passwordMatches("", "correct horse battery")).toBe(false);
    expect(passwordMatches(undefined, "correct horse battery")).toBe(false);
    expect(passwordMatches(["correct horse battery"], "correct horse battery")).toBe(false);
  });
});

describe("isOperatorRequest", () => {
  const req = (cookie?: string) =>
    new Request("http://localhost/api/x", { headers: cookie ? { cookie } : {} });

  it("is false while login is not configured, even with a cookie", () => {
    vi.stubEnv("OPERATOR_PASSWORD", "");
    vi.stubEnv("SESSION_SECRET", "");
    expect(isOperatorRequest(req(`cm_session=${createSessionToken(SECRET)}`))).toBe(false);
  });

  it("needs a valid session cookie", () => {
    configure();
    expect(isOperatorRequest(req())).toBe(false);
    expect(isOperatorRequest(req("cm_session=nope"))).toBe(false);
    expect(isOperatorRequest(req(`other=1; cm_session=${createSessionToken(SECRET)}`))).toBe(true);
    expect(isOperatorRequest(req(`cm_session_x=${createSessionToken(SECRET)}`))).toBe(false);
  });
});

describe("cookies", () => {
  it("are HttpOnly, Secure and SameSite=Strict", () => {
    for (const cookie of [sessionCookie("t"), clearedSessionCookie()]) {
      expect(cookie).toContain("HttpOnly");
      expect(cookie).toContain("Secure");
      expect(cookie).toContain("SameSite=Strict");
      expect(cookie).toContain("Path=/");
    }
    expect(clearedSessionCookie()).toContain("Max-Age=0");
  });
});

describe("safeNextPath", () => {
  it("keeps same-origin paths and drops everything else", () => {
    expect(safeNextPath("/applications")).toBe("/applications");
    expect(safeNextPath("/applications?x=1")).toBe("/applications?x=1");
    for (const bad of ["https://evil.example", "//evil.example", "/\\evil.example", "evil", undefined, 5]) {
      expect(safeNextPath(bad)).toBe("/");
    }
  });
});
