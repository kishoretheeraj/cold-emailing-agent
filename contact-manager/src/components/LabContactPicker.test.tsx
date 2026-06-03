import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { LabContactPicker } from "./LabContactPicker";
import type { Contact } from "@/lib/types";

// ── Supabase mock ─────────────────────────────────────────────────────────────
// The picker does two kinds of queries on "contacts":
//   1. List: .select(LIST_COLUMNS).is().order()[.or()].limit()  → limitMock
//   2. Full: .select("*").eq("id", id).single()                → singleMock

const { limitMock, singleMock } = vi.hoisted(() => ({
  limitMock: vi.fn(),
  singleMock: vi.fn(),
}));

vi.mock("@/lib/supabase", () => {
  // Shared chain object — supports both list and single-record paths
  const chain: Record<string, unknown> = {};
  for (const m of ["is", "order", "or"]) {
    chain[m] = vi.fn(() => chain);
  }
  // eq() leads to the single() path (used by full-record fetch)
  chain.eq = vi.fn(() => ({ single: singleMock }));
  chain.limit = limitMock;

  return {
    supabase: {
      from: vi.fn(() => ({ select: vi.fn(() => chain) })),
    },
  };
});

// ── Fixtures ───────────────────────────────────────────────────────────────────

const CONTACT: Contact = {
  id: "1",
  name: "Alice Chen",
  email: "alice@acme.com",
  company: "Acme Corp",
  role: "VP Engineering",
  detail: null,
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

beforeEach(() => {
  limitMock.mockResolvedValue({ data: [CONTACT] });
  singleMock.mockResolvedValue({ data: { ...CONTACT, detail: "Full detail" } });
});

// ── Tests ──────────────────────────────────────────────────────────────────────

describe("LabContactPicker", () => {
  it("renders collapsed toggle button when no contact selected", () => {
    render(<LabContactPicker selectedContact={null} onSelect={vi.fn()} />);
    expect(screen.getByTestId("contact-picker-toggle")).toBeInTheDocument();
    expect(screen.getByText(/select a contact/i)).toBeInTheDocument();
  });

  it("shows contact summary when a contact is selected", () => {
    render(<LabContactPicker selectedContact={CONTACT} onSelect={vi.fn()} />);
    expect(screen.getByText("Alice Chen at Acme Corp")).toBeInTheDocument();
  });

  it("expands dropdown on click", async () => {
    render(<LabContactPicker selectedContact={null} onSelect={vi.fn()} />);
    fireEvent.click(screen.getByTestId("contact-picker-toggle"));
    await waitFor(() => {
      expect(screen.getByPlaceholderText(/search contacts/i)).toBeInTheDocument();
    });
  });

  it("displays loaded contacts in dropdown", async () => {
    render(<LabContactPicker selectedContact={null} onSelect={vi.fn()} />);
    fireEvent.click(screen.getByTestId("contact-picker-toggle"));
    await waitFor(() => {
      expect(screen.getByText("Alice Chen")).toBeInTheDocument();
    });
  });

  it("calls onSelect after picking a contact", async () => {
    const onSelect = vi.fn();
    render(<LabContactPicker selectedContact={null} onSelect={onSelect} />);
    fireEvent.click(screen.getByTestId("contact-picker-toggle"));
    await waitFor(() => screen.getByText("Alice Chen"));
    fireEvent.click(screen.getByText("Alice Chen"));
    await waitFor(() => {
      expect(onSelect).toHaveBeenCalledWith(
        expect.objectContaining({ name: "Alice Chen" })
      );
    });
  });

  it("closes dropdown after selection", async () => {
    render(<LabContactPicker selectedContact={null} onSelect={vi.fn()} />);
    fireEvent.click(screen.getByTestId("contact-picker-toggle"));
    await waitFor(() => screen.getByText("Alice Chen"));
    fireEvent.click(screen.getByText("Alice Chen"));
    await waitFor(() => {
      expect(
        screen.queryByPlaceholderText(/search contacts/i)
      ).not.toBeInTheDocument();
    });
  });
});
