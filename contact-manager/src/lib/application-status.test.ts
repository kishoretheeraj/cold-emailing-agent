import { describe, expect, it } from "vitest";
import {
  BUCKET_LABELS,
  bucketCounts,
  toBucket,
} from "./application-status";
import type { AutomationStatus } from "@/lib/types";
import type { DisplayBucket } from "./application-status";

const EXPECTED: [AutomationStatus, DisplayBucket][] = [
  ["ready_for_review", "action"],
  ["needs_input", "action"],
  ["needs_confirmation", "action"],
  ["failed_retryable", "action"],
  ["failed_terminal", "action"],
  ["unsupported", "action"],
  ["idle", "progress"],
  ["preparing", "progress"],
  ["approved", "progress"],
  ["submitting", "progress"],
  ["submitted", "done"],
];

describe("toBucket", () => {
  it.each(EXPECTED)("maps %s to %s", (status, bucket) => {
    expect(toBucket(status)).toBe(bucket);
  });

  it("covers all eleven statuses", () => {
    expect(new Set(EXPECTED.map(([s]) => s)).size).toBe(11);
  });
});

describe("BUCKET_LABELS", () => {
  it("has a label for every bucket", () => {
    expect(BUCKET_LABELS).toEqual({
      action: "Action Needed",
      progress: "In Progress",
      done: "Done",
    });
  });
});

describe("bucketCounts", () => {
  it("counts a mixed list", () => {
    const apps = [
      "ready_for_review",
      "needs_input",
      "failed_terminal",
      "idle",
      "submitting",
      "submitted",
      "submitted",
      "submitted",
    ].map((automation_status) => ({
      automation_status: automation_status as AutomationStatus,
    }));
    expect(bucketCounts(apps)).toEqual({ action: 3, progress: 2, done: 3 });
  });

  it("returns zeros for an empty list", () => {
    expect(bucketCounts([])).toEqual({ action: 0, progress: 0, done: 0 });
  });
});
