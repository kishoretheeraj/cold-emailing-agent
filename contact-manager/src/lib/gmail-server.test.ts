import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

// Mock googleapis before any import of gmail-server.
// OAuth2 must be a constructable function (not arrow), so we use a regular function class.
vi.mock("googleapis", () => {
  function OAuth2(this: { credentials: Record<string, unknown> }) {
    this.credentials = {};
  }
  OAuth2.prototype.setCredentials = vi.fn();

  return {
    google: {
      auth: { OAuth2 },
      gmail: vi.fn(() => ({ users: { drafts: {}, messages: {} } })),
    },
  };
});

// gmail-server uses "server-only" — aliased to a no-op stub via vitest.config.ts.
import { getGmailClient } from "./gmail-server";

describe("getGmailClient", () => {
  const saved: Record<string, string | undefined> = {};

  beforeEach(() => {
    saved.clientId = process.env.GOOGLE_OAUTH_CLIENT_ID;
    saved.clientSecret = process.env.GOOGLE_OAUTH_CLIENT_SECRET;
    saved.refreshToken = process.env.GOOGLE_OAUTH_REFRESH_TOKEN;
    delete process.env.GOOGLE_OAUTH_CLIENT_ID;
    delete process.env.GOOGLE_OAUTH_CLIENT_SECRET;
    delete process.env.GOOGLE_OAUTH_REFRESH_TOKEN;
  });

  afterEach(() => {
    if (saved.clientId !== undefined)
      process.env.GOOGLE_OAUTH_CLIENT_ID = saved.clientId;
    if (saved.clientSecret !== undefined)
      process.env.GOOGLE_OAUTH_CLIENT_SECRET = saved.clientSecret;
    if (saved.refreshToken !== undefined)
      process.env.GOOGLE_OAUTH_REFRESH_TOKEN = saved.refreshToken;
  });

  it("throws when GOOGLE_OAUTH_CLIENT_ID is missing", () => {
    process.env.GOOGLE_OAUTH_CLIENT_SECRET = "secret";
    process.env.GOOGLE_OAUTH_REFRESH_TOKEN = "token";
    expect(() => getGmailClient()).toThrow(/GOOGLE_OAUTH_CLIENT_ID/);
  });

  it("throws when GOOGLE_OAUTH_CLIENT_SECRET is missing", () => {
    process.env.GOOGLE_OAUTH_CLIENT_ID = "id";
    process.env.GOOGLE_OAUTH_REFRESH_TOKEN = "token";
    expect(() => getGmailClient()).toThrow(/GOOGLE_OAUTH_CLIENT_SECRET/);
  });

  it("throws when GOOGLE_OAUTH_REFRESH_TOKEN is missing", () => {
    process.env.GOOGLE_OAUTH_CLIENT_ID = "id";
    process.env.GOOGLE_OAUTH_CLIENT_SECRET = "secret";
    expect(() => getGmailClient()).toThrow(/GOOGLE_OAUTH_REFRESH_TOKEN/);
  });

  it("returns a Gmail client when all vars are present", () => {
    process.env.GOOGLE_OAUTH_CLIENT_ID = "id";
    process.env.GOOGLE_OAUTH_CLIENT_SECRET = "secret";
    process.env.GOOGLE_OAUTH_REFRESH_TOKEN = "token";
    const client = getGmailClient();
    expect(client).toBeDefined();
    expect(client).toHaveProperty("users");
  });
});
