import { createHash, createHmac, timingSafeEqual } from "node:crypto";

// Single-operator login for the contact-manager (spec 2026-10-08 §9.1). A session is an expiry
// plus an HMAC over it under SESSION_SECRET, kept in an HttpOnly, SameSite=Strict cookie; the
// strict cookie is also what keeps a cross-site page from posting to the submit route.
export const SESSION_COOKIE = "cm_session";
export const SESSION_TTL_SECONDS = 30 * 24 * 60 * 60;
const MIN_PASSWORD_LENGTH = 16;
const MIN_SECRET_LENGTH = 32;

export interface AuthConfig {
  password: string;
  secret: string;
}

export function authConfig(): AuthConfig | null {
  const password = process.env.OPERATOR_PASSWORD ?? "";
  const secret = process.env.SESSION_SECRET ?? "";
  if (password.length < MIN_PASSWORD_LENGTH || secret.length < MIN_SECRET_LENGTH) return null;
  return { password, secret };
}

function sessionMac(secret: string, expSeconds: number): string {
  return createHmac("sha256", secret).update(`session:v1:${expSeconds}`).digest("hex");
}

function equalHex(a: string, b: string): boolean {
  const left = Buffer.from(a, "utf8");
  const right = Buffer.from(b, "utf8");
  return left.length === right.length && timingSafeEqual(left, right);
}

export function createSessionToken(secret: string, nowMs: number = Date.now()): string {
  const exp = Math.floor(nowMs / 1000) + SESSION_TTL_SECONDS;
  return `${exp}.${sessionMac(secret, exp)}`;
}

export function verifySessionToken(token: string | undefined | null, secret: string, nowMs: number = Date.now()): boolean {
  if (!token) return false;
  const match = /^(\d{1,12})\.([0-9a-f]{64})$/.exec(token);
  if (!match) return false;
  const exp = Number(match[1]);
  if (exp * 1000 <= nowMs) return false;
  return equalHex(sessionMac(secret, exp), match[2]);
}

export function passwordMatches(input: unknown, expected: string): boolean {
  if (typeof input !== "string" || input.length === 0) return false;
  const digest = (s: string) => createHash("sha256").update(s).digest("hex");
  return equalHex(digest(input), digest(expected));
}

function cookieValue(header: string | null, name: string): string | null {
  if (!header) return null;
  for (const part of header.split(";")) {
    const [key, ...rest] = part.trim().split("=");
    if (key === name) return rest.join("=");
  }
  return null;
}

/** True only when login is configured and the request carries a valid operator session. */
export function isOperatorRequest(req: Request): boolean {
  const config = authConfig();
  if (!config) return false;
  return verifySessionToken(cookieValue(req.headers.get("cookie"), SESSION_COOKIE), config.secret);
}

export function sessionCookie(token: string): string {
  return `${SESSION_COOKIE}=${token}; Path=/; HttpOnly; Secure; SameSite=Strict; Max-Age=${SESSION_TTL_SECONDS}`;
}

export function clearedSessionCookie(): string {
  return `${SESSION_COOKIE}=; Path=/; HttpOnly; Secure; SameSite=Strict; Max-Age=0`;
}

export { safeNextPath } from "./loginNext";
