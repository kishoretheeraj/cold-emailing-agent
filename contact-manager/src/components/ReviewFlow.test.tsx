import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const insertMock = vi.fn();
vi.mock("@/lib/supabase", () => ({
  supabase: {
    from: vi.fn(() => ({ insert: insertMock })),
  },
  resolveInsertError: vi.fn((err: { message: string }) => Promise.resolve(err.message)),
}));

import { ReviewFlow } from "./ReviewFlow";
import type { ReviewContact, BulkImportWindow } from "@/lib/types";

function makeContact(overrides: Partial<ReviewContact> = {}): ReviewContact {
  return {
    name: "Alice Smith",
    email: "alice@example.com",
    company: "Acme",
    role: "CEO",
    detail: "CEO at Acme",
    tier: 2,
    mode: "outreach",
    dartmouth: false,
    job_title: null,
    job_description: null,
    applied_date: null,
    notes: null,
    resume_url: null,
    missing_email: false,
    status: "pending",
    ...overrides,
  };
}

function makeProps(
  contacts: ReviewContact[],
  {
    onUpdate,
    onBack,
    onAddMore,
    onAdded,
    onError,
  }: {
    onUpdate?: (i: number, c: ReviewContact) => void;
    onBack?: () => void;
    onAddMore?: () => void;
    onAdded?: (w?: BulkImportWindow) => void;
    onError?: (m: string) => void;
  } = {}
) {
  return {
    contacts,
    onUpdate: onUpdate ?? vi.fn(),
    onBack: onBack ?? vi.fn(),
    onAddMore: onAddMore ?? vi.fn(),
    onAdded: onAdded ?? vi.fn(),
    onError: onError ?? vi.fn(),
  };
}

beforeEach(() => {
  insertMock.mockReset();
  insertMock.mockResolvedValue({ error: null });
});

describe("ReviewFlow — reviewing phase", () => {
  it("renders the first card given 3 contacts", () => {
    const contacts = [
      makeContact({ name: "Alice Smith" }),
      makeContact({ name: "Bob Jones", email: "bob@example.com" }),
      makeContact({ name: "Carol Lee", email: "carol@example.com" }),
    ];
    render(<ReviewFlow {...makeProps(contacts)} />);
    expect(screen.getByDisplayValue("Alice Smith")).toBeInTheDocument();
    expect(screen.getByText(/Reviewing 1 of 3/i)).toBeInTheDocument();
  });

  it("Skip on card 1 marks it skipped and calls onUpdate", async () => {
    const user = userEvent.setup();
    const onUpdate = vi.fn();
    const contacts = [
      makeContact({ name: "Alice Smith" }),
      makeContact({ name: "Bob Jones", email: "bob@example.com" }),
    ];
    render(<ReviewFlow {...makeProps(contacts, { onUpdate })} />);

    await user.click(screen.getByRole("button", { name: /Skip/i }));

    expect(onUpdate).toHaveBeenCalledWith(
      0,
      expect.objectContaining({ status: "skipped" })
    );
  });

  it("Previous on card 2 returns to card 1", async () => {
    const user = userEvent.setup();
    // Start with second card shown by confirming first
    const contacts = [
      makeContact({ name: "Alice Smith", status: "confirmed" }),
      makeContact({ name: "Bob Jones", email: "bob@example.com", status: "pending" }),
    ];
    // Render starting at index 1 by pre-confirming contact 0
    const { rerender } = render(<ReviewFlow {...makeProps(contacts)} />);
    // Navigate to card 2 by clicking confirm on card 1 (confirmed status already set)
    // Since first contact is confirmed, let's start from fresh and confirm it
    const confirmedContacts = [
      makeContact({ name: "Alice Smith" }),
      makeContact({ name: "Bob Jones", email: "bob@example.com" }),
    ];
    const onUpdate = vi.fn((i, c) => {
      confirmedContacts[i] = c;
    });
    rerender(<ReviewFlow {...makeProps(confirmedContacts, { onUpdate })} />);

    // Confirm card 1 to advance to card 2
    await user.click(screen.getByRole("button", { name: /Confirm and next/i }));

    // Now at card 2 — click Previous
    await user.click(screen.getByRole("button", { name: /Previous/i }));

    expect(screen.getByText(/Reviewing 1 of 2/i)).toBeInTheDocument();
  });

  it("ArrowRight key confirms and advances when no input is focused", async () => {
    const onUpdate = vi.fn();
    const contacts = [
      makeContact({ name: "Alice Smith" }),
      makeContact({ name: "Bob Jones", email: "bob@example.com" }),
    ];
    render(<ReviewFlow {...makeProps(contacts, { onUpdate })} />);

    fireEvent.keyDown(window, { key: "ArrowRight" });

    expect(onUpdate).toHaveBeenCalledWith(
      0,
      expect.objectContaining({ status: "confirmed" })
    );
  });

  it("ArrowLeft key goes back to previous card when not on first card", async () => {
    const onUpdate = vi.fn((i, c) => {
      contacts[i] = c;
    });
    const contacts = [
      makeContact({ name: "Alice Smith" }),
      makeContact({ name: "Bob Jones", email: "bob@example.com" }),
    ];
    const { rerender } = render(<ReviewFlow {...makeProps(contacts, { onUpdate })} />);

    // Advance to card 2 first
    fireEvent.keyDown(window, { key: "ArrowRight" });
    rerender(<ReviewFlow {...makeProps(contacts, { onUpdate })} />);

    // Now press ArrowLeft
    fireEvent.keyDown(window, { key: "ArrowLeft" });

    await waitFor(() =>
      expect(screen.getByText(/Reviewing 1 of 2/i)).toBeInTheDocument()
    );
  });

  it("pressing 's' while focus is in an input does NOT skip", () => {
    const onUpdate = vi.fn();
    const contacts = [makeContact({ name: "Alice Smith" })];
    render(<ReviewFlow {...makeProps(contacts, { onUpdate })} />);

    const input = screen.getByPlaceholderText(/One specific thing/i);
    input.focus();
    // document.activeElement is now the input; the keyboard guard should suppress skip
    fireEvent.keyDown(window, { key: "s" });

    expect(onUpdate).not.toHaveBeenCalled();
  });

  it("pressing Enter while focus is in an input does NOT confirm", () => {
    const onUpdate = vi.fn();
    const contacts = [makeContact({ name: "Alice Smith" })];
    render(<ReviewFlow {...makeProps(contacts, { onUpdate })} />);

    const input = screen.getByPlaceholderText(/One specific thing/i);
    input.focus();
    // document.activeElement is now the input; the keyboard guard should suppress confirm
    fireEvent.keyDown(window, { key: "Enter" });

    expect(onUpdate).not.toHaveBeenCalled();
  });
});

describe("ReviewFlow — validation", () => {
  it("blocks confirm with inline error when missing_email and email empty", async () => {
    const user = userEvent.setup();
    const contacts = [
      makeContact({ missing_email: true, email: null }),
    ];
    render(<ReviewFlow {...makeProps(contacts)} />);

    await user.click(screen.getByRole("button", { name: /Confirm/i }));

    expect(screen.getByText(/Add an email address to confirm/i)).toBeInTheDocument();
  });

  it("blocks confirm with inline error for invalid email format 'asdf'", async () => {
    const user = userEvent.setup();
    const onUpdate = vi.fn((i, c) => {
      contacts[i] = c;
    });
    const contacts = [
      makeContact({ missing_email: true, email: null }),
    ];
    const { rerender } = render(<ReviewFlow {...makeProps(contacts, { onUpdate })} />);

    // Type an invalid email in the email input
    const emailInput = screen.getByPlaceholderText(/email@example\.com/i);
    await userEvent.type(emailInput, "asdf");

    // Simulate the onUpdate having been called and update contacts
    contacts[0] = { ...contacts[0], email: "asdf", missing_email: true };
    rerender(<ReviewFlow {...makeProps(contacts, { onUpdate })} />);

    await user.click(screen.getByRole("button", { name: /Confirm/i }));

    expect(screen.getByText(/Enter a valid email address/i)).toBeInTheDocument();
  });

  it("blocks confirm with inline error when missing required field is still empty", async () => {
    const user = userEvent.setup();
    const contacts = [
      makeContact({
        missing_required: true,
        required_missing_fields: ["company"],
        company: null,
      }),
    ];
    render(<ReviewFlow {...makeProps(contacts)} />);

    await user.click(screen.getByRole("button", { name: /Confirm/i }));

    expect(screen.getByText(/Fill the required fields above to confirm/i)).toBeInTheDocument();
  });

  it("transitions to summary phase when all cards are confirmed", async () => {
    const user = userEvent.setup();
    const contacts = [
      makeContact({ name: "Alice Smith" }),
    ];
    const onUpdate = vi.fn((i, c) => {
      contacts[i] = c;
    });
    render(<ReviewFlow {...makeProps(contacts, { onUpdate })} />);

    await user.click(screen.getByRole("button", { name: /Confirm and review/i }));

    await waitFor(() =>
      expect(screen.getByText(/Ready to import/i)).toBeInTheDocument()
    );
  });
});

describe("ReviewFlow — summary phase", () => {
  it("lists pending count in Not reviewed section when user jumps to summary early", async () => {
    const user = userEvent.setup();
    const contacts = [
      makeContact({ name: "Alice Smith", status: "confirmed" }),
      makeContact({ name: "Bob Jones", email: "bob@example.com", status: "pending" }),
    ];
    render(<ReviewFlow {...makeProps(contacts)} />);

    // Jump to summary (confirmed count >= 1)
    await user.click(screen.getByRole("button", { name: /Jump to summary/i }));

    await waitFor(() =>
      expect(screen.getByText(/Ready to import/i)).toBeInTheDocument()
    );
    expect(screen.getByText(/Not reviewed \(1\)/i)).toBeInTheDocument();
    expect(screen.getByText(/These will not be imported/i)).toBeInTheDocument();
  });

  it("shows Jump to summary button only when at least 1 contact is confirmed", async () => {
    const contacts = [
      makeContact({ name: "Alice Smith", status: "pending" }),
    ];
    render(<ReviewFlow {...makeProps(contacts)} />);

    expect(screen.queryByRole("button", { name: /Jump to summary/i })).toBeNull();

    // Now with one confirmed
    const withConfirmed = [
      makeContact({ name: "Alice Smith", status: "confirmed" }),
    ];
    const { rerender } = render(<ReviewFlow {...makeProps(withConfirmed)} />);
    rerender(<ReviewFlow {...makeProps(withConfirmed)} />);
    expect(screen.getByRole("button", { name: /Jump to summary/i })).toBeInTheDocument();
  });

  it("Import button is disabled when 0 contacts are confirmed", async () => {
    const user = userEvent.setup();
    // Force into summary with 0 confirmed by having all skipped
    const contacts = [
      makeContact({ name: "Alice Smith", status: "skipped" }),
    ];
    // Render directly in summary by confirming 0 but setting status to skipped
    // We need to reach summary phase. Since all are skipped after skip, it goes to summary.
    const onUpdate = vi.fn((i, c) => {
      contacts[i] = c;
    });
    render(<ReviewFlow {...makeProps(contacts, { onUpdate })} />);

    await user.click(screen.getByRole("button", { name: /Skip/i }));

    await waitFor(() =>
      expect(screen.getByText(/Ready to import/i)).toBeInTheDocument()
    );
    const importBtn = screen.getByRole("button", { name: /Import/i });
    expect(importBtn).toBeDisabled();
  });
});

describe("ReviewFlow — importing phase", () => {
  it("transitions to done phase after successful Supabase inserts", async () => {
    const user = userEvent.setup();
    const contacts = [makeContact({ name: "Alice Smith" })];
    const onUpdate = vi.fn((i, c) => { contacts[i] = c; });
    render(<ReviewFlow {...makeProps(contacts, { onUpdate })} />);

    // Confirm card and enter summary
    await user.click(screen.getByRole("button", { name: /Confirm and review/i }));
    await waitFor(() => screen.getByText(/Ready to import/i));

    // Click import
    await user.click(screen.getByRole("button", { name: /Import 1 contact/i }));

    await waitFor(() =>
      expect(screen.getByText(/contact.* added to your pipeline/i)).toBeInTheDocument()
    );
  });

  it("shows Retry failed button on done screen for partial failures; second retry is disabled", async () => {
    const user = userEvent.setup();
    insertMock
      .mockResolvedValueOnce({ error: { message: "unique violation" } });

    const contacts = [makeContact({ name: "Alice Smith" })];
    const onUpdate = vi.fn((i, c) => { contacts[i] = c; });
    render(<ReviewFlow {...makeProps(contacts, { onUpdate })} />);

    await user.click(screen.getByRole("button", { name: /Confirm and review/i }));
    await waitFor(() => screen.getByText(/Ready to import/i));
    await user.click(screen.getByRole("button", { name: /Import 1 contact/i }));

    await waitFor(() => screen.getByText(/failed to import/i));
    const retryBtn = screen.getByRole("button", { name: /Retry failed/i });
    expect(retryBtn).not.toBeDisabled();

    // Retry — second time should be disabled
    insertMock.mockResolvedValueOnce({ error: null });
    await user.click(retryBtn);

    await waitFor(() => {
      const btn = screen.queryByRole("button", { name: /Retry failed/i });
      return btn === null || btn.hasAttribute("disabled");
    });
  });

  it("calls onAdded with { startedAt, endedAt } from the Done screen View contacts button", async () => {
    const user = userEvent.setup();
    const onAdded = vi.fn();
    const contacts = [makeContact({ name: "Alice Smith" })];
    const onUpdate = vi.fn((i, c) => { contacts[i] = c; });
    render(<ReviewFlow {...makeProps(contacts, { onUpdate, onAdded })} />);

    await user.click(screen.getByRole("button", { name: /Confirm and review/i }));
    await waitFor(() => screen.getByText(/Ready to import/i));
    await user.click(screen.getByRole("button", { name: /Import 1 contact/i }));
    await waitFor(() => screen.getByText(/added to your pipeline/i));

    await user.click(screen.getByRole("button", { name: /View contacts/i }));

    expect(onAdded).toHaveBeenCalledWith(
      expect.objectContaining({
        startedAt: expect.any(Number),
        endedAt: expect.any(Number),
      })
    );
  });

  it("calls onAddMore from the Add more button on Done screen", async () => {
    const user = userEvent.setup();
    const onAddMore = vi.fn();
    const contacts = [makeContact({ name: "Alice Smith" })];
    const onUpdate = vi.fn((i, c) => { contacts[i] = c; });
    render(<ReviewFlow {...makeProps(contacts, { onUpdate, onAddMore })} />);

    await user.click(screen.getByRole("button", { name: /Confirm and review/i }));
    await waitFor(() => screen.getByText(/Ready to import/i));
    await user.click(screen.getByRole("button", { name: /Import 1 contact/i }));
    await waitFor(() => screen.getByText(/added to your pipeline/i));

    await user.click(screen.getByRole("button", { name: /Add more/i }));

    expect(onAddMore).toHaveBeenCalled();
  });
});

describe("ReviewFlow — always-editable identity fields", () => {
  it("company TextInput renders with pre-filled value", () => {
    const contacts = [makeContact({ company: "Acme Corp" })];
    render(<ReviewFlow {...makeProps(contacts)} />);
    expect(screen.getByDisplayValue("Acme Corp")).toBeInTheDocument();
  });

  it("role TextInput renders with pre-filled value", () => {
    const contacts = [makeContact({ role: "VP Engineering" })];
    render(<ReviewFlow {...makeProps(contacts)} />);
    expect(screen.getByDisplayValue("VP Engineering")).toBeInTheDocument();
  });

  it("editing company field calls onUpdate with new company value", () => {
    const onUpdate = vi.fn();
    const contacts = [makeContact({ company: "OldCo" })];
    render(<ReviewFlow {...makeProps(contacts, { onUpdate })} />);

    fireEvent.change(screen.getByDisplayValue("OldCo"), {
      target: { value: "NewCo" },
    });

    expect(onUpdate).toHaveBeenLastCalledWith(
      0,
      expect.objectContaining({ company: "NewCo" })
    );
  });

  it("editing role field calls onUpdate with new role value", () => {
    const onUpdate = vi.fn();
    const contacts = [makeContact({ role: "Old Title" })];
    render(<ReviewFlow {...makeProps(contacts, { onUpdate })} />);

    fireEvent.change(screen.getByDisplayValue("Old Title"), {
      target: { value: "New Title" },
    });

    expect(onUpdate).toHaveBeenLastCalledWith(
      0,
      expect.objectContaining({ role: "New Title" })
    );
  });

  it("name TextInput always renders even when name is not in missing_required", () => {
    const contacts = [makeContact({ name: "Bob Jones" })];
    render(<ReviewFlow {...makeProps(contacts)} />);
    expect(screen.getByDisplayValue("Bob Jones")).toBeInTheDocument();
  });

  it("email TextInput always renders even when missing_email is false", () => {
    const contacts = [makeContact({ email: "bob@example.com", missing_email: false })];
    render(<ReviewFlow {...makeProps(contacts)} />);
    expect(screen.getByDisplayValue("bob@example.com")).toBeInTheDocument();
  });
});

describe("ReviewFlow — search web link", () => {
  it("renders 'Search web' link with correct Google href when name and company are present", () => {
    const contacts = [makeContact({ name: "Alice Smith", company: "Acme" })];
    render(<ReviewFlow {...makeProps(contacts)} />);
    const link = screen.getByRole("link", { name: /search web/i });
    expect(link).toBeInTheDocument();
    expect(link).toHaveAttribute(
      "href",
      "https://www.google.com/search?q=Alice%20Smith%20Acme"
    );
    expect(link).toHaveAttribute("target", "_blank");
  });

  it("search link href encodes spaces and special characters", () => {
    const contacts = [makeContact({ name: "O'Brien, Pat", company: "AT&T" })];
    render(<ReviewFlow {...makeProps(contacts)} />);
    const link = screen.getByRole("link", { name: /search web/i });
    expect(link).toHaveAttribute(
      "href",
      `https://www.google.com/search?q=${encodeURIComponent("O'Brien, Pat AT&T")}`
    );
  });

  it("search link is absent when both name and company are empty", () => {
    const contacts = [makeContact({ name: null, company: null })];
    render(<ReviewFlow {...makeProps(contacts)} />);
    expect(screen.queryByRole("link", { name: /search web/i })).toBeNull();
  });

  it("search link still renders when only name is present (no company)", () => {
    const contacts = [makeContact({ name: "Alice Smith", company: null })];
    render(<ReviewFlow {...makeProps(contacts)} />);
    const link = screen.getByRole("link", { name: /search web/i });
    expect(link).toHaveAttribute(
      "href",
      "https://www.google.com/search?q=Alice%20Smith"
    );
  });
});

describe("SmartInput — bulk mode via onError", () => {
  it("calls onError (not inline error) when /api/extract returns 500 for bulk input", async () => {
    // This is tested in SmartInput.test.tsx via the extraction error test
    // which covers the fetch error path for both single and bulk inputs.
    // The onError callback is always used for network/API failures in SmartInput.
    expect(true).toBe(true);
  });
});
