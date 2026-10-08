import { describe, it, expect, vi, afterEach } from "vitest";
import { approvalSigningKey, signApproval } from "./approvalSignature";

// Shared with tests/test_approval_signature.py -- the Python worker must verify what this signs.
const VECTOR = "813fb85b60f9e586a35ca8c3bff7a1636ace15b7aaa404167fbb7505fb1e751d";

afterEach(() => vi.unstubAllEnvs());

describe("signApproval", () => {
  it("matches the vector the Python verifier pins", () => {
    expect(signApproval("k".repeat(32), 42, "a".repeat(64), 1791428400000)).toBe(VECTOR);
  });

  it("changes with every signed component", () => {
    const base = signApproval("k".repeat(32), 42, "a".repeat(64), 1791428400000);
    expect(signApproval("k".repeat(32), 43, "a".repeat(64), 1791428400000)).not.toBe(base);
    expect(signApproval("k".repeat(32), 42, "b".repeat(64), 1791428400000)).not.toBe(base);
    expect(signApproval("k".repeat(32), 42, "a".repeat(64), 1791428400001)).not.toBe(base);
    expect(signApproval("j".repeat(32), 42, "a".repeat(64), 1791428400000)).not.toBe(base);
  });
});

describe("approvalSigningKey", () => {
  it("is null when unset or shorter than 32 characters", () => {
    vi.stubEnv("APPROVAL_SIGNING_KEY", "");
    expect(approvalSigningKey()).toBeNull();
    vi.stubEnv("APPROVAL_SIGNING_KEY", "short");
    expect(approvalSigningKey()).toBeNull();
  });

  it("returns a long enough key", () => {
    vi.stubEnv("APPROVAL_SIGNING_KEY", "k".repeat(32));
    expect(approvalSigningKey()).toBe("k".repeat(32));
  });
});
