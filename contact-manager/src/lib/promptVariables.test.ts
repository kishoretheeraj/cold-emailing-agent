import { describe, it, expect } from "vitest";
import {
  extractVariables,
  getPythonFormatPlaceholders,
  getUnknownVariables,
  PROMPT_OUTPUT_SCHEMAS,
} from "./promptVariables";

describe("extractVariables", () => {
  it("extracts named variables in order", () => {
    expect(
      extractVariables("Hello {name}, you are at {company}")
    ).toEqual(["name", "company"]);
  });

  it("returns empty array when no placeholders", () => {
    expect(extractVariables("No placeholders")).toEqual([]);
  });

  it("deduplicates preserving first-occurrence order", () => {
    expect(extractVariables("{a} {b} {a} {c}")).toEqual(["a", "b", "c"]);
  });

  it("returns empty array for empty string", () => {
    expect(extractVariables("")).toEqual([]);
  });

  it("ignores patterns with spaces, matches valid single-brace identifiers", () => {
    // {{not a single}} has a space so \w+ won't match; {valid} will
    expect(extractVariables("{{not a single}} {valid}")).toEqual(["valid"]);
  });
});

describe("getPythonFormatPlaceholders", () => {
  it("extracts standard word-char placeholders", () => {
    expect(getPythonFormatPlaceholders("{name} at {company}")).toEqual([
      "name",
      "company",
    ]);
  });

  it("extracts placeholders with spaces like {First Name}", () => {
    expect(
      getPythonFormatPlaceholders("recipients read `{First Name}` as a tell")
    ).toEqual(["First Name"]);
  });

  it("ignores escaped double-brace patterns {{...}}", () => {
    expect(
      getPythonFormatPlaceholders("{{escaped}} and {real}")
    ).toEqual(["real"]);
  });

  it("deduplicates preserving first-occurrence order", () => {
    expect(getPythonFormatPlaceholders("{a} {b} {a}")).toEqual(["a", "b"]);
  });

  it("returns empty array when no placeholders", () => {
    expect(getPythonFormatPlaceholders("no placeholders here")).toEqual([]);
  });
});

describe("getUnknownVariables", () => {
  it("returns empty array for prompts not in the valid-key map", () => {
    expect(getUnknownVariables("unknown_prompt_key", "{anything}")).toEqual([]);
  });

  it("returns empty array when all placeholders are valid", () => {
    expect(
      getUnknownVariables("subject_prompt", "Hello {name} at {company}")
    ).toEqual([]);
  });

  it("flags a placeholder with spaces that Python would choke on", () => {
    expect(
      getUnknownVariables(
        "subject_prompt",
        "recipients read `{First Name}` as a tell, use {name} instead"
      )
    ).toEqual(["First Name"]);
  });

  it("flags an unknown word-char placeholder", () => {
    expect(
      getUnknownVariables("subject_prompt", "{name} — {sender_name}")
    ).toEqual(["sender_name"]);
  });

  it("returns multiple unknowns in order of appearance", () => {
    expect(
      getUnknownVariables("subject_prompt", "{bad_one} {name} {bad_two}")
    ).toEqual(["bad_one", "bad_two"]);
  });
});

describe("PROMPT_OUTPUT_SCHEMAS", () => {
  it("critic_prompt has the expected output keys", () => {
    const schema = PROMPT_OUTPUT_SCHEMAS["critic_prompt"];
    expect(schema).toBeDefined();
    expect(schema.keys).toContain("rewrite_required");
    expect(schema.keys).toContain("verdict");
    expect(schema.keys).toContain("killed_by");
    expect(schema.keys).toContain("failed_soft_criteria");
    expect(schema.keys).toContain("score");
    expect(schema.keys).toContain("feedback");
  });

  it("reply_classification_prompt has classifier_status key", () => {
    const schema = PROMPT_OUTPUT_SCHEMAS["reply_classification_prompt"];
    expect(schema).toBeDefined();
    expect(schema.keys).toContain("classifier_status");
  });

  it("research_query_prompt has empty keys (array output, not object)", () => {
    const schema = PROMPT_OUTPUT_SCHEMAS["research_query_prompt"];
    expect(schema).toBeDefined();
    expect(schema.keys).toHaveLength(0);
    expect(schema.label).toMatch(/array/i);
  });

  it("outreach_prompt is not in output schemas (plain text output)", () => {
    expect(PROMPT_OUTPUT_SCHEMAS["outreach_prompt"]).toBeUndefined();
  });
});
