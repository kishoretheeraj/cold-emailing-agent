import { describe, it, expect, vi, beforeEach } from "vitest";

// ── Hoisted mocks ─────────────────────────────────────────────────────────────

const { mockCreate, mockSupabaseFrom, mockSingle, mockSelect, mockEq } =
  vi.hoisted(() => ({
    mockCreate: vi.fn(),
    mockSingle: vi.fn(),
    mockSelect: vi.fn(),
    mockEq: vi.fn(),
    mockSupabaseFrom: vi.fn(),
  }));

vi.mock("@anthropic-ai/sdk", () => ({
  default: class FakeAnthropic {
    messages = { create: mockCreate };
  },
}));

vi.mock("@supabase/supabase-js", () => ({
  createClient: () => ({
    from: mockSupabaseFrom,
  }),
}));

import { POST, _resetRateLimitForTesting } from "./route";

// ── Fixtures ───────────────────────────────────────────────────────────────────

const CONTACT = {
  id: "1",
  name: "Alice Chen",
  email: "alice@acme.com",
  company: "Acme Corp",
  role: "VP Engineering",
  detail: "Built fintech compliance dashboards",
  tier: 1,
  mode: "outreach",
  stage: "new",
  reply_status: "no_reply",
  classifier_status: null,
  dartmouth: false,
  job_title: null,
  job_description: null,
  company_applied: null,
  applied_date: null,
  followup_date: null,
  notes: null,
  created_at: "2026-06-01T00:00:00Z",
  message_id: null,
  last_emailed: null,
  deleted_at: null,
  state: null,
};

const PROMPTS = [
  { key: "sender_profile", value: "I am Kishore." },
  { key: "outreach_prompt", value: "Write for {name} at {company}. Tier {tier}: {tier_instruction}. Template: {template}. {dartmouth_instruction}" },
  { key: "tier_1_instruction", value: "Dream company." },
  { key: "outreach_first_touch_instruction", value: "First touch." },
];

function makeRequest(body: unknown, ip = "1.2.3.4"): Request {
  return new Request("http://localhost/api/preview-draft", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "x-forwarded-for": ip,
    },
    body: JSON.stringify(body),
  });
}

function setupSupabaseMocks(
  contactResult: { data: unknown; error: unknown } = { data: CONTACT, error: null },
  promptsResult: { data: unknown } = { data: PROMPTS }
) {
  // contacts chain: .from("contacts").select("*").eq("id", id).single()
  const contactChain = {
    select: vi.fn().mockReturnThis(),
    eq: vi.fn().mockReturnThis(),
    single: vi.fn().mockResolvedValue(contactResult),
  };
  // prompts chain: .from("prompts").select("key,value")
  const promptsChain = {
    select: vi.fn().mockResolvedValue(promptsResult),
  };
  mockSupabaseFrom.mockImplementation((table: string) => {
    if (table === "contacts") return contactChain;
    if (table === "prompts") return promptsChain;
    return { select: vi.fn().mockResolvedValue({ data: [] }) };
  });
}

beforeEach(() => {
  vi.resetAllMocks();
  _resetRateLimitForTesting();
  setupSupabaseMocks();
});

// ── Validation ─────────────────────────────────────────────────────────────────

describe("POST /api/preview-draft — validation", () => {
  it("returns 400 when body is empty", async () => {
    const res = await POST(
      new Request("http://localhost/api/preview-draft", {
        method: "POST",
        headers: { "Content-Type": "application/json", "x-forwarded-for": "1.2.3.4" },
        body: "{}",
      })
    );
    expect(res.status).toBe(400);
  });

  it("returns 400 when contact_id is missing", async () => {
    const res = await POST(
      makeRequest({
        active_prompt_key: "outreach_prompt",
        sandbox_value: "foo",
        mode: "writer",
      })
    );
    expect(res.status).toBe(400);
  });

  it("returns 400 when mode is invalid", async () => {
    const res = await POST(
      makeRequest({
        contact_id: "1",
        active_prompt_key: "outreach_prompt",
        sandbox_value: "foo",
        mode: "invalid",
      })
    );
    expect(res.status).toBe(400);
  });

  it("returns 400 for invalid JSON body", async () => {
    const res = await POST(
      new Request("http://localhost/api/preview-draft", {
        method: "POST",
        headers: { "Content-Type": "application/json", "x-forwarded-for": "1.2.3.4" },
        body: "not-json",
      })
    );
    expect(res.status).toBe(400);
  });
});

// ── Rate limiting ──────────────────────────────────────────────────────────────

describe("POST /api/preview-draft — rate limiting", () => {
  it("returns 429 after 10 requests from the same IP within 60s", async () => {
    const validBody = {
      contact_id: "1",
      active_prompt_key: "outreach_prompt",
      sandbox_value: "test",
      mode: "writer",
    };
    mockCreate.mockResolvedValue({
      content: [{ type: "text", text: "Email body." }],
    });

    // Use a unique IP to avoid pollution from other tests
    const uniqueIp = "9.9.9.9";

    let lastStatus = 200;
    for (let i = 0; i < 11; i++) {
      const res = await POST(makeRequest(validBody, uniqueIp));
      lastStatus = res.status;
    }
    expect(lastStatus).toBe(429);
  });
});

// ── 404 contact not found ──────────────────────────────────────────────────────

describe("POST /api/preview-draft — contact not found", () => {
  it("returns 404 when contact does not exist", async () => {
    setupSupabaseMocks({ data: null, error: { message: "not found" } });
    const res = await POST(
      makeRequest({
        contact_id: "999",
        active_prompt_key: "outreach_prompt",
        sandbox_value: "test",
        mode: "writer",
      })
    );
    expect(res.status).toBe(404);
  });
});

// ── Writer mode ────────────────────────────────────────────────────────────────

describe("POST /api/preview-draft — writer mode", () => {
  it("returns body for a non-first-touch contact (no subject)", async () => {
    // followup1_sent → send_followup1 → NOT a first-touch action → no subject
    setupSupabaseMocks({
      data: { ...CONTACT, stage: "followup1_sent" },
      error: null,
    });
    mockCreate.mockResolvedValue({
      content: [{ type: "text", text: "Hi Alice, following up." }],
    });

    const res = await POST(
      makeRequest({
        contact_id: "1",
        active_prompt_key: "outreach_prompt",
        sandbox_value: "Write for {name}.",
        mode: "writer",
      })
    );
    expect(res.status).toBe(200);
    const json = await res.json() as { kind: string; body: string; subject?: string };
    expect(json.kind).toBe("writer");
    expect(json.body).toBe("Hi Alice, following up.");
    expect(json.subject).toBeUndefined();
  });

  it("returns body + subject for a first-touch contact", async () => {
    mockCreate
      .mockResolvedValueOnce({
        content: [{ type: "text", text: "Great intro body." }],
      })
      .mockResolvedValueOnce({
        content: [{ type: "text", text: "Quick chat?" }],
      });

    const res = await POST(
      makeRequest({
        contact_id: "1",
        active_prompt_key: "outreach_prompt",
        sandbox_value: "Write for {name}.",
        mode: "writer",
      })
    );
    expect(res.status).toBe(200);
    const json = await res.json() as { kind: string; body: string; subject?: string };
    expect(json.kind).toBe("writer");
    expect(json.body).toBe("Great intro body.");
    expect(json.subject).toBe("Quick chat?");
  });

  it("passes sandbox_value as the prompt for active_prompt_key", async () => {
    mockCreate.mockResolvedValue({
      content: [{ type: "text", text: "Custom template body." }],
    });

    await POST(
      makeRequest({
        contact_id: "1",
        active_prompt_key: "outreach_prompt",
        sandbox_value: "SANDBOX: Write for {name} only.",
        mode: "writer",
      })
    );

    // The assembler should have used the sandbox value
    const callArgs = mockCreate.mock.calls[0][0] as { messages: { content: string }[] };
    expect(callArgs.messages[0].content).toContain("SANDBOX: Write for");
    expect(callArgs.messages[0].content).toContain("Alice Chen");
  });

  it("returns 500 when Anthropic SDK throws", async () => {
    mockCreate.mockRejectedValue(new Error("SDK error"));

    const res = await POST(
      makeRequest({
        contact_id: "1",
        active_prompt_key: "outreach_prompt",
        sandbox_value: "test",
        mode: "writer",
      })
    );
    expect(res.status).toBe(500);
  });
});

// ── Critic mode ────────────────────────────────────────────────────────────────

describe("POST /api/preview-draft — critic mode", () => {
  const criticBody = {
    contact_id: "1",
    active_prompt_key: "critic_prompt",
    sandbox_value: "Evaluate {body} for {contact_context}.",
    mode: "critic",
    critic_draft_subject: "Re: Quick question",
    critic_draft_body: "Hi Alice, let's connect.",
  };

  it("returns structured critic result", async () => {
    mockCreate.mockResolvedValue({
      content: [
        {
          type: "text",
          text: JSON.stringify({
            score: 5,
            verdict: "FAIL",
            feedback: "Too generic.",
            killed_by: ["personalization"],
            failed_soft_criteria: ["specificity"],
            rewrite_required: true,
          }),
        },
      ],
    });

    const res = await POST(makeRequest(criticBody));
    expect(res.status).toBe(200);
    const json = await res.json() as Record<string, unknown>;
    expect(json.kind).toBe("critic");
    expect(json.score).toBe(5);
    expect(json.verdict).toBe("FAIL");
    expect(json.feedback).toBe("Too generic.");
    expect(json.rewrite_required).toBe(true);
    expect(json.killed_by).toEqual(["personalization"]);
  });

  it("strips ```json fences before parsing critic response", async () => {
    mockCreate.mockResolvedValue({
      content: [
        {
          type: "text",
          text: '```json\n{"score":7,"verdict":"PASS","feedback":"Good.","killed_by":[],"failed_soft_criteria":[],"rewrite_required":false}\n```',
        },
      ],
    });

    const res = await POST(makeRequest(criticBody));
    expect(res.status).toBe(200);
    const json = await res.json() as { score: number; verdict: string };
    expect(json.score).toBe(7);
    expect(json.verdict).toBe("PASS");
  });

  it("returns 502 when critic response is not valid JSON", async () => {
    mockCreate.mockResolvedValue({
      content: [{ type: "text", text: "not json at all" }],
    });

    const res = await POST(makeRequest(criticBody));
    expect(res.status).toBe(502);
  });
});
