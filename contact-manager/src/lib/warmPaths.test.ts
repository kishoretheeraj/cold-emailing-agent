import fs from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";
import {
  companyCap,
  companyKey,
  companySearchWord,
  contactFromApplication,
  guessEmail,
  holdDays,
  hookSuggestion,
  linkBlocker,
  modeFor,
  parsePerson,
  postingEmails,
  RELATIONSHIPS,
  searchLinks,
  type ContactBrief,
} from "./warmPaths";

const APP = {
  id: 7,
  company: "Acme, Inc.",
  role: "Associate Product Manager",
  job_url: "https://boards.greenhouse.io/acme/jobs/1",
  stage: "ready_to_submit",
  posting_snapshot: { description: "Own the lending roadmap. " + "x".repeat(2000) },
};

function person(over: Record<string, unknown> = {}) {
  const parsed = parsePerson({ name: "Jane Doe", email: "Jane.Doe@Acme.com", relationship: "hiring_manager", ...over });
  if (!parsed.ok) throw new Error(parsed.error);
  return parsed.person;
}

describe("companyKey mirrors job_identity.company_key", () => {
  const rows = JSON.parse(
    fs.readFileSync(path.join(__dirname, "../../../tests/fixtures/company_keys.json"), "utf-8"),
  ) as [string, string][];

  it.each(rows)("%j -> %j", (name, key) => {
    expect(companyKey(name)).toBe(key);
  });

  it("picks the most distinctive word to pre-filter on", () => {
    expect(companySearchWord("The Goldman Sachs Group, Inc.")).toBe("goldman");
    expect(companySearchWord("Inc")).toBe("");
  });
});

describe("mode and hooks", () => {
  it.each([
    ["hiring_manager", "applied"], ["leader", "applied"], ["recruiter", "applied"],
    ["alum", "networking"], ["team_member", "networking"], ["other", "networking"],
  ] as const)("%s -> %s", (rel, mode) => {
    expect(modeFor(rel)).toBe(mode);
  });

  it.each(RELATIONSHIPS)("a %s hook never names the role", (rel) => {
    const hook = hookSuggestion(rel, { company: "Acme", school: "dartmouth", title: "Product Manager" });
    expect(hook).not.toMatch(/associate|apply|applied|opening|role|position|hiring/i);
  });

  it("suggests the shared school and the person's work", () => {
    expect(hookSuggestion("alum", { company: "Acme", school: "tuck" })).toBe("Fellow Dartmouth Tuck alum");
    expect(hookSuggestion("team_member", { company: "Acme", title: "Product Manager" })).toBe("Works as Product Manager at Acme");
    expect(hookSuggestion("team_member", { company: "Acme" })).toBe("Works at Acme");
    expect(hookSuggestion("hiring_manager", { company: "Acme" })).toBe("");
  });
});

describe("parsePerson", () => {
  it.each([
    [{}, "required"],
    [{ name: "A", email: "a@b.co", relationship: "boss" }, "required"],
    [{ name: "A", email: "not-an-email", relationship: "other" }, "valid"],
    [{ name: "A", email: "a@b.co", relationship: "other", tier: 4 }, "tier"],
    [{ name: "A", email: "a@b.co", relationship: "alum" }, "school"],
    [{ name: "A", email: "a@b.co", relationship: "other", linkedin: "http://evil.example/in/a" }, "LinkedIn"],
  ])("refuses %j", (raw, message) => {
    const parsed = parsePerson(raw);
    expect(parsed.ok).toBe(false);
    if (!parsed.ok) expect(parsed.error).toContain(message);
  });

  it("normalizes the email and defaults the tier to 2", () => {
    const p = person();
    expect(p.email).toBe("jane.doe@acme.com");
    expect(p.tier).toBe(2);
  });
});

describe("contactFromApplication", () => {
  it("builds an applied-mode row with the job fields persisted", () => {
    const row = contactFromApplication(APP, person({ title: "Director of Product", linkedin: "https://www.linkedin.com/in/jane" }));
    expect(row).toMatchObject({
      name: "Jane Doe", email: "jane.doe@acme.com", company: "Acme, Inc.", role: "Director of Product",
      mode: "applied", stage: "new", reply_status: "no_reply", relationship: "hiring_manager",
      job_application_id: 7, job_title: "Associate Product Manager", tier: 2, dartmouth: false,
      notes: "LinkedIn: https://www.linkedin.com/in/jane",
    });
    expect((row.job_description as string).length).toBe(1500);
    expect(row).not.toHaveProperty("applied_date");
    expect(row).not.toHaveProperty("connection_context");
  });

  it("builds a networking row with only the hook the user typed", () => {
    const row = contactFromApplication(APP, person({ relationship: "alum", school: "thayer", connection_context: "Fellow Dartmouth Thayer alum" }));
    expect(row).toMatchObject({ mode: "networking", dartmouth: true, connection_context: "Fellow Dartmouth Thayer alum" });
    expect(row).not.toHaveProperty("job_title");
    const blank = contactFromApplication(APP, person({ relationship: "other" }));
    expect(blank.connection_context).toBeNull();
  });

  it("only Dartmouth schools set the dartmouth flag", () => {
    expect(contactFromApplication(APP, person({ relationship: "alum", school: "anna" })).dartmouth).toBe(false);
  });
});

describe("finding people", () => {
  it("builds search links without automating anything", () => {
    const links = searchLinks(APP);
    expect(links).toHaveLength(6);
    expect(links[0].url).toBe("https://www.linkedin.com/search/results/people/?keywords=Acme%2C%20Inc.%20Dartmouth");
    expect(links.every((l) => l.url.startsWith("https://www.linkedin.com/") || l.url.startsWith("https://www.google.com/"))).toBe(true);
    expect(links.map((l) => l.label)).toContain("LinkedIn: product team");
  });

  it("finds emails in the posting and tells inboxes from people", () => {
    expect(postingEmails({
      description: "Questions? Write to careers@acme.com or jane.doe@acme.com.",
      qualifications: ["Reach recruiting@acme.com", "noreply@acme.com"],
      other: 5,
    })).toEqual([
      { email: "careers@acme.com", kind: "inbox" },
      { email: "jane.doe@acme.com", kind: "person" },
      { email: "noreply@acme.com", kind: "inbox" },
      { email: "recruiting@acme.com", kind: "inbox" },
    ]);
    expect(postingEmails(null)).toEqual([]);
  });

  it("guesses an email only when two known addresses agree", () => {
    const one = [{ name: "Ann Lee", email: "ann.lee@acme.com" }];
    expect(guessEmail("Jane Doe", one)).toBeNull();
    const two = [...one, { name: "Bo Chan", email: "bo.chan@acme.com" }, { name: "X", email: "x@acme.com" }];
    expect(guessEmail("Jane Q. Doe", two)).toEqual({ email: "jane.doe@acme.com", pattern: "first.last", basis: 2 });
    expect(guessEmail("Prince", two)).toBeNull();
  });

  it("refuses a tie between patterns", () => {
    const known = [
      { name: "Ann Lee", email: "ann.lee@acme.com" }, { name: "Bo Chan", email: "bo.chan@acme.com" },
      { name: "Cy Dee", email: "cdee@acme.com" }, { name: "Di Eve", email: "deve@acme.com" },
    ];
    expect(guessEmail("Jane Doe", known)).toBeNull();
  });
});

describe("link rules mirror the trigger", () => {
  const base: ContactBrief = {
    id: 1, name: "A", email: "a@acme.com", company: "Acme", mode: "networking",
    stage: "new", reply_status: "no_reply", job_application_id: null,
  };
  it.each([
    [{}, null],
    [{ mode: "applied" }, null],
    [{ mode: "outreach" }, "Outreach"],
    [{ stage: "networking_sent" }, "in touch"],
    [{ reply_status: "interested" }, "in touch"],
    [{ job_application_id: 9 }, "Already linked"],
  ])("%j", (over, expected) => {
    const blocker = linkBlocker({ ...base, ...over });
    if (expected === null) expect(blocker).toBeNull();
    else expect(blocker).toContain(expected);
  });
});

describe("preferences", () => {
  it.each([
    [undefined, 10], ["not json", 10], [JSON.stringify({ referral_hold_days: 3 }), 3],
    [JSON.stringify({ referral_hold_days: 99 }), 14], [JSON.stringify({ referral_hold_days: 0 }), 1],
  ])("hold days from %j", (raw, days) => {
    expect(holdDays(raw)).toBe(days);
  });

  it.each([
    [undefined, 5], [JSON.stringify({ outreach_per_company_30d: 2 }), 2], [JSON.stringify({ outreach_per_company_30d: -1 }), 5],
  ])("company cap from %j", (raw, cap) => {
    expect(companyCap(raw)).toBe(cap);
  });
});
