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
  mockIs.mockReturnValue({ select: mockSelect });
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
    mockMaybeSingle.mockResolvedValue({ data: { id: "1" }, error: null });
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

  it("does not gate a stage-only edit on approved_at (e.g. marking an approved row withdrawn)", async () => {
    mockSingle.mockResolvedValue({ data: { id: "1", stage: "withdrawn" }, error: null });
    const req = new Request("http://test", {
      method: "PATCH",
      body: JSON.stringify({ stage: "withdrawn" }),
    });
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
