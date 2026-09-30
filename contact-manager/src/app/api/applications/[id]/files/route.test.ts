import { describe, it, expect, vi, beforeEach } from "vitest";
import { GET } from "./route";

const mockSingle = vi.fn();
const mockEq = vi.fn();
const mockSelect = vi.fn();
const mockFrom = vi.fn();
const mockCreateSignedUrl = vi.fn();

vi.mock("@supabase/supabase-js", () => ({
  createClient: vi.fn(() => ({
    from: mockFrom,
    storage: { from: vi.fn(() => ({ createSignedUrl: mockCreateSignedUrl })) },
  })),
}));

function params(id: string) {
  return { params: Promise.resolve({ id }) };
}

beforeEach(() => {
  vi.clearAllMocks();
  mockSingle.mockResolvedValue({
    data: { resume_file_ref: "resumes/5/resume.pdf", cover_letter_file_ref: "resumes/5/cl.pdf" },
    error: null,
  });
  mockSelect.mockReturnValue({ eq: mockEq });
  mockEq.mockReturnValue({ single: mockSingle });
  mockFrom.mockReturnValue({ select: mockSelect });
  mockCreateSignedUrl.mockResolvedValue({ data: { signedUrl: "https://signed.example/x" }, error: null });
});

describe("GET /api/applications/[id]/files", () => {
  it("returns signed urls for both files when both refs are present", async () => {
    const res = await GET(new Request("http://test"), params("5"));
    const body = await res.json();
    expect(body.resume_url).toBe("https://signed.example/x");
    expect(body.cover_letter_url).toBe("https://signed.example/x");
    expect(body.resume_error).toBe(false);
    expect(body.cover_letter_error).toBe(false);
    expect(mockCreateSignedUrl).toHaveBeenCalledWith("resumes/5/resume.pdf", 300);
    expect(mockCreateSignedUrl).toHaveBeenCalledWith("resumes/5/cl.pdf", 300);
  });

  it("returns null (not an error) for a missing ref instead of calling createSignedUrl", async () => {
    mockSingle.mockResolvedValue({
      data: { resume_file_ref: null, cover_letter_file_ref: null },
      error: null,
    });
    const res = await GET(new Request("http://test"), params("5"));
    const body = await res.json();
    expect(body.resume_url).toBeNull();
    expect(body.cover_letter_url).toBeNull();
    expect(body.resume_error).toBe(false);
    expect(body.cover_letter_error).toBe(false);
    expect(mockCreateSignedUrl).not.toHaveBeenCalled();
  });

  it("returns an error flag (I11 -- distinct from a missing ref) when signing fails", async () => {
    mockCreateSignedUrl.mockResolvedValue({ data: null, error: new Error("storage unavailable") });
    const res = await GET(new Request("http://test"), params("5"));
    const body = await res.json();
    expect(body.resume_url).toBeNull();
    expect(body.resume_error).toBe(true);
    expect(body.cover_letter_url).toBeNull();
    expect(body.cover_letter_error).toBe(true);
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
