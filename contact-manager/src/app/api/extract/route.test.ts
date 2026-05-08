import { describe, it, expect, vi, beforeEach } from "vitest";

// vi.mock is hoisted to the top of the file, so any external state it depends
// on must be hoisted alongside it via vi.hoisted.
const { mockCreate } = vi.hoisted(() => ({ mockCreate: vi.fn() }));

vi.mock("@anthropic-ai/sdk", () => ({
  default: class FakeAnthropic {
    messages = { create: mockCreate };
  },
}));

// Import AFTER mocks are set up.
import { POST } from "./route";

function makeRequest(body: unknown): Request {
  return new Request("http://localhost/api/extract", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

const sampleExtraction = {
  name: "Dana",
  email: "dana@example.com",
  company: "Clearbond",
  role: "CEO",
  detail: "customs bond SaaS",
  tier: 2,
  mode: "outreach",
  dartmouth: true,
  job_title: null,
  job_description: null,
  applied_date: null,
  notes: null,
};

beforeEach(() => {
  mockCreate.mockReset();
});

describe("POST /api/extract", () => {
  it("returns contacts array with is_bulk=false for a single contact", async () => {
    mockCreate.mockResolvedValueOnce({
      content: [{ type: "text", text: JSON.stringify(sampleExtraction) }],
    });

    const res = await POST(makeRequest({ text: "Dana from Clearbond" }));
    expect(res.status).toBe(200);
    const json = await res.json();
    expect(json.is_bulk).toBe(false);
    expect(json.count).toBe(1);
    expect(json.contacts).toHaveLength(1);
    expect(json.contacts[0].name).toBe("Dana");
    expect(json.contacts[0].email).toBe("dana@example.com");
    expect(json.contacts[0].missing_email).toBe(false);
  });

  it("returns is_bulk=true when Claude returns an array of contacts", async () => {
    const two = [sampleExtraction, { ...sampleExtraction, name: "Eve", email: "eve@example.com" }];
    mockCreate.mockResolvedValueOnce({
      content: [{ type: "text", text: JSON.stringify(two) }],
    });

    const res = await POST(makeRequest({ text: "Dana and Eve" }));
    expect(res.status).toBe(200);
    const json = await res.json();
    expect(json.is_bulk).toBe(true);
    expect(json.count).toBe(2);
    expect(json.contacts).toHaveLength(2);
  });

  it("strips ```json code fences from Claude response", async () => {
    mockCreate.mockResolvedValueOnce({
      content: [
        {
          type: "text",
          text: "```json\n" + JSON.stringify(sampleExtraction) + "\n```",
        },
      ],
    });

    const res = await POST(makeRequest({ text: "anything" }));
    expect(res.status).toBe(200);
    const json = await res.json();
    expect(json.contacts[0].name).toBe("Dana");
  });

  it("returns 400 when text is empty", async () => {
    const res = await POST(makeRequest({ text: "" }));
    expect(res.status).toBe(400);
    const json = await res.json();
    expect(json.error).toMatch(/text is required/i);
    expect(mockCreate).not.toHaveBeenCalled();
  });

  it("returns 400 when text is missing", async () => {
    const res = await POST(makeRequest({}));
    expect(res.status).toBe(400);
  });

  it("returns 400 when text is whitespace only", async () => {
    const res = await POST(makeRequest({ text: "   \n  " }));
    expect(res.status).toBe(400);
    expect(mockCreate).not.toHaveBeenCalled();
  });

  it("returns 502 when Claude returns invalid JSON", async () => {
    mockCreate.mockResolvedValueOnce({
      content: [{ type: "text", text: "not even close to JSON" }],
    });

    const res = await POST(makeRequest({ text: "x" }));
    expect(res.status).toBe(502);
    const json = await res.json();
    expect(json.error).toMatch(/Could not parse contacts/i);
  });

  it("returns 500 when the SDK throws", async () => {
    mockCreate.mockRejectedValueOnce(new Error("rate limited"));
    const res = await POST(makeRequest({ text: "x" }));
    expect(res.status).toBe(500);
    const json = await res.json();
    expect(json.error).toBe("rate limited");
  });

  it("forwards the user text inside the Claude prompt", async () => {
    mockCreate.mockResolvedValueOnce({
      content: [{ type: "text", text: JSON.stringify(sampleExtraction) }],
    });

    await POST(makeRequest({ text: "Dana from Clearbond does customs SaaS" }));

    expect(mockCreate).toHaveBeenCalledOnce();
    const callArgs = mockCreate.mock.calls[0][0];
    expect(callArgs.model).toBe("claude-sonnet-4-6");
    const userMessage = callArgs.messages[0].content;
    expect(userMessage).toContain("Dana from Clearbond does customs SaaS");
  });
});

describe("POST /api/extract — field validation", () => {
  it("returns 422 when name is missing (contact is skipped)", async () => {
    const noName = { ...sampleExtraction, name: null };
    mockCreate.mockResolvedValueOnce({
      content: [{ type: "text", text: JSON.stringify(noName) }],
    });

    const res = await POST(makeRequest({ text: "x" }));
    expect(res.status).toBe(422);
    const json = await res.json();
    expect(json.error).toMatch(/no valid contacts/i);
  });

  it("marks contact with missing_email=true when email is absent (does not return 422)", async () => {
    const noEmail = { ...sampleExtraction, email: null };
    mockCreate.mockResolvedValueOnce({
      content: [{ type: "text", text: JSON.stringify(noEmail) }],
    });

    const res = await POST(makeRequest({ text: "x" }));
    expect(res.status).toBe(200);
    const json = await res.json();
    expect(json.contacts[0].missing_email).toBe(true);
    expect(json.contacts[0].email).toBeNull();
  });

  it("marks contact with missing_email=true when email has no @", async () => {
    const badEmail = { ...sampleExtraction, email: "notanemail" };
    mockCreate.mockResolvedValueOnce({
      content: [{ type: "text", text: JSON.stringify(badEmail) }],
    });

    const res = await POST(makeRequest({ text: "x" }));
    expect(res.status).toBe(200);
    const json = await res.json();
    expect(json.contacts[0].missing_email).toBe(true);
  });

  it("returns 422 when company is missing (contact is skipped)", async () => {
    const noCompany = { ...sampleExtraction, company: null };
    mockCreate.mockResolvedValueOnce({
      content: [{ type: "text", text: JSON.stringify(noCompany) }],
    });

    const res = await POST(makeRequest({ text: "x" }));
    expect(res.status).toBe(422);
    const json = await res.json();
    expect(json.error).toMatch(/no valid contacts/i);
  });

  it("auto-corrects invalid mode to outreach", async () => {
    const badMode = { ...sampleExtraction, mode: "unknown" };
    mockCreate.mockResolvedValueOnce({
      content: [{ type: "text", text: JSON.stringify(badMode) }],
    });

    const res = await POST(makeRequest({ text: "x" }));
    expect(res.status).toBe(200);
    const json = await res.json();
    expect(json.contacts[0].mode).toBe("outreach");
  });

  it("auto-corrects invalid tier to 2", async () => {
    const badTier = { ...sampleExtraction, tier: 99 };
    mockCreate.mockResolvedValueOnce({
      content: [{ type: "text", text: JSON.stringify(badTier) }],
    });

    const res = await POST(makeRequest({ text: "x" }));
    expect(res.status).toBe(200);
    const json = await res.json();
    expect(json.contacts[0].tier).toBe(2);
  });

  it("auto-corrects null dartmouth to false", async () => {
    const noDartmouth = { ...sampleExtraction, dartmouth: null };
    mockCreate.mockResolvedValueOnce({
      content: [{ type: "text", text: JSON.stringify(noDartmouth) }],
    });

    const res = await POST(makeRequest({ text: "x" }));
    expect(res.status).toBe(200);
    const json = await res.json();
    expect(json.contacts[0].dartmouth).toBe(false);
  });

  it("skips contacts missing name, keeps contacts with missing email marked", async () => {
    const two = [
      { ...sampleExtraction, name: null },           // skipped — no name
      { ...sampleExtraction, email: null, name: "Eve" }, // kept — missing_email=true
    ];
    mockCreate.mockResolvedValueOnce({
      content: [{ type: "text", text: JSON.stringify(two) }],
    });

    const res = await POST(makeRequest({ text: "x" }));
    expect(res.status).toBe(200);
    const json = await res.json();
    expect(json.contacts).toHaveLength(1);
    expect(json.contacts[0].name).toBe("Eve");
    expect(json.contacts[0].missing_email).toBe(true);
  });
});
