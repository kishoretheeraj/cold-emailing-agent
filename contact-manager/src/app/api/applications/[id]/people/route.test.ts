import { beforeEach, describe, expect, it, vi } from "vitest";
import { GET, POST } from "./route";
import { POST as LINK } from "./link/route";
import { POST as UNLINK } from "./unlink/route";
import * as data from "@/lib/warmPathsData";

vi.mock("@supabase/supabase-js", () => ({ createClient: vi.fn(() => ({})) }));
vi.mock("@/lib/warmPathsData", () => ({
  loadApplication: vi.fn(),
  loadLinked: vi.fn(),
  loadCompanyContacts: vi.fn(),
  loadPreferences: vi.fn(),
  findByEmail: vi.fn(),
  insertContact: vi.fn(),
  linkContact: vi.fn(),
  unlinkContact: vi.fn(),
}));

const m = vi.mocked(data);
const APP = {
  id: 7, company: "Acme", role: "Associate Product Manager", job_url: "https://x", stage: "ready_to_submit",
  automation_status: "ready_for_review", referral_hold_until: null,
  posting_snapshot: { description: "Questions: jane@acme.com" },
};
const params = (id = "7") => ({ params: Promise.resolve({ id }) });
const post = (body: unknown) => new Request("http://x", { method: "POST", body: JSON.stringify(body) });
const contact = (over: Record<string, unknown> = {}) => ({
  id: 1, name: "Ann Lee", email: "ann.lee@acme.com", company: "Acme Inc", mode: "networking", stage: "new",
  reply_status: "no_reply", job_application_id: null, relationship: null, created_at: new Date().toISOString(), ...over,
});
const NEW_PERSON = { name: "Jane Doe", email: "jane@acme.com", relationship: "hiring_manager", title: "Director" };

beforeEach(() => {
  vi.clearAllMocks();
  m.loadApplication.mockResolvedValue(APP);
  m.loadLinked.mockResolvedValue([]);
  m.loadCompanyContacts.mockResolvedValue([]);
  m.loadPreferences.mockResolvedValue(null);
  m.findByEmail.mockResolvedValue(null);
  m.insertContact.mockImplementation(async (_db, row) => ({ ok: true, contact: { id: 99, ...row } as never }));
});

describe("GET people", () => {
  it("rejects a non-numeric id", async () => {
    expect((await GET(new Request("http://x"), params("7 or 1=1"))).status).toBe(400);
  });

  it("404s an unknown application", async () => {
    m.loadApplication.mockResolvedValue(null);
    expect((await GET(new Request("http://x"), params())).status).toBe(404);
  });

  it("returns linked, known people with their blockers, links, posting emails and caps", async () => {
    m.loadLinked.mockResolvedValue([contact({ id: 5, job_application_id: 7, relationship: "alum" })] as never);
    m.loadCompanyContacts.mockResolvedValue([
      contact({ id: 5, job_application_id: 7, relationship: "alum" }),
      contact({ id: 6, stage: "networking_sent" }),
      contact({ id: 8, mode: "applied" }),
    ] as never);
    const body = await (await GET(new Request("http://x"), params())).json();
    expect(body.linked.map((c: { id: number }) => c.id)).toEqual([5]);
    expect(body.known.map((c: { id: number; blocker: string | null }) => [c.id, c.blocker])).toEqual([
      [6, "Already in touch"], [8, null],
    ]);
    expect(body.remaining).toBe(2);
    expect(body.company_recent).toBe(1);
    expect(body.company_cap).toBe(5);
    expect(body.posting_emails).toEqual([{ email: "jane@acme.com", kind: "person" }]);
    expect(body.search_links).toHaveLength(6);
    expect(body.known_emails).toHaveLength(3);
  });

  it("500s when a read fails", async () => {
    m.loadLinked.mockRejectedValue(new Error("down"));
    expect((await GET(new Request("http://x"), params())).status).toBe(500);
  });
});

describe("POST people", () => {
  it("creates an applied-mode contact linked to the application", async () => {
    const res = await POST(post(NEW_PERSON), params());
    expect(res.status).toBe(201);
    const row = m.insertContact.mock.calls[0][1];
    expect(row).toMatchObject({ mode: "applied", job_application_id: 7, job_title: "Associate Product Manager",
      role: "Director", relationship: "hiring_manager", stage: "new" });
  });

  it.each([
    [{ name: "x" }, 400],
    ["not json at all", 400],
  ])("400s bad input %j", async (body, status) => {
    const req = typeof body === "string" ? new Request("http://x", { method: "POST", body }) : post(body);
    expect((await POST(req, params())).status).toBe(status);
    expect(m.insertContact).not.toHaveBeenCalled();
  });

  it.each(["rejected", "withdrawn"])("refuses a %s application", async (stage) => {
    m.loadApplication.mockResolvedValue({ ...APP, stage });
    expect((await POST(post(NEW_PERSON), params())).status).toBe(409);
    expect(m.insertContact).not.toHaveBeenCalled();
  });

  it("refuses a duplicate and says whether it can be linked instead", async () => {
    m.findByEmail.mockResolvedValue({ ...contact({ id: 3, name: "Jane Doe" }), deleted_at: null } as never);
    const res = await POST(post(NEW_PERSON), params());
    expect(res.status).toBe(409);
    expect((await res.json()).existing).toEqual({ id: 3, name: "Jane Doe", blocker: null });
  });

  it("refuses a soft-deleted duplicate with the restore message", async () => {
    m.findByEmail.mockResolvedValue({ ...contact({ name: "Jane Doe" }), deleted_at: "2026-10-01" } as never);
    const res = await POST(post(NEW_PERSON), params());
    expect(res.status).toBe(409);
    expect((await res.json()).error).toContain("previously deleted");
    expect(m.insertContact).not.toHaveBeenCalled();
  });

  it("refuses a fourth person", async () => {
    m.loadLinked.mockResolvedValue([contact(), contact(), contact()] as never);
    expect((await POST(post(NEW_PERSON), params())).status).toBe(409);
  });

  it("refuses past the per-company cap", async () => {
    m.loadPreferences.mockResolvedValue(JSON.stringify({ outreach_per_company_30d: 2 }));
    m.loadCompanyContacts.mockResolvedValue([contact({ relationship: "alum" }), contact({ relationship: "other" })] as never);
    const res = await POST(post(NEW_PERSON), params());
    expect(res.status).toBe(409);
    expect((await res.json()).error).toContain("2 people at Acme");
  });

  it("people added over 30 days ago do not count toward the cap", async () => {
    m.loadPreferences.mockResolvedValue(JSON.stringify({ outreach_per_company_30d: 1 }));
    m.loadCompanyContacts.mockResolvedValue([contact({ relationship: "alum", created_at: "2026-01-01T00:00:00Z" })] as never);
    expect((await POST(post(NEW_PERSON), params())).status).toBe(201);
  });

  it("maps a trigger refusal to 409 and other write errors to 500", async () => {
    m.insertContact.mockResolvedValue({ ok: false, conflict: true, error: "application 7 already has 3 people" });
    expect((await POST(post(NEW_PERSON), params())).status).toBe(409);
    m.insertContact.mockResolvedValue({ ok: false, conflict: false, error: "boom" });
    expect((await POST(post(NEW_PERSON), params())).status).toBe(500);
  });
});

describe("link and unlink", () => {
  it.each([[{}], [{ contact_id: "1; drop" }], [{ contact_id: -1 }]])("400s %j", async (body) => {
    expect((await LINK(post(body), params())).status).toBe(400);
    expect((await UNLINK(post(body), params())).status).toBe(400);
  });

  it("links an unlinked contact", async () => {
    m.linkContact.mockResolvedValue({ ok: true, contact: contact({ job_application_id: 7 }) as never });
    const res = await LINK(post({ contact_id: 1 }), params());
    expect(res.status).toBe(200);
    expect(m.linkContact.mock.calls[0].slice(1)).toEqual([1, 7]);
  });

  it("409s a contact that is gone, already linked, or refused by the trigger", async () => {
    m.linkContact.mockResolvedValue(null);
    expect((await LINK(post({ contact_id: 1 }), params())).status).toBe(409);
    m.linkContact.mockResolvedValue({ ok: false, conflict: true, error: "only a contact not yet emailed can be linked" });
    const res = await LINK(post({ contact_id: 1 }), params());
    expect(res.status).toBe(409);
    expect((await res.json()).error).toContain("not yet emailed");
  });

  it("unlinks only a contact linked to this application", async () => {
    m.unlinkContact.mockResolvedValue(true);
    expect((await UNLINK(post({ contact_id: "1" }), params())).status).toBe(200);
    expect(m.unlinkContact.mock.calls[0].slice(1)).toEqual([1, 7]);
    m.unlinkContact.mockResolvedValue(false);
    expect((await UNLINK(post({ contact_id: "1" }), params())).status).toBe(409);
  });
});
