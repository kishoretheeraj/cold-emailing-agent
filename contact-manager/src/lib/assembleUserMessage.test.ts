import { describe, it, expect } from "vitest";
import {
  isDartmouth,
  deriveAction,
  assembleUserMessage,
  assembleCriticMessage,
} from "./assembleUserMessage";
import type { Contact } from "./types";

// ── Helpers ────────────────────────────────────────────────────────────────────

function makeContact(overrides: Partial<Contact> = {}): Contact {
  return {
    id: "1",
    name: "Alice Chen",
    email: "alice@acme.com",
    company: "Acme Corp",
    role: "VP Engineering",
    detail: "Built fintech compliance dashboards",
    tier: 1,
    mode: "outreach",
    stage: "new",
    reply_status: "no_reply",
    classifier_status: null,
    dartmouth: false,
    job_title: null,
    job_description: null,
    company_applied: null,
    applied_date: null,
    connection_context: null,
    followup_date: null,
    notes: null,
    resume_url: null,
    created_at: "2026-06-01T00:00:00Z",
    message_id: null,
    last_emailed: null,
    deleted_at: null,
    state: null,
    ...overrides,
  };
}

// ── isDartmouth ────────────────────────────────────────────────────────────────

describe("isDartmouth", () => {
  it("returns true when contact.dartmouth is true", () => {
    expect(isDartmouth(makeContact({ dartmouth: true }))).toBe(true);
  });

  it("returns true when detail contains 'dartmouth'", () => {
    expect(isDartmouth(makeContact({ detail: "Dartmouth alum" }))).toBe(true);
  });

  it("returns true when detail contains 'tuck' (case-insensitive)", () => {
    expect(isDartmouth(makeContact({ detail: "Tuck MBA colleague" }))).toBe(true);
  });

  it("returns true when detail contains 'thayer'", () => {
    expect(isDartmouth(makeContact({ detail: "Thayer School grad" }))).toBe(true);
  });

  it("returns true when detail contains 'irving'", () => {
    expect(isDartmouth(makeContact({ detail: "Irving Institute fellow" }))).toBe(true);
  });

  it("returns true when detail contains 'big green'", () => {
    expect(isDartmouth(makeContact({ detail: "big green fan" }))).toBe(true);
  });

  it("returns false when dartmouth is false and detail has no keyword", () => {
    expect(isDartmouth(makeContact({ dartmouth: false, detail: "Acme CEO" }))).toBe(false);
  });

  it("returns false when detail is null", () => {
    expect(isDartmouth(makeContact({ dartmouth: false, detail: null }))).toBe(false);
  });
});

// ── deriveAction ───────────────────────────────────────────────────────────────

describe("deriveAction", () => {
  it.each([
    ["new", "outreach", "send_first_touch"],
    ["first_touch_drafted", "outreach", "send_first_touch"],
    ["first_touch_sent", "outreach", "send_first_touch"],
    ["followup1_drafted", "outreach", "send_followup1"],
    ["followup1_sent", "outreach", "send_followup1"],
    ["followup2_drafted", "outreach", "send_followup2"],
    ["followup2_sent", "outreach", "send_followup2"],
    ["breakup_drafted", "outreach", "send_breakup"],
    ["breakup_sent", "outreach", "send_breakup"],
    ["closed", "outreach", "send_first_touch"],     // fallback
    ["new", "applied", "send_applied_intro"],
    ["applied_intro_drafted", "applied", "send_applied_intro"],
    ["applied_intro_sent", "applied", "send_applied_intro"],
    ["applied_followup_drafted", "applied", "send_applied_followup"],
    ["applied_followup_sent", "applied", "send_applied_followup"],
    ["new", "networking", "send_networking_first_touch"],
    ["networking_drafted", "networking", "send_networking_first_touch"],
    ["networking_sent", "networking", "send_networking_first_touch"],
    ["networking_followup_drafted", "networking", "send_networking_followup"],
    ["networking_followup_sent", "networking", "send_networking_followup"],
  ] as const)("stage=%s mode=%s → %s", (stage, mode, expected) => {
    expect(deriveAction(makeContact({ stage, mode }))).toBe(expected);
  });
});

// ── assembleUserMessage ────────────────────────────────────────────────────────

describe("assembleUserMessage — outreach first touch (Tier 1, Dartmouth)", () => {
  const contact = makeContact({ tier: 1, dartmouth: true });
  const { userMessage, systemMessage } = assembleUserMessage(contact, "send_first_touch", {});

  it("systemMessage is the sender profile", () => {
    expect(systemMessage).toContain("Name: Kishore");
  });

  it("includes sender profile in user message body", () => {
    expect(userMessage).toContain("Name: Kishore");
  });

  it("includes contact name, company, role, detail", () => {
    expect(userMessage).toContain("Alice Chen");
    expect(userMessage).toContain("Acme Corp");
    expect(userMessage).toContain("VP Engineering");
    expect(userMessage).toContain("Built fintech compliance dashboards");
  });

  it("includes tier 1 instruction", () => {
    expect(userMessage).toContain("DREAM company. Deep personalization.");
  });

  it("includes cold_intro template and instruction", () => {
    expect(userMessage).toContain("cold_intro");
    expect(userMessage).toContain("First-touch cold email.");
  });

  it("includes dartmouth instruction", () => {
    expect(userMessage).toContain("ALUMNI CONNECTION DETECTED:");
  });

  it("has no unreplaced {placeholders}", () => {
    expect(userMessage).not.toMatch(/\{[a-z_]+\}/);
  });
});

describe("assembleUserMessage — outreach followup1 (Tier 2, non-Dartmouth)", () => {
  // Case 2: exercises follow_up_1 template; no dartmouth block
  const contact = makeContact({
    name: "Bob Martinez",
    company: "Bolt Inc",
    role: "VP Engineering",
    detail: "Scaling payments infra",
    tier: 2,
    dartmouth: false,
    stage: "first_touch_sent",
  });
  const { userMessage } = assembleUserMessage(contact, "send_followup1", {});

  it("includes tier 2 instruction", () => {
    expect(userMessage).toContain("STRONG FIT. Moderate personalization.");
  });

  it("includes follow_up_1 template", () => {
    expect(userMessage).toContain("follow_up_1");
    expect(userMessage).toContain("Gentle follow-up (day 5).");
  });

  it("does NOT include dartmouth instruction", () => {
    expect(userMessage).not.toContain("ALUMNI CONNECTION DETECTED");
  });

  it("includes contact fields", () => {
    expect(userMessage).toContain("Bob Martinez");
    expect(userMessage).toContain("Bolt Inc");
    expect(userMessage).toContain("Scaling payments infra");
  });

  it("has no unreplaced {placeholders}", () => {
    expect(userMessage).not.toMatch(/\{[a-z_]+\}/);
  });
});

describe("assembleUserMessage — applied intro (non-Dartmouth)", () => {
  // Case 3: exercises job_title, job_description, applied_date
  const contact = makeContact({
    name: "Carol Davis",
    company: "StartupXYZ",
    role: "CTO",
    tier: 2,
    mode: "applied",
    stage: "new",
    dartmouth: false,
    job_title: "Senior Product Manager",
    job_description: "We are looking for a PM to lead our core product.",
    applied_date: "2026-06-01",
  });
  const { userMessage } = assembleUserMessage(contact, "send_applied_intro", {});

  it("includes hiring manager info", () => {
    expect(userMessage).toContain("Carol Davis");
    expect(userMessage).toContain("StartupXYZ");
    expect(userMessage).toContain("CTO");
  });

  it("includes job_title", () => {
    expect(userMessage).toContain("Senior Product Manager");
  });

  it("includes job_description", () => {
    expect(userMessage).toContain("We are looking for a PM to lead our core product.");
  });

  it("includes applied_date", () => {
    expect(userMessage).toContain("2026-06-01");
  });

  it("does NOT include dartmouth instruction", () => {
    expect(userMessage).not.toContain("ALUMNI CONNECTION DETECTED");
  });

  it("has no unreplaced {placeholders}", () => {
    // {{...}} in the template are Python escaped literal braces — they become {score} etc. in output.
    // We only check for unescaped single-variable placeholders.
    expect(userMessage).not.toMatch(/(?<!\{)\{[a-z_]+\}(?!\})/);
  });
});

describe("assembleUserMessage — applied followup (Dartmouth via detail)", () => {
  // Case 4: exercises dartmouth detection via detail keyword; applied followup path
  const contact = makeContact({
    name: "Eve Johnson",
    company: "VC Firm",
    role: "Partner",
    detail: "Tuck alum, led Series A deals", // contains 'tuck' keyword
    tier: 1,
    mode: "applied",
    stage: "applied_followup_sent",
    dartmouth: false, // detected via detail keyword instead
    job_title: "Associate",
  });
  const { userMessage } = assembleUserMessage(contact, "send_applied_followup", {});

  it("includes hiring manager and job title", () => {
    expect(userMessage).toContain("Eve Johnson");
    expect(userMessage).toContain("VC Firm");
    expect(userMessage).toContain("Associate");
  });

  it("includes sender profile", () => {
    expect(userMessage).toContain("Name: Kishore");
  });

  it("has no unreplaced {placeholders}", () => {
    expect(userMessage).not.toMatch(/\{[a-z_]+\}/);
  });
});

describe("assembleUserMessage — networking first touch (with connection_context)", () => {
  const contact = makeContact({
    name: "Priya Nair",
    company: "Northwind",
    mode: "networking",
    stage: "new",
    connection_context: "We met at the Tuck info session last spring",
    dartmouth: false,
  });
  const { userMessage, systemMessage } = assembleUserMessage(
    contact,
    "send_networking_first_touch",
    {}
  );

  it("systemMessage is the sender profile", () => {
    expect(systemMessage).toContain("Name: Kishore");
  });

  it("leads with the connection_context hook", () => {
    expect(userMessage).toContain("We met at the Tuck info session last spring");
  });

  it("does not include the no-hook degrade instruction", () => {
    expect(userMessage).not.toContain("do not invent one");
  });

  it("includes contact name and company", () => {
    expect(userMessage).toContain("Priya Nair");
    expect(userMessage).toContain("Northwind");
  });

  it("has no unreplaced {placeholders}", () => {
    expect(userMessage).not.toMatch(/\{[a-z_]+\}/);
  });
});

describe("assembleUserMessage — networking first touch (no connection_context)", () => {
  const contact = makeContact({
    mode: "networking",
    stage: "new",
    connection_context: null,
    dartmouth: false,
  });
  const { userMessage } = assembleUserMessage(contact, "send_networking_first_touch", {});

  it("degrades to the no-hook instruction, never fabricating one", () => {
    expect(userMessage).toContain("do not invent one");
  });
});

describe("assembleUserMessage — networking followup", () => {
  const contact = makeContact({
    mode: "networking",
    stage: "networking_sent",
    dartmouth: true,
  });
  const { userMessage } = assembleUserMessage(contact, "send_networking_followup", {});

  it("includes dartmouth instruction when alumni", () => {
    expect(userMessage).toContain("ALUMNI CONNECTION DETECTED:");
  });

  it("has no unreplaced {placeholders}", () => {
    expect(userMessage).not.toMatch(/\{[a-z_]+\}/);
  });
});

// ── Prompt override: sandbox value replaces saved ──────────────────────────────

describe("assembleUserMessage — custom prompt override", () => {
  it("uses sandbox value for outreach_prompt when provided", () => {
    const contact = makeContact({ tier: 2, dartmouth: false });
    const customPrompt = "CUSTOM TEMPLATE: Write for {name} at {company}, tier {tier}.";
    const { userMessage } = assembleUserMessage(contact, "send_first_touch", {
      outreach_prompt: customPrompt,
    });
    expect(userMessage).toContain("CUSTOM TEMPLATE:");
    expect(userMessage).toContain("Alice Chen");
    expect(userMessage).toContain("Acme Corp");
    expect(userMessage).toContain("2"); // tier
    // Should NOT contain the default template text
    expect(userMessage).not.toContain("Generate ONLY the email body.");
  });

  it("uses custom tier_1_instruction when provided", () => {
    const contact = makeContact({ tier: 1, dartmouth: false });
    const { userMessage } = assembleUserMessage(contact, "send_first_touch", {
      tier_1_instruction: "CUSTOM TIER 1 INSTRUCTION",
    });
    expect(userMessage).toContain("CUSTOM TIER 1 INSTRUCTION");
    expect(userMessage).not.toContain("DREAM company.");
  });
});

// ── assembleCriticMessage ──────────────────────────────────────────────────────

describe("assembleCriticMessage", () => {
  const contact = makeContact({ dartmouth: true });
  const { userMessage, systemMessage } = assembleCriticMessage(
    contact,
    {},
    "Re: Quick question",
    "Hi Alice, I noticed you work on fintech. Would love to connect."
  );

  it("systemMessage is sender profile", () => {
    expect(systemMessage).toContain("Name: Kishore");
  });

  it("includes contact context fields", () => {
    expect(userMessage).toContain("Name: Alice Chen");
    expect(userMessage).toContain("Company: Acme Corp");
    expect(userMessage).toContain("Role: VP Engineering");
    expect(userMessage).toContain("Dartmouth: yes");
  });

  it("includes subject and body", () => {
    expect(userMessage).toContain("Re: Quick question");
    expect(userMessage).toContain("Hi Alice, I noticed you work on fintech.");
  });

  it("has no unreplaced single-brace {placeholders}", () => {
    // critic prompt has {{...}} escaped braces → they become {score} in output (literal)
    // We only flag unescaped {key} that should have been replaced
    expect(userMessage).not.toMatch(/\{sender_profile\}/);
    expect(userMessage).not.toMatch(/\{contact_context\}/);
    expect(userMessage).not.toMatch(/\{subject\}/);
    expect(userMessage).not.toMatch(/\{body\}/);
  });
});
