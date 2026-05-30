import { describe, it, expect, vi, beforeEach } from "vitest";

// limitMock and inMock must be hoisted so they're accessible inside the vi.mock factory.
const { limitMock, inMock } = vi.hoisted(() => ({
  limitMock: vi.fn(),
  inMock: vi.fn(),
}));

// Mock @supabase/supabase-js so createClient() returns a controllable client.
// supabase.ts calls createClient at module load, so the mock must be here rather
// than mocking @/lib/supabase directly (which would replace resolveInsertError too).
vi.mock("@supabase/supabase-js", () => {
  const readChain: Record<string, unknown> = {};
  readChain.select = vi.fn(() => readChain);
  readChain.eq = vi.fn(() => readChain);
  readChain.limit = limitMock;
  readChain.in = inMock;
  return {
    createClient: vi.fn(() => ({ from: vi.fn(() => readChain) })),
  };
});

import { resolveInsertError, checkDuplicateEmails } from "@/lib/supabase";

beforeEach(() => {
  limitMock.mockReset();
  limitMock.mockResolvedValue({ data: [] });
  inMock.mockReset();
  inMock.mockResolvedValue({ data: [] });
});

describe("resolveInsertError", () => {
  it("returns raw message for non-23505 error without querying Supabase", async () => {
    const result = await resolveInsertError(
      { code: "42501", message: "permission denied" },
      "test@example.com"
    );
    expect(result).toBe("permission denied");
    expect(limitMock).not.toHaveBeenCalled();
  });

  it("returns raw message when error has no code", async () => {
    const result = await resolveInsertError(
      { message: "something went wrong" },
      "test@example.com"
    );
    expect(result).toBe("something went wrong");
    expect(limitMock).not.toHaveBeenCalled();
  });

  it("returns already-in-list message when 23505 and email is empty", async () => {
    const result = await resolveInsertError(
      { code: "23505", message: "duplicate key value" },
      ""
    );
    expect(result).toBe("A contact with this email is already in your list.");
    expect(limitMock).not.toHaveBeenCalled();
  });

  it("returns already-in-list message when 23505 and no row found", async () => {
    limitMock.mockResolvedValue({ data: [] });
    const result = await resolveInsertError(
      { code: "23505", message: "duplicate key value" },
      "test@example.com"
    );
    expect(result).toBe("A contact with this email is already in your list.");
  });

  it("returns already-in-list message when 23505 and contact is active (deleted_at null)", async () => {
    limitMock.mockResolvedValue({ data: [{ deleted_at: null, name: "Jane Doe" }] });
    const result = await resolveInsertError(
      { code: "23505", message: "duplicate key value" },
      "jane@example.com"
    );
    expect(result).toBe("A contact with this email is already in your list.");
  });

  it("returns deleted-contact message with name when contact is soft-deleted", async () => {
    limitMock.mockResolvedValue({
      data: [{ deleted_at: "2026-05-16T15:25:07.259+00:00", name: "Omar Al Banawi" }],
    });
    const result = await resolveInsertError(
      { code: "23505", message: "duplicate key value" },
      "omar@example.com"
    );
    expect(result).toBe(
      "Omar Al Banawi with this email was previously deleted. Restore them in the Supabase dashboard to re-add."
    );
  });

  it("falls back to 'A contact' when soft-deleted row has no name", async () => {
    limitMock.mockResolvedValue({
      data: [{ deleted_at: "2026-05-16T15:25:07.259+00:00", name: null }],
    });
    const result = await resolveInsertError(
      { code: "23505", message: "duplicate key value" },
      "anon@example.com"
    );
    expect(result).toBe(
      "A contact with this email was previously deleted. Restore them in the Supabase dashboard to re-add."
    );
  });
});

describe("checkDuplicateEmails", () => {
  it("returns empty set for empty input without querying Supabase", async () => {
    const result = await checkDuplicateEmails([]);
    expect(result.size).toBe(0);
    expect(inMock).not.toHaveBeenCalled();
  });

  it("returns empty set when no emails match", async () => {
    inMock.mockResolvedValueOnce({ data: [] });
    const result = await checkDuplicateEmails(["test@example.com"]);
    expect(result.size).toBe(0);
  });

  it("returns matched emails as a Set", async () => {
    inMock.mockResolvedValueOnce({ data: [{ email: "jane@example.com" }] });
    const result = await checkDuplicateEmails(["jane@example.com", "bob@example.com"]);
    expect(result.has("jane@example.com")).toBe(true);
    expect(result.has("bob@example.com")).toBe(false);
  });

  it("handles null data gracefully", async () => {
    inMock.mockResolvedValueOnce({ data: null });
    const result = await checkDuplicateEmails(["x@example.com"]);
    expect(result.size).toBe(0);
  });

  it("deduplicates and trims input emails before querying", async () => {
    inMock.mockResolvedValueOnce({ data: [] });
    await checkDuplicateEmails(["  a@example.com  ", "a@example.com"]);
    const calledWith = (inMock.mock.calls[0] as [string, string[]])[1];
    expect(calledWith).toEqual(["a@example.com"]);
  });
});
