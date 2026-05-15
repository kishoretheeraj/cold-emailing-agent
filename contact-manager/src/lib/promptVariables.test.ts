import { describe, it, expect } from "vitest";
import { extractVariables } from "./promptVariables";

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
