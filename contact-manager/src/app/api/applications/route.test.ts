import { describe, it, expect, vi, beforeEach } from "vitest";
import { GET, POST } from "./route";

const mockOrder = vi.fn();
const mockEq = vi.fn();
const mockSelect = vi.fn();
const mockSingle = vi.fn();
const mockInsertSelect = vi.fn();
const mockInsert = vi.fn();
const mockFrom = vi.fn();

vi.mock("@supabase/supabase-js", () => ({
  createClient: vi.fn(() => ({
    from: mockFrom,
  })),
}));

beforeEach(() => {
  vi.clearAllMocks();
  mockOrder.mockResolvedValue({ data: [{ id: "1", company: "Acme" }], error: null });
  mockEq.mockReturnValue({ order: mockOrder });
  mockSelect.mockReturnValue({ order: mockOrder, eq: mockEq });
  mockSingle.mockResolvedValue({ data: { id: "1", company: "Acme", role: "PM" }, error: null });
  mockInsertSelect.mockReturnValue({ single: mockSingle });
  mockInsert.mockReturnValue({ select: mockInsertSelect });
  mockFrom.mockReturnValue({ select: mockSelect, insert: mockInsert });
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
