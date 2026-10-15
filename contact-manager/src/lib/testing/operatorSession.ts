import { vi } from "vitest";
import { createSessionToken, SESSION_COOKIE } from "@/lib/operatorAuth";

// Test-only helpers for route tests: every mutating route checks isOperatorRequest itself.
export const TEST_OPERATOR_PASSWORD = "correct horse battery staple";
export const TEST_SESSION_SECRET = "s".repeat(32);

export function stubOperatorLogin() {
  vi.stubEnv("OPERATOR_PASSWORD", TEST_OPERATOR_PASSWORD);
  vi.stubEnv("SESSION_SECRET", TEST_SESSION_SECRET);
}

export function operatorCookie(nowMs: number = Date.now()): string {
  return `${SESSION_COOKIE}=${createSessionToken(TEST_SESSION_SECRET, nowMs)}`;
}

/** A Request carrying a valid operator session cookie (call stubOperatorLogin first). */
export function operatorRequest(input: string, init: RequestInit = {}): Request {
  const headers = new Headers(init.headers);
  headers.set("cookie", operatorCookie());
  return new Request(input, { ...init, headers });
}
