import { describe, it, expect } from "vitest";
import {
  OUTREACH_STAGES,
  APPLIED_STAGES,
  REPLY_STATUSES,
  type ReplyStatus,
} from "./types";

describe("stage and status enums", () => {
  it("outreach stages start at new and end at closed", () => {
    expect(OUTREACH_STAGES[0]).toBe("new");
    expect(OUTREACH_STAGES.at(-1)).toBe("closed");
  });

  it("applied stages start at new and end at closed", () => {
    expect(APPLIED_STAGES[0]).toBe("new");
    expect(APPLIED_STAGES.at(-1)).toBe("closed");
  });

  it("outreach stages have no duplicates", () => {
    expect(new Set(OUTREACH_STAGES).size).toBe(OUTREACH_STAGES.length);
  });

  it("applied stages have no duplicates", () => {
    expect(new Set(APPLIED_STAGES).size).toBe(APPLIED_STAGES.length);
  });

  it("each non-terminal stage transitions through *_drafted then *_sent", () => {
    // For every "_drafted" stage there is a matching "_sent" stage.
    const draftedOutreach = OUTREACH_STAGES.filter((s) => s.endsWith("_drafted"));
    for (const d of draftedOutreach) {
      const sent = d.replace("_drafted", "_sent");
      expect(OUTREACH_STAGES).toContain(sent);
    }
    const draftedApplied = APPLIED_STAGES.filter((s) => s.endsWith("_drafted"));
    for (const d of draftedApplied) {
      const sent = d.replace("_drafted", "_sent");
      expect(APPLIED_STAGES).toContain(sent);
    }
  });

  it("reply statuses cover the user-facing options", () => {
    const expected: ReplyStatus[] = [
      "no_reply",
      "replied",
      "interested",
      "call_scheduled",
      "dead",
    ];
    expect(REPLY_STATUSES).toEqual(expected);
  });
});
