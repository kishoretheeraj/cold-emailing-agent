import { describe, it, expect, vi, beforeEach } from "vitest";
import { operatorRequest, stubOperatorLogin } from "@/lib/testing/operatorSession";

beforeEach(() => stubOperatorLogin());

const { mockTrash, mockGetGmail } = vi.hoisted(() => ({
  mockTrash: vi.fn(),
  mockGetGmail: vi.fn(),
}));

vi.mock("server-only", () => ({}));
vi.mock("@/lib/gmail-server", () => ({ getGmailClient: mockGetGmail }));

import { POST } from "./route";

function req(body: unknown) {
  return operatorRequest("http://localhost/api/trash-message", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

beforeEach(() => {
  vi.clearAllMocks();
  mockGetGmail.mockReturnValue({
    users: { messages: { trash: mockTrash } },
  });
});

describe("POST /api/trash-message", () => {
  it("returns 400 when message_id is missing", async () => {
    const res = await POST(req({}));
    expect(res.status).toBe(400);
  });

  it("returns 400 when message_id is not a string", async () => {
    const res = await POST(req({ message_id: 42 }));
    expect(res.status).toBe(400);
  });

  it("happy path: trashes message and returns 200", async () => {
    mockTrash.mockResolvedValueOnce({ data: {} });
    const res = await POST(req({ message_id: "msg-abc" }));
    expect(res.status).toBe(200);
    const json = await res.json();
    expect(json.ok).toBe(true);
    expect(mockTrash).toHaveBeenCalledWith({ userId: "me", id: "msg-abc" });
  });

  it("returns 502 on Gmail error", async () => {
    mockTrash.mockRejectedValueOnce(new Error("gmail down"));
    const res = await POST(req({ message_id: "msg-abc" }));
    expect(res.status).toBe(502);
  });
});
