import { describe, it, expect, vi, beforeEach } from "vitest";

const { mockDraftsGet, mockDraftsUpdate, mockGetGmail } = vi.hoisted(() => ({
  mockDraftsGet: vi.fn(),
  mockDraftsUpdate: vi.fn(),
  mockGetGmail: vi.fn(),
}));

vi.mock("@/lib/gmail-server", () => ({ getGmailClient: mockGetGmail }));

const mockSingle = vi.hoisted(() => vi.fn());
const mockLimit = vi.hoisted(() => vi.fn(() => ({ single: mockSingle })));

vi.mock("@supabase/supabase-js", () => {
  const chain: Record<string, unknown> = {};
  for (const m of ["eq", "is"]) {
    chain[m] = vi.fn(() => chain);
  }
  chain.order = vi.fn(() => ({ limit: mockLimit }));
  chain.single = mockSingle;
  const updateChain = { eq: vi.fn(() => ({ data: null, error: null })) };

  return {
    createClient: vi.fn(() => ({
      from: vi.fn(() => ({
        select: vi.fn(() => chain),
        update: vi.fn(() => updateChain),
      })),
    })),
  };
});

import { POST } from "./route";

function req(body: unknown) {
  return new Request("http://localhost/api/update-draft", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

const contact = {
  id: 1,
  email: "dana@acme.com",
  stage: "first_touch_drafted",
  deleted_at: null,
};

const draftRow = {
  id: 10,
  gmail_draft_id: "r-draft99",
  subject: "Original",
  body: "Original body",
  sent_body: null,
};

beforeEach(() => {
  vi.clearAllMocks();
  mockGetGmail.mockReturnValue({
    users: {
      drafts: { get: mockDraftsGet, update: mockDraftsUpdate },
    },
  });
  mockDraftsGet.mockResolvedValue({ data: { message: { payload: { headers: [] } } } });
  mockDraftsUpdate.mockResolvedValue({ data: {} });
});

describe("POST /api/update-draft", () => {
  it("returns 400 when contact_id is missing", async () => {
    const res = await POST(req({ subject: "s", body: "b" }));
    expect(res.status).toBe(400);
  });

  it("accepts numeric contact_id (Supabase returns integer ids as numbers)", async () => {
    mockSingle
      .mockResolvedValueOnce({ data: contact, error: null })
      .mockResolvedValueOnce({ data: draftRow, error: null });

    const res = await POST(req({ contact_id: 1, subject: "New Subject", body: "New body" }));
    // Should reach Gmail (200), not fail validation (400)
    expect(res.status).toBe(200);
  });

  it("returns 400 when subject is missing", async () => {
    const res = await POST(req({ contact_id: "1", body: "b" }));
    expect(res.status).toBe(400);
  });

  it("returns 400 when body is missing", async () => {
    const res = await POST(req({ contact_id: "1", subject: "s" }));
    expect(res.status).toBe(400);
  });

  it("happy path: updates draft and returns 200", async () => {
    mockSingle
      .mockResolvedValueOnce({ data: contact, error: null })
      .mockResolvedValueOnce({ data: draftRow, error: null });

    const res = await POST(
      req({ contact_id: "1", subject: "New Subject", body: "New body" })
    );
    expect(res.status).toBe(200);
    const json = await res.json();
    expect(json.ok).toBe(true);
    expect(mockDraftsUpdate).toHaveBeenCalledWith(
      expect.objectContaining({ id: "r-draft99" })
    );
  });

  it("returns 502 when Gmail drafts.update fails", async () => {
    mockSingle
      .mockResolvedValueOnce({ data: contact, error: null })
      .mockResolvedValueOnce({ data: draftRow, error: null });
    mockDraftsUpdate.mockRejectedValueOnce(new Error("quota"));

    const res = await POST(
      req({ contact_id: "1", subject: "s", body: "b" })
    );
    expect(res.status).toBe(502);
  });
});
