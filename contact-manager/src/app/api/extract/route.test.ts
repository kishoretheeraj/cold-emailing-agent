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
  it("returns parsed JSON when Claude responds with raw JSON", async () => {
    mockCreate.mockResolvedValueOnce({
      content: [{ type: "text", text: JSON.stringify(sampleExtraction) }],
    });

    const res = await POST(makeRequest({ text: "Dana from Clearbond" }));
    expect(res.status).toBe(200);
    const json = await res.json();
    expect(json.data).toEqual(sampleExtraction);
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
    expect(json.data.name).toBe("Dana");
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
    expect(json.error).toMatch(/valid JSON/i);
    expect(json.raw).toBe("not even close to JSON");
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
