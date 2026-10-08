import { describe, it, expect } from "vitest";
import { openTakeover } from "./takeover";

const req = { kind: "captcha", reason: "CAPTCHA before Submit", lease: "L", requested_at: "2026-10-08T03:00:00Z", continue_at: null };

describe("openTakeover", () => {
  it("is the request while the worker that asked still holds the row", () => {
    expect(openTakeover({ takeover: req, worker_lease_id: "L" })).toBe(req);
  });

  it.each([
    [{ takeover: null, worker_lease_id: "L" }, "no request"],
    [{ takeover: req, worker_lease_id: null }, "lease released"],
    [{ takeover: req, worker_lease_id: "OTHER" }, "request from an earlier lease"],
    [{ takeover: { ...req, continue_at: "2026-10-08T03:01:00Z" }, worker_lease_id: "L" }, "already answered"],
  ])("is null for %j (%s)", (app) => {
    expect(openTakeover(app)).toBeNull();
  });
});
