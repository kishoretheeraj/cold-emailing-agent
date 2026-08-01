import { describe, it, expect } from "vitest";
import { CADENCE, STAGE_TRANSITIONS, QUEUE_STAGES } from "./cadence";

describe("CADENCE", () => {
  it("all values are positive integers", () => {
    for (const [key, val] of Object.entries(CADENCE)) {
      expect(typeof val).toBe("number");
      expect(Number.isInteger(val)).toBe(true);
      expect(val).toBeGreaterThan(0);
    }
  });
});

describe("STAGE_TRANSITIONS", () => {
  it("QUEUE_STAGES are all in STAGE_TRANSITIONS", () => {
    for (const stage of QUEUE_STAGES) {
      expect(stage in STAGE_TRANSITIONS).toBe(true);
    }
  });

  it("reply_drafted is in STAGE_TRANSITIONS", () => {
    expect("reply_drafted" in STAGE_TRANSITIONS).toBe(true);
  });

  it("covers exactly QUEUE_STAGES + reply_drafted", () => {
    const keys = new Set(Object.keys(STAGE_TRANSITIONS));
    const expected = new Set([...QUEUE_STAGES, "reply_drafted"]);
    expect(keys).toEqual(expected);
  });

  it("terminal stages have cadenceKey null", () => {
    const terminals = [
      "breakup_drafted",
      "applied_followup_drafted",
      "networking_followup_drafted",
      "reply_drafted",
    ] as const;
    for (const stage of terminals) {
      expect(STAGE_TRANSITIONS[stage].cadenceKey).toBeNull();
    }
  });

  it("non-terminal stages have a valid cadenceKey", () => {
    const nonTerminals = [
      "first_touch_drafted",
      "followup1_drafted",
      "followup2_drafted",
      "applied_intro_drafted",
      "networking_drafted",
    ] as const;
    for (const stage of nonTerminals) {
      const key = STAGE_TRANSITIONS[stage].cadenceKey;
      expect(key).not.toBeNull();
      expect(key! in CADENCE).toBe(true);
    }
  });

  it("networking_drafted and networking_followup_drafted are QUEUE_STAGES", () => {
    expect(QUEUE_STAGES).toContain("networking_drafted");
    expect(QUEUE_STAGES).toContain("networking_followup_drafted");
  });

  it("each transition maps to a *_sent next stage", () => {
    for (const [stage, transition] of Object.entries(STAGE_TRANSITIONS)) {
      expect(transition.next).toMatch(/_sent$/);
      expect(transition.next).not.toBe(stage);
    }
  });
});
