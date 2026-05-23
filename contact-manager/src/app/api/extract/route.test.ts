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
  dartmouth: false,
  notes: null,
  resume_url: null,
};

beforeEach(() => {
  mockCreate.mockReset();
});

describe("POST /api/extract — basic extraction", () => {
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
    const two = [
      sampleExtraction,
      { ...sampleExtraction, name: "Eve", email: "eve@example.com" },
    ];
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

  it("forwards the user text inside the Claude prompt using claude-sonnet-4-6", async () => {
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

describe("POST /api/extract — input validation", () => {
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
    expect(mockCreate).not.toHaveBeenCalled();
  });

  it("returns 400 when text is whitespace only", async () => {
    const res = await POST(makeRequest({ text: "   \n  " }));
    expect(res.status).toBe(400);
    expect(mockCreate).not.toHaveBeenCalled();
  });

  it("returns 400 when body exceeds 20000 characters", async () => {
    const res = await POST(makeRequest({ text: "a".repeat(20001) }));
    expect(res.status).toBe(400);
    const json = await res.json();
    expect(json.error).toMatch(/input too large/i);
    expect(mockCreate).not.toHaveBeenCalled();
  });

  it("returns 200 for body exactly 20000 characters", async () => {
    mockCreate.mockResolvedValueOnce({
      content: [{ type: "text", text: JSON.stringify(sampleExtraction) }],
    });
    const res = await POST(makeRequest({ text: "a".repeat(20000) }));
    expect(res.status).toBe(200);
  });

  it("returns 400 when body contains more than 50 @ signs", async () => {
    const text = Array(51).fill("x@y.com").join(" ");
    const res = await POST(makeRequest({ text }));
    expect(res.status).toBe(400);
    const json = await res.json();
    expect(json.error).toMatch(/too many contacts/i);
    expect(mockCreate).not.toHaveBeenCalled();
  });

  it("returns 200 for body with exactly 50 @ signs", async () => {
    mockCreate.mockResolvedValueOnce({
      content: [{ type: "text", text: JSON.stringify(sampleExtraction) }],
    });
    const text = Array(50).fill("x@y.com").join(" ");
    const res = await POST(makeRequest({ text }));
    expect(res.status).toBe(200);
  });
});

describe("POST /api/extract — upstream failures", () => {
  it("returns 500 when the Anthropic SDK throws", async () => {
    mockCreate.mockRejectedValueOnce(new Error("rate limited"));
    const res = await POST(makeRequest({ text: "x" }));
    expect(res.status).toBe(500);
    const json = await res.json();
    expect(json.error).toBe("extraction service unavailable");
  });

  it("returns 502 when Claude returns invalid JSON", async () => {
    mockCreate.mockResolvedValueOnce({
      content: [{ type: "text", text: "not even close to JSON" }],
    });
    const res = await POST(makeRequest({ text: "x" }));
    expect(res.status).toBe(502);
    const json = await res.json();
    expect(json.error).toBe("upstream parse failure");
  });

  it("returns 502 when Claude returns valid JSON but an empty array", async () => {
    mockCreate.mockResolvedValueOnce({
      content: [{ type: "text", text: "[]" }],
    });
    const res = await POST(makeRequest({ text: "x" }));
    expect(res.status).toBe(502);
    const json = await res.json();
    expect(json.error).toBe("no contacts extracted");
  });
});

describe("POST /api/extract — field normalization", () => {
  it("marks missing_email=true when email is null", async () => {
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

  it("marks missing_email=true when email has no @", async () => {
    const badEmail = { ...sampleExtraction, email: "notanemail" };
    mockCreate.mockResolvedValueOnce({
      content: [{ type: "text", text: JSON.stringify(badEmail) }],
    });
    const res = await POST(makeRequest({ text: "x" }));
    expect(res.status).toBe(200);
    const json = await res.json();
    expect(json.contacts[0].missing_email).toBe(true);
  });

  it("marks missing_required=true with ['company'] when company absent — still returns 200", async () => {
    const noCompany = { ...sampleExtraction, company: null };
    mockCreate.mockResolvedValueOnce({
      content: [{ type: "text", text: JSON.stringify(noCompany) }],
    });
    const res = await POST(makeRequest({ text: "x" }));
    expect(res.status).toBe(200);
    const json = await res.json();
    expect(json.contacts[0].missing_required).toBe(true);
    expect(json.contacts[0].required_missing_fields).toContain("company");
  });

  it("marks missing_required=true with ['name'] when name absent — still returns 200", async () => {
    const noName = { ...sampleExtraction, name: null };
    mockCreate.mockResolvedValueOnce({
      content: [{ type: "text", text: JSON.stringify(noName) }],
    });
    const res = await POST(makeRequest({ text: "x" }));
    expect(res.status).toBe(200);
    const json = await res.json();
    expect(json.contacts[0].missing_required).toBe(true);
    expect(json.contacts[0].required_missing_fields).toContain("name");
  });

  it("auto-corrects invalid mode 'foo' to 'outreach'", async () => {
    const badMode = { ...sampleExtraction, mode: "foo" };
    mockCreate.mockResolvedValueOnce({
      content: [{ type: "text", text: JSON.stringify(badMode) }],
    });
    const res = await POST(makeRequest({ text: "x" }));
    expect(res.status).toBe(200);
    const json = await res.json();
    expect(json.contacts[0].mode).toBe("outreach");
  });

  it("auto-corrects tier 7 to 2", async () => {
    const badTier = { ...sampleExtraction, tier: 7 };
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

  it("sets dartmouth=true when dartmouth field is true", async () => {
    const withDartmouth = { ...sampleExtraction, dartmouth: true, notes: "Tuck Class of 2006" };
    mockCreate.mockResolvedValueOnce({
      content: [{ type: "text", text: JSON.stringify(withDartmouth) }],
    });
    const res = await POST(makeRequest({ text: "x" }));
    expect(res.status).toBe(200);
    const json = await res.json();
    expect(json.contacts[0].dartmouth).toBe(true);
  });
});

describe("POST /api/extract — state extraction", () => {
  it("passes through a valid 2-letter state code", async () => {
    const withState = { ...sampleExtraction, state: "NY" };
    mockCreate.mockResolvedValueOnce({
      content: [{ type: "text", text: JSON.stringify(withState) }],
    });
    const res = await POST(makeRequest({ text: "x" }));
    const json = await res.json();
    expect(json.contacts[0].state).toBe("NY");
  });

  it("upcases a lowercase state code", async () => {
    const withState = { ...sampleExtraction, state: "ca" };
    mockCreate.mockResolvedValueOnce({
      content: [{ type: "text", text: JSON.stringify(withState) }],
    });
    const res = await POST(makeRequest({ text: "x" }));
    const json = await res.json();
    expect(json.contacts[0].state).toBe("CA");
  });

  it("sets state=null when Claude returns null", async () => {
    const withState = { ...sampleExtraction, state: null };
    mockCreate.mockResolvedValueOnce({
      content: [{ type: "text", text: JSON.stringify(withState) }],
    });
    const res = await POST(makeRequest({ text: "x" }));
    const json = await res.json();
    expect(json.contacts[0].state).toBeNull();
  });

  it("sets state=null when Claude omits the field", async () => {
    mockCreate.mockResolvedValueOnce({
      content: [{ type: "text", text: JSON.stringify(sampleExtraction) }],
    });
    const res = await POST(makeRequest({ text: "x" }));
    const json = await res.json();
    expect(json.contacts[0].state).toBeNull();
  });

  it("sets state=null when Claude returns a non-2-char string", async () => {
    const withState = { ...sampleExtraction, state: "New York" };
    mockCreate.mockResolvedValueOnce({
      content: [{ type: "text", text: JSON.stringify(withState) }],
    });
    const res = await POST(makeRequest({ text: "x" }));
    const json = await res.json();
    expect(json.contacts[0].state).toBeNull();
  });
});

describe("POST /api/extract — Dartmouth year rules", () => {
  it("T'24 in notes maps to Tuck Class of 2024 (20YY rule: 24 <= 26+5=31)", async () => {
    const c = { ...sampleExtraction, dartmouth: true, notes: "Tuck Class of 2024" };
    mockCreate.mockResolvedValueOnce({
      content: [{ type: "text", text: JSON.stringify(c) }],
    });
    const res = await POST(makeRequest({ text: "x" }));
    const json = await res.json();
    expect(json.contacts[0].notes).toBe("Tuck Class of 2024");
  });

  it("T'64 in notes maps to Tuck Class of 1964 (19YY rule: 64 > 26+5=31)", async () => {
    const c = { ...sampleExtraction, dartmouth: true, notes: "Tuck Class of 1964" };
    mockCreate.mockResolvedValueOnce({
      content: [{ type: "text", text: JSON.stringify(c) }],
    });
    const res = await POST(makeRequest({ text: "x" }));
    const json = await res.json();
    expect(json.contacts[0].notes).toBe("Tuck Class of 1964");
  });
});
