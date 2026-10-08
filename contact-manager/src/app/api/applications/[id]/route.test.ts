import { describe, it, expect, vi, beforeEach } from "vitest";
import { GET, PATCH } from "./route";

const mockSingle = vi.fn();
const mockSelect = vi.fn();
const mockIs = vi.fn();
const mockEq = vi.fn();
const mockUpdate = vi.fn();
const mockFrom = vi.fn();
// GET's chain (from -> select -> eq -> single) is shaped differently from PATCH's
// (from -> update -> eq -> select -> single), so it needs its own select/eq mocks rather
// than reusing mockSelect/mockEq above -- both terminate at the same shared mockSingle.
// finding-1 fix: the apply_preview PATCH path additionally chains .is("approved_at", null)
// between .eq() and .select() (from -> update -> eq -> is -> select -> single), and its
// zero-rows disambiguation reads via from -> select -> eq -> maybeSingle (mockGetEq also
// needs a maybeSingle branch for that).
const mockGetSelect = vi.fn();
const mockGetEq = vi.fn();
const mockMaybeSingle = vi.fn();

vi.mock("@supabase/supabase-js", () => ({
  createClient: vi.fn(() => ({ from: mockFrom })),
}));

function params(id: string) {
  return { params: Promise.resolve({ id }) };
}

beforeEach(() => {
  vi.clearAllMocks();
  mockSingle.mockResolvedValue({ data: { id: "1", stage: "onsite" }, error: null });
  mockSelect.mockReturnValue({ single: mockSingle });
  mockIs.mockReturnValue({ select: mockSelect, is: mockIs });
  mockEq.mockReturnValue({ select: mockSelect, is: mockIs });
  mockUpdate.mockReturnValue({ eq: mockEq });
  mockMaybeSingle.mockResolvedValue({ data: { id: "1" }, error: null });
  mockGetEq.mockReturnValue({ single: mockSingle, maybeSingle: mockMaybeSingle });
  mockGetSelect.mockReturnValue({ eq: mockGetEq });
  mockFrom.mockReturnValue({ update: mockUpdate, select: mockGetSelect });
});

describe("PATCH /api/applications/[id]", () => {
  it("updates stage", async () => {
    const req = new Request("http://test", { method: "PATCH", body: JSON.stringify({ stage: "onsite" }) });
    const res = await PATCH(req, params("1"));
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.application.stage).toBe("onsite");
  });

  it("rejects an invalid stage", async () => {
    const req = new Request("http://test", { method: "PATCH", body: JSON.stringify({ stage: "not_a_stage" }) });
    const res = await PATCH(req, params("1"));
    expect(res.status).toBe(400);
  });

  it("returns 400 for invalid JSON", async () => {
    const req = new Request("http://test", { method: "PATCH", body: "not json" });
    const res = await PATCH(req, params("1"));
    expect(res.status).toBe(400);
  });

  it("returns 400 when no valid fields given", async () => {
    const req = new Request("http://test", { method: "PATCH", body: JSON.stringify({}) });
    const res = await PATCH(req, params("1"));
    expect(res.status).toBe(400);
  });

  it("returns 500 on supabase error", async () => {
    mockSingle.mockResolvedValue({ data: null, error: new Error("update failed") });
    const req = new Request("http://test", { method: "PATCH", body: JSON.stringify({ stage: "onsite" }) });
    const res = await PATCH(req, params("1"));
    expect(res.status).toBe(500);
  });

  it("rejects a non-numeric id (M9 -- matches GET/submit/reset-approval/files routes)", async () => {
    const req = new Request("http://test", { method: "PATCH", body: JSON.stringify({ stage: "onsite" }) });
    const res = await PATCH(req, params("abc"));
    expect(res.status).toBe(400);
    expect(mockFrom).not.toHaveBeenCalled();
  });

  it("accepts ready_to_submit as a valid stage (regression test for U10)", async () => {
    mockSingle.mockResolvedValue({ data: { id: "1", stage: "ready_to_submit" }, error: null });
    const req = new Request("http://test", {
      method: "PATCH",
      body: JSON.stringify({ stage: "ready_to_submit" }),
    });
    const res = await PATCH(req, params("1"));
    expect(res.status).toBe(200);
  });
});

describe("PATCH /api/applications/[id] -- apply_preview (U11)", () => {
  const validPreview = {
    platform: "ashby",
    field_values: { name: "Kishore" },
    eligibility_answers: { "Authorized to work in the US?": "Yes" },
    screening_answers: { "Why this role?": "Because of the mission." },
  };

  it("updates apply_preview when given a valid object", async () => {
    mockSingle.mockResolvedValue({ data: { id: "1", apply_preview: validPreview }, error: null });
    const req = new Request("http://test", {
      method: "PATCH",
      body: JSON.stringify({ apply_preview: validPreview }),
    });
    const res = await PATCH(req, params("1"));
    expect(res.status).toBe(200);
    expect(mockUpdate).toHaveBeenCalledWith(expect.objectContaining({ apply_preview: validPreview }));
    // finding 1: apply_preview writes must be gated atomically on approved_at IS NULL, not
    // checked-then-acted -- the WHERE clause itself is the guard.
    expect(mockEq).toHaveBeenCalledWith("id", 1);
    expect(mockIs).toHaveBeenCalledWith("approved_at", null);
  });

  // Merge review 2026-09-28, finding 1: after Approve & Submit sets approved_at, the PATCH
  // route used to accept an apply_preview write with no check on approved_at at all -- an edit
  // during the GitHub Actions workflow's dependency-install window would change the
  // screening/eligibility answers sent under an approval granted for a different preview. The
  // conditional update's WHERE clause (id = X AND approved_at IS NULL) now rejects this
  // atomically: zero rows match, PostgREST's .single() surfaces PGRST116, and the route
  // disambiguates that into a 409 (row exists but is approved) rather than a generic 500.
  it("rejects an apply_preview edit once the row is approved (409, not the generic 500 path)", async () => {
    mockSingle.mockResolvedValue({ data: null, error: { code: "PGRST116", message: "no rows" } });
    mockMaybeSingle.mockResolvedValue({ data: { id: "1", approved_at: "2026-10-01T00:00:00Z", worker_lease_id: null }, error: null });
    const req = new Request("http://test", {
      method: "PATCH",
      body: JSON.stringify({ apply_preview: validPreview }),
    });
    const res = await PATCH(req, params("1"));
    expect(res.status).toBe(409);
    const body = await res.json();
    expect(body.error).toMatch(/already been approved/i);
  });

  it("returns 404, not 409, when the guarded update matches zero rows because the id doesn't exist", async () => {
    mockSingle.mockResolvedValue({ data: null, error: { code: "PGRST116", message: "no rows" } });
    mockMaybeSingle.mockResolvedValue({ data: null, error: null });
    const req = new Request("http://test", {
      method: "PATCH",
      body: JSON.stringify({ apply_preview: validPreview }),
    });
    const res = await PATCH(req, params("999"));
    expect(res.status).toBe(404);
  });

  it("does not gate a stage-only edit on approved_at (e.g. marking an approved row withdrawn), only on no worker lease", async () => {
    mockSingle.mockResolvedValue({ data: { id: "1", stage: "withdrawn" }, error: null });
    const req = new Request("http://test", {
      method: "PATCH",
      body: JSON.stringify({ stage: "withdrawn" }),
    });
    const res = await PATCH(req, params("1"));
    expect(res.status).toBe(200);
    expect(mockIs).toHaveBeenCalledWith("worker_lease_id", null);
    expect(mockIs).not.toHaveBeenCalledWith("approved_at", null);
  });

  it("apply_preview edit is conditional on both approved_at and worker_lease_id being NULL", async () => {
    const req = new Request("http://test", {
      method: "PATCH",
      body: JSON.stringify({ apply_preview: validPreview }),
    });
    await PATCH(req, params("1"));
    expect(mockIs).toHaveBeenCalledWith("approved_at", null);
    expect(mockIs).toHaveBeenCalledWith("worker_lease_id", null);
  });

  it("returns 409 'worker is currently processing' when a worker lease blocks a stage edit", async () => {
    mockSingle.mockResolvedValue({ data: null, error: { code: "PGRST116", message: "no rows" } });
    mockMaybeSingle.mockResolvedValue({ data: { id: "1", approved_at: null, worker_lease_id: "lease-1" }, error: null });
    const req = new Request("http://test", { method: "PATCH", body: JSON.stringify({ stage: "withdrawn" }) });
    const res = await PATCH(req, params("1"));
    expect(res.status).toBe(409);
    expect((await res.json()).error).toMatch(/worker is currently processing/i);
  });

  it("returns 404 when a stage edit matches zero rows and the row is missing", async () => {
    mockSingle.mockResolvedValue({ data: null, error: { code: "PGRST116", message: "no rows" } });
    mockMaybeSingle.mockResolvedValue({ data: null, error: null });
    const req = new Request("http://test", { method: "PATCH", body: JSON.stringify({ stage: "withdrawn" }) });
    const res = await PATCH(req, params("999"));
    expect(res.status).toBe(404);
  });

  it("a notes-only PATCH stays unconditional", async () => {
    const req = new Request("http://test", { method: "PATCH", body: JSON.stringify({ notes: "hi" }) });
    const res = await PATCH(req, params("1"));
    expect(res.status).toBe(200);
    expect(mockIs).not.toHaveBeenCalled();
  });

  it("rejects apply_preview that isn't a well-formed object", async () => {
    const req = new Request("http://test", {
      method: "PATCH",
      body: JSON.stringify({ apply_preview: "nope" }),
    });
    const res = await PATCH(req, params("1"));
    expect(res.status).toBe(400);
    const body = await res.json();
    expect(body.error).toContain("apply_preview must be");
  });

  it("rejects apply_preview missing a required key", async () => {
    const req = new Request("http://test", {
      method: "PATCH",
      body: JSON.stringify({ apply_preview: { platform: "ashby" } }),
    });
    const res = await PATCH(req, params("1"));
    expect(res.status).toBe(400);
    const body = await res.json();
    expect(body.error).toContain("apply_preview must be");
  });
});

describe("GET /api/applications/[id] (I8 -- single-row fetch for status polling)", () => {
  it("returns the row for a valid id", async () => {
    mockSingle.mockResolvedValue({ data: { id: "5", stage: "applied", apply_blocked_reason: null }, error: null });
    const res = await GET(new Request("http://test"), params("5"));
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.application).toEqual({ id: "5", stage: "applied", apply_blocked_reason: null });
  });

  it("rejects a non-numeric id", async () => {
    const res = await GET(new Request("http://test"), params("abc"));
    expect(res.status).toBe(400);
  });

  it("returns 500 on a supabase read error", async () => {
    mockSingle.mockResolvedValue({ data: null, error: new Error("db down") });
    const res = await GET(new Request("http://test"), params("5"));
    expect(res.status).toBe(500);
  });
});

describe("PATCH /api/applications/[id] -- applied_date when marked applied (warm paths)", () => {
  const mockDated = vi.fn();
  beforeEach(() => {
    mockSelect.mockReturnValue({ single: mockSingle, maybeSingle: mockDated });
  });

  it("records today's New York date when a row is marked applied without one", async () => {
    mockSingle.mockResolvedValue({ data: { id: "1", stage: "applied", applied_date: null }, error: null });
    mockDated.mockResolvedValue({ data: { id: "1", stage: "applied", applied_date: "2026-10-09" }, error: null });
    const req = new Request("http://test", { method: "PATCH", body: JSON.stringify({ stage: "applied" }) });
    const body = await (await PATCH(req, params("1"))).json();
    expect(body.application.applied_date).toBe("2026-10-09");
    expect(mockUpdate.mock.calls[1][0]).toEqual({ applied_date: expect.stringMatching(/^\d{4}-\d{2}-\d{2}$/) });
    expect(mockIs).toHaveBeenCalledWith("applied_date", null);
  });

  it("never overwrites an existing applied_date", async () => {
    mockSingle.mockResolvedValue({ data: { id: "1", stage: "applied", applied_date: "2026-10-01" }, error: null });
    const req = new Request("http://test", { method: "PATCH", body: JSON.stringify({ stage: "applied" }) });
    const body = await (await PATCH(req, params("1"))).json();
    expect(body.application.applied_date).toBe("2026-10-01");
    expect(mockUpdate).toHaveBeenCalledTimes(1);
  });

  it("other stages leave the date alone", async () => {
    mockSingle.mockResolvedValue({ data: { id: "1", stage: "onsite", applied_date: null }, error: null });
    const req = new Request("http://test", { method: "PATCH", body: JSON.stringify({ stage: "onsite" }) });
    await PATCH(req, params("1"));
    expect(mockUpdate).toHaveBeenCalledTimes(1);
  });
});
