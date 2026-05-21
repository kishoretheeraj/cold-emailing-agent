import { describe, it, expect } from "vitest";
import { highlight } from "./personalization";
import type { Contact } from "./types";

function makeContact(overrides: Partial<Contact> = {}): Contact {
  return {
    id: "1",
    name: null,
    email: null,
    company: null,
    role: null,
    detail: null,
    tier: 1,
    mode: "outreach",
    stage: "first_touch_drafted",
    reply_status: "no_reply",
    classifier_status: null,
    dartmouth: false,
    job_title: null,
    job_description: null,
    company_applied: null,
    applied_date: null,
    followup_date: null,
    notes: null,
    resume_url: null,
    created_at: "2026-01-01T00:00:00.000Z",
    message_id: null,
    last_emailed: null,
    deleted_at: null,
    ...overrides,
  };
}

function segments(segs: ReturnType<typeof highlight>) {
  return segs.map((s) => `${s.highlighted ? "[" : ""}${s.text}${s.highlighted ? "]" : ""}`).join("");
}

describe("highlight", () => {
  it("highlights first name in body", () => {
    const contact = makeContact({ name: "Alice Chen" });
    const body = "Hi Alice, hope you're well.";
    const result = highlight(body, contact);
    expect(segments(result)).toBe("Hi [Alice], hope you're well.");
  });

  it("highlights company in body", () => {
    const contact = makeContact({ company: "Acme Corp" });
    const body = "I noticed Acme Corp is expanding.";
    const result = highlight(body, contact);
    expect(segments(result)).toBe("I noticed [Acme Corp] is expanding.");
  });

  it("highlights T'YY alumni pattern", () => {
    const contact = makeContact({ name: "Bob" });
    const body = "As a T'22 alum, I wanted to reach out.";
    const result = highlight(body, contact);
    const highlighted = result.filter((s) => s.highlighted).map((s) => s.text);
    expect(highlighted).toContain("T'22");
  });

  it("highlights Dartmouth keyword", () => {
    const contact = makeContact({});
    const body = "My Dartmouth network speaks highly of your work.";
    const result = highlight(body, contact);
    const highlighted = result.filter((s) => s.highlighted).map((s) => s.text);
    expect(highlighted).toContain("Dartmouth");
  });

  it("highlights Tuck keyword", () => {
    const contact = makeContact({});
    const body = "As a Tuck T'22 graduate.";
    const result = highlight(body, contact);
    const highlighted = result.filter((s) => s.highlighted).map((s) => s.text);
    expect(highlighted).toContain("Tuck");
  });

  it("handles overlapping matches by merging ranges", () => {
    // company "Tuck" would overlap with Tuck keyword — should merge, not double-highlight
    const contact = makeContact({ company: "Tuck School" });
    const body = "I studied at Tuck School and loved it.";
    const result = highlight(body, contact);
    // Should not have adjacent highlighted segments for same range
    const hiSegs = result.filter((s) => s.highlighted);
    // No two adjacent highlighted segments should be next to each other
    for (let i = 0; i < hiSegs.length - 1; i++) {
      const aEnd = body.indexOf(hiSegs[i].text);
      const bStart = body.indexOf(hiSegs[i + 1].text, aEnd);
      expect(bStart).toBeGreaterThanOrEqual(aEnd + hiSegs[i].text.length);
    }
  });

  it("returns a single unhighlighted segment when no matches", () => {
    const contact = makeContact({ name: "Zzz Yyy", company: "Nope Inc" });
    const body = "Nothing matches here at all.";
    const result = highlight(body, contact);
    // name "Zzz" not in body, company "Nope" not whole-word-matched
    // T'YY, Dartmouth, Tuck, Thayer not present
    const highlighted = result.filter((s) => s.highlighted);
    expect(highlighted.length).toBe(0);
    expect(result[0].text).toBe(body);
  });

  it("joined segments reconstruct the full body", () => {
    const contact = makeContact({ name: "Alice Chen", company: "Acme Corp" });
    const body = "Hi Alice, I work at Acme Corp and went to Tuck T'21.";
    const result = highlight(body, contact);
    expect(result.map((s) => s.text).join("")).toBe(body);
  });

  it("is case-insensitive for first name", () => {
    const contact = makeContact({ name: "alice chen" });
    const body = "Hi Alice, great to meet you.";
    const result = highlight(body, contact);
    const highlighted = result.filter((s) => s.highlighted).map((s) => s.text);
    expect(highlighted.some((t) => t.toLowerCase() === "alice")).toBe(true);
  });
});
