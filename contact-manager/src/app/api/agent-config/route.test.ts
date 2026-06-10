import { describe, it, expect, vi, beforeEach } from "vitest";
import { GET, POST } from "./route";

const mockSingle = vi.fn();
const mockEq = vi.fn();
const mockSelect = vi.fn();
const mockFrom = vi.fn();
const mockUpsert = vi.fn();

vi.mock("@supabase/supabase-js", () => ({
  createClient: vi.fn(() => ({
    from: mockFrom,
  })),
}));

beforeEach(() => {
  vi.clearAllMocks();
  mockSingle.mockResolvedValue({ data: { value: "none" }, error: null });
  mockEq.mockReturnValue({ single: mockSingle, eq: mockEq });
  mockSelect.mockReturnValue({ eq: mockEq });
  mockUpsert.mockReturnValue({ eq: vi.fn().mockResolvedValue({ error: null }) });
  mockFrom.mockReturnValue({ select: mockSelect, upsert: mockUpsert });
});

describe("GET /api/agent-config", () => {
  it("returns scope from system_config", async () => {
    mockSingle.mockResolvedValue({ data: { value: "agent" }, error: null });
    const res = await GET();
    const body = await res.json();
    expect(body).toEqual({ scope: "agent" });
  });

  it("returns scope=none when table missing or error", async () => {
    mockSingle.mockResolvedValue({ data: null, error: new Error("table not found") });
    const res = await GET();
    const body = await res.json();
    expect(body).toEqual({ scope: "none" });
  });

  it("returns scope=none when row missing", async () => {
    mockSingle.mockResolvedValue({ data: null, error: null });
    const res = await GET();
    const body = await res.json();
    expect(body).toEqual({ scope: "none" });
  });
});

describe("POST /api/agent-config", () => {
  it("accepts valid scope and returns ok", async () => {
    const req = new Request("http://test", {
      method: "POST",
      body: JSON.stringify({ scope: "all" }),
    });
    const res = await POST(req);
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body).toEqual({ ok: true });
  });

  it("returns 400 for invalid scope", async () => {
    const req = new Request("http://test", {
      method: "POST",
      body: JSON.stringify({ scope: "bad" }),
    });
    const res = await POST(req);
    expect(res.status).toBe(400);
  });

  it("returns 400 for missing scope", async () => {
    const req = new Request("http://test", {
      method: "POST",
      body: JSON.stringify({}),
    });
    const res = await POST(req);
    expect(res.status).toBe(400);
  });

  it("returns 400 for invalid JSON", async () => {
    const req = new Request("http://test", {
      method: "POST",
      body: "not json",
    });
    const res = await POST(req);
    expect(res.status).toBe(400);
  });
});
