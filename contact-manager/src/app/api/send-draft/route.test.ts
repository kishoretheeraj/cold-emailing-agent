import { describe, it, expect, vi, beforeEach } from "vitest";

// Hoist mocks so they are available before module-level imports.
const { mockDraftsSend, mockMessagesGet, mockGetGmail } = vi.hoisted(() => ({
  mockDraftsSend: vi.fn(),
  mockMessagesGet: vi.fn(),
  mockGetGmail: vi.fn(),
}));

vi.mock("@/lib/gmail-server", () => ({ getGmailClient: mockGetGmail }));

// Supabase chain mocks.
// mockSingle is shared: contacts uses chain.single directly;
// draft_history uses chain.order().limit().single() so mockLimit must return { single: mockSingle }.
const mockSingle = vi.hoisted(() => vi.fn());
const mockLimit = vi.hoisted(() => vi.fn(() => ({ single: mockSingle })));
const mockInsert = vi.hoisted(() => vi.fn());
const mockUpsert = vi.hoisted(() => vi.fn());

vi.mock("@supabase/supabase-js", () => {
  const chain: Record<string, unknown> = {};
  for (const m of ["eq", "is"]) {
    chain[m] = vi.fn(() => chain);
  }
  chain.order = vi.fn(() => ({ limit: mockLimit }));
  chain.single = mockSingle;
  const eqStub = vi.fn(() => ({ data: null, error: null }));
  const updateChain = { eq: eqStub };
  const upsertResult = { data: null, error: null };

  return {
    createClient: vi.fn(() => ({
      from: vi.fn((table: string) => {
        if (table === "draft_history") {
          return {
            select: vi.fn(() => chain),
            update: vi.fn(() => updateChain),
            upsert: vi.fn(() => upsertResult),
          };
        }
        if (table === "agent_events") {
          return { insert: mockInsert };
        }
        if (table === "email_messages") {
          return { upsert: mockUpsert };
        }
        // contacts
        return {
          select: vi.fn(() => chain),
          update: vi.fn(() => updateChain),
        };
      }),
    })),
  };
});

import { POST } from "./route";

function req(body: unknown) {
  return new Request("http://localhost/api/send-draft", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

const contact = {
  id: 1,
  email: "dana@acme.com",
  stage: "first_touch_drafted",
  name: "Dana",
  deleted_at: null,
};

const draftRow = {
  id: 10,
  contact_id: 1,
  stage: "first_touch_drafted",
  subject: "Hello Dana",
  body: "Body text",
  gmail_draft_id: "r-draft99",
  drafted_at: "2026-05-20T00:00:00Z",
  sent_body: null,
};

beforeEach(() => {
  vi.clearAllMocks();
  mockGetGmail.mockReturnValue({
    users: {
      drafts: { send: mockDraftsSend },
      messages: { get: mockMessagesGet },
    },
  });
  mockInsert.mockResolvedValue({ data: null, error: null });
  mockUpsert.mockResolvedValue({ data: null, error: null });
  mockMessagesGet.mockResolvedValue({ data: { payload: { headers: [], body: {} } } });
});

describe("POST /api/send-draft", () => {
  it("returns 400 when contact_id is missing", async () => {
    const res = await POST(req({}));
    expect(res.status).toBe(400);
  });

  it("returns 400 when contact_id is null", async () => {
    const res = await POST(req({ contact_id: null }));
    expect(res.status).toBe(400);
  });

  it("accepts numeric contact_id (Supabase returns integer ids as numbers)", async () => {
    mockSingle.mockResolvedValueOnce({ data: null, error: { message: "not found" } });
    const res = await POST(req({ contact_id: 123 }));
    // Should reach the contact lookup (404), not fail validation (400)
    expect(res.status).toBe(404);
  });

  it("returns 404 when contact not found", async () => {
    mockSingle.mockResolvedValueOnce({ data: null, error: { message: "not found" } });
    const res = await POST(req({ contact_id: "999" }));
    expect(res.status).toBe(404);
  });

  it("returns 200 already_sent when stage is a *_sent value", async () => {
    mockSingle.mockResolvedValueOnce({
      data: { ...contact, stage: "first_touch_sent" },
      error: null,
    });
    const res = await POST(req({ contact_id: "1" }));
    expect(res.status).toBe(200);
    const json = await res.json();
    expect(json.already_sent).toBe(true);
    expect(mockDraftsSend).not.toHaveBeenCalled();
  });

  it("returns 409 when contact stage is not a drafted state", async () => {
    mockSingle.mockResolvedValueOnce({
      data: { ...contact, stage: "new" },
      error: null,
    });
    const res = await POST(req({ contact_id: "1" }));
    expect(res.status).toBe(409);
  });

  it("returns 410 when latest draft_history has null gmail_draft_id", async () => {
    mockSingle
      .mockResolvedValueOnce({ data: contact, error: null }) // contact
      .mockResolvedValueOnce({
        data: { ...draftRow, gmail_draft_id: null },
        error: null,
      }); // draft_history
    const res = await POST(req({ contact_id: "1" }));
    expect(res.status).toBe(410);
  });

  it("returns 410 when no draft_history row exists", async () => {
    mockSingle
      .mockResolvedValueOnce({ data: contact, error: null })
      .mockResolvedValueOnce({ data: null, error: { message: "no row" } });
    const res = await POST(req({ contact_id: "1" }));
    expect(res.status).toBe(410);
  });

  it("happy path: Gmail send succeeds → 200 with stage and message_id", async () => {
    mockSingle
      .mockResolvedValueOnce({ data: contact, error: null })
      .mockResolvedValueOnce({ data: draftRow, error: null });
    mockDraftsSend.mockResolvedValueOnce({
      data: { id: "msg-sent-01", threadId: "thread-01" },
    });

    const res = await POST(req({ contact_id: "1" }));
    expect(res.status).toBe(200);
    const json = await res.json();
    expect(json.ok).toBe(true);
    expect(json.stage).toBe("first_touch_sent");
    expect(json.message_id).toBe("msg-sent-01");
    expect(mockDraftsSend).toHaveBeenCalledWith({
      userId: "me",
      requestBody: { id: "r-draft99" },
    });
  });

  it("returns 410 when Gmail returns 404", async () => {
    mockSingle
      .mockResolvedValueOnce({ data: contact, error: null })
      .mockResolvedValueOnce({ data: draftRow, error: null });
    mockDraftsSend.mockRejectedValueOnce({ code: 404 });

    const res = await POST(req({ contact_id: "1" }));
    expect(res.status).toBe(410);
  });

  it("returns 401 when Gmail returns 401", async () => {
    mockSingle
      .mockResolvedValueOnce({ data: contact, error: null })
      .mockResolvedValueOnce({ data: draftRow, error: null });
    mockDraftsSend.mockRejectedValueOnce({ code: 401 });

    const res = await POST(req({ contact_id: "1" }));
    expect(res.status).toBe(401);
  });

  it("returns 502 on other Gmail errors", async () => {
    mockSingle
      .mockResolvedValueOnce({ data: contact, error: null })
      .mockResolvedValueOnce({ data: draftRow, error: null });
    mockDraftsSend.mockRejectedValueOnce({ code: 500, message: "internal" });

    const res = await POST(req({ contact_id: "1" }));
    expect(res.status).toBe(502);
  });
});
