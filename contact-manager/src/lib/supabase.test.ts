import { describe, it, expect, vi, beforeEach } from "vitest";

// limitMock must be hoisted so it's accessible inside the vi.mock factory.
const { limitMock } = vi.hoisted(() => ({ limitMock: vi.fn() }));

// Mock @supabase/supabase-js so createClient() returns a controllable client.
// supabase.ts calls createClient at module load, so the mock must be here rather
// than mocking @/lib/supabase directly (which would replace resolveInsertError too).
vi.mock("@supabase/supabase-js", () => {
  const readChain: Record<string, unknown> = {};
  readChain.select = vi.fn(() => readChain);
  readChain.eq = vi.fn(() => readChain);
  readChain.limit = limitMock;
  return {
    createClient: vi.fn(() => ({ from: vi.fn(() => readChain) })),
  };
});

import { resolveInsertError } from "@/lib/supabase";

beforeEach(() => {
  limitMock.mockReset();
  limitMock.mockResolvedValue({ data: [] });
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
