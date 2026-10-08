import { describe, it, expect, vi, beforeEach } from "vitest";
import { GET, POST } from "./route";

const mockOrder = vi.fn();
const mockEq = vi.fn();
const mockSelect = vi.fn();
const mockSingle = vi.fn();
const mockInsertSelect = vi.fn();
const mockInsert = vi.fn();
const mockFrom = vi.fn();

const mockLinked = vi.fn();
const mockKnown = vi.fn();
vi.mock("@/lib/warmPathsData", () => ({
  linkedCounts: (...a: unknown[]) => mockLinked(...a),
  knownPeopleByCompany: (...a: unknown[]) => mockKnown(...a),
}));

vi.mock("@supabase/supabase-js", () => ({
  createClient: vi.fn(() => ({
    from: mockFrom,
  })),
}));

beforeEach(() => {
  vi.clearAllMocks();
  mockOrder.mockResolvedValue({ data: [{ id: "1", company: "Acme" }], error: null });
  mockEq.mockReturnValue({ order: mockOrder, eq: mockEq });
  mockSelect.mockReturnValue({ order: mockOrder, eq: mockEq });
  mockSingle.mockResolvedValue({ data: { id: "1", company: "Acme", role: "PM" }, error: null });
  mockInsertSelect.mockReturnValue({ single: mockSingle });
  mockInsert.mockReturnValue({ select: mockInsertSelect });
  mockFrom.mockReturnValue({ select: mockSelect, insert: mockInsert });
  mockLinked.mockResolvedValue(new Map());
  mockKnown.mockResolvedValue(new Map());
});

describe("GET /api/applications?view=queue", () => {
  it("returns the approval queue's statuses and drops skipped or closed rows", async () => {
    const mockOr = vi.fn().mockReturnValue({ order: mockOrder });
    const mockIn = vi.fn().mockReturnValue({ order: mockOrder, eq: mockEq, or: mockOr });
    const mockNot = vi.fn().mockReturnValue({ in: mockIn, order: mockOrder });
    mockSelect.mockReturnValue({ order: mockOrder, eq: mockEq, not: mockNot, in: mockIn });
    await GET(new Request("http://test/api/applications?view=queue"));
    expect(mockNot).toHaveBeenCalledWith("stage", "in", "(withdrawn,rejected)");
    expect(mockIn).toHaveBeenCalledWith("automation_status", [
      "ready_for_review", "approved", "submitting", "needs_input", "needs_confirmation", "submitted",
    ]);
    // Only the last two weeks of submitted rows, so the list stays bounded at fifty a day.
    const filter = mockOr.mock.calls[0][0] as string;
    expect(filter.startsWith("automation_status.neq.submitted,updated_at.gte.")).toBe(true);
    const since = Date.parse(filter.split("updated_at.gte.")[1]);
    expect(Math.round((Date.now() - since) / 86_400_000)).toBe(14);
  });
});

describe("GET /api/applications?view=queue -- people counts (warm paths)", () => {
  function queueChain() {
    const mockOr = vi.fn().mockReturnValue({ order: mockOrder });
    const mockIn = vi.fn().mockReturnValue({ or: mockOr });
    const mockNot = vi.fn().mockReturnValue({ in: mockIn });
    mockSelect.mockReturnValue({ not: mockNot });
  }

  it("adds linked and known counts to each card", async () => {
    queueChain();
    mockOrder.mockResolvedValue({ data: [{ id: "7", company: "Acme, Inc." }, { id: "8", company: "Beta" }], error: null });
    mockLinked.mockResolvedValue(new Map([[7, 2]]));
    mockKnown.mockResolvedValue(new Map([["acme", 4]]));
    const body = await (await GET(new Request("http://test/api/applications?view=queue"))).json();
    expect(mockLinked.mock.calls[0][1]).toEqual([7, 8]);
    expect(body.applications.map((a: { people: unknown }) => a.people)).toEqual([
      { linked: 2, known: 4 }, { linked: 0, known: 0 },
    ]);
  });

  it("still returns the queue when the counts fail", async () => {
    queueChain();
    mockKnown.mockRejectedValue(new Error("down"));
    const res = await GET(new Request("http://test/api/applications?view=queue"));
    expect(res.status).toBe(200);
    expect((await res.json()).applications).toEqual([{ id: "1", company: "Acme" }]);
  });

  it("other views never pay for the counts", async () => {
    await GET(new Request("http://test/api/applications"));
    expect(mockKnown).not.toHaveBeenCalled();
  });
});

describe("GET /api/applications?takeover=open", () => {
  it("returns only rows whose current worker is waiting for a human", async () => {
    const waiting = { id: "1", worker_lease_id: "L", takeover: { kind: "captcha", reason: "x", lease: "L", requested_at: "t", continue_at: null } };
    const stale = { id: "2", worker_lease_id: "M", takeover: { kind: "captcha", reason: "x", lease: "L", requested_at: "t", continue_at: null } };
    const answered = { id: "3", worker_lease_id: "L", takeover: { kind: "captcha", reason: "x", lease: "L", requested_at: "t", continue_at: "t2" } };
    const mockNot = vi.fn().mockReturnValue({ order: mockOrder, eq: mockEq });
    mockSelect.mockReturnValue({ order: mockOrder, eq: mockEq, not: mockNot });
    mockOrder.mockResolvedValue({ data: [waiting, stale, answered], error: null });
    const res = await GET(new Request("http://test/api/applications?takeover=open"));
    expect(mockNot).toHaveBeenCalledWith("takeover", "is", null);
    expect((await res.json()).applications).toEqual([waiting]);
  });
});

describe("GET /api/applications", () => {
  it("returns all applications", async () => {
    const res = await GET(new Request("http://test/api/applications"));
    const body = await res.json();
    expect(body.applications).toEqual([{ id: "1", company: "Acme" }]);
  });

  it("filters by stage query param", async () => {
    await GET(new Request("http://test/api/applications?stage=applied"));
    expect(mockEq).toHaveBeenCalledWith("stage", "applied");
  });

  it("filters by source query param", async () => {
    await GET(new Request("http://test/api/applications?source=linkedin"));
    expect(mockEq).toHaveBeenCalledWith("source", "linkedin");
  });

  it("filters by both stage and source query params together (M4)", async () => {
    await GET(new Request("http://test/api/applications?stage=applied&source=linkedin"));
    expect(mockEq).toHaveBeenCalledWith("stage", "applied");
    expect(mockEq).toHaveBeenCalledWith("source", "linkedin");
  });

  it("returns 500 on supabase error", async () => {
    mockOrder.mockResolvedValue({ data: null, error: new Error("db down") });
    const res = await GET(new Request("http://test/api/applications"));
    expect(res.status).toBe(500);
  });
});

describe("POST /api/applications", () => {
  it("creates an application with company and role", async () => {
    const req = new Request("http://test/api/applications", {
      method: "POST",
      body: JSON.stringify({ company: "Acme", role: "PM" }),
    });
    const res = await POST(req);
    expect(res.status).toBe(201);
    const body = await res.json();
    expect(body.application.company).toBe("Acme");
  });

  it("returns 400 when company is missing", async () => {
    const req = new Request("http://test/api/applications", {
      method: "POST",
      body: JSON.stringify({ role: "PM" }),
    });
    const res = await POST(req);
    expect(res.status).toBe(400);
  });

  it("returns 400 when role is missing", async () => {
    const req = new Request("http://test/api/applications", {
      method: "POST",
      body: JSON.stringify({ company: "Acme" }),
    });
    const res = await POST(req);
    expect(res.status).toBe(400);
  });

  it("returns 400 for invalid JSON", async () => {
    const req = new Request("http://test/api/applications", { method: "POST", body: "not json" });
    const res = await POST(req);
    expect(res.status).toBe(400);
  });

  it("returns 500 on supabase insert error", async () => {
    mockSingle.mockResolvedValue({ data: null, error: new Error("insert failed") });
    const req = new Request("http://test/api/applications", {
      method: "POST",
      body: JSON.stringify({ company: "Acme", role: "PM" }),
    });
    const res = await POST(req);
    expect(res.status).toBe(500);
  });
});
