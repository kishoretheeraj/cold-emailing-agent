import { describe, it, expect, vi, beforeEach } from "vitest";
import { PATCH } from "./route";

const mockSingle = vi.fn();
const mockSelect = vi.fn();
const mockEq = vi.fn();
const mockUpdate = vi.fn();
const mockFrom = vi.fn();

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
  mockEq.mockReturnValue({ select: mockSelect });
  mockUpdate.mockReturnValue({ eq: mockEq });
  mockFrom.mockReturnValue({ update: mockUpdate });
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
