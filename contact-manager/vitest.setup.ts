import "@testing-library/jest-dom/vitest";
import { afterEach, vi } from "vitest";
import { cleanup } from "@testing-library/react";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

// Stub env vars so importing modules that read them doesn't blow up.
process.env.NEXT_PUBLIC_SUPABASE_URL = "https://test.supabase.co";
process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY = "sb_publishable_test";
process.env.ANTHROPIC_API_KEY = "test-key";

// IntersectionObserver is not implemented in jsdom.
// Uses a plain function (not vi.fn) so vi.restoreAllMocks() never resets it.
// Individual test files override global.IntersectionObserver in a beforeAll/beforeEach.
(function setupIntersectionObserver() {
  function IntersectionObserverMock(
    this: object,
    _cb: IntersectionObserverCallback
  ) {
    return { observe() {}, disconnect() {}, unobserve() {} };
  }
  global.IntersectionObserver =
    IntersectionObserverMock as unknown as typeof IntersectionObserver;
})();
