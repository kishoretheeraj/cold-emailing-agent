import { describe, it, expect } from "vitest";
import { CATEGORY_ORDER, PROMPT_CATEGORY_MAP } from "./promptCategories";

describe("PROMPT_CATEGORY_MAP — networking", () => {
  it("has a Networking category in CATEGORY_ORDER", () => {
    expect(CATEGORY_ORDER).toContain("Networking");
  });

  it("maps the three networking prompt keys to the Networking category", () => {
    expect(PROMPT_CATEGORY_MAP["networking_prompt"]).toBe("Networking");
    expect(PROMPT_CATEGORY_MAP["networking_followup_prompt"]).toBe("Networking");
    expect(PROMPT_CATEGORY_MAP["networking_subject_prompt"]).toBe("Networking");
  });
});
