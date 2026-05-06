import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

// ── Mock the supabase chain ──────────────────────────────────────────────────
// reads:  from(...).select(...).order(...).limit(...) → { data, error }
// writes: from(...).update(...).eq(...).select().single() → { data, error }

const limitMock = vi.fn();
const updateEqSelectSingleMock = vi.fn();

vi.mock("@/lib/supabase", () => ({
  supabase: {
    from: vi.fn(() => ({
      select: vi.fn(() => ({
        order: vi.fn(() => ({
          limit: limitMock,
        })),
      })),
      update: vi.fn(() => ({
        eq: vi.fn(() => ({
          select: vi.fn(() => ({
            single: updateEqSelectSingleMock,
          })),
        })),
      })),
    })),
  },
}));

import { ContactsList } from "./ContactsList";
import type { Contact } from "@/lib/types";

const dana: Contact = {
  id: "1",
  name: "Dana Ehrlich",
  email: "dana@clearbond.com",
  company: "Clearbond",
  role: "CEO",
  detail: "customs SaaS",
  tier: 1,
  mode: "outreach",
  stage: "first_touch_sent",
  reply_status: "no_reply",
  dartmouth: true,
  job_title: null,
  job_description: null,
  company_applied: null,
  applied_date: null,
  followup_date: "2026-05-10",
  notes: null,
  created_at: "2026-05-01T10:00:00Z",
};

const sarah: Contact = {
  ...dana,
  id: "2",
  name: "Sarah Kim",
  email: "sarah@stripe.com",
  company: "Stripe",
  mode: "applied",
  stage: "applied_intro_drafted",
  job_title: "Senior PM",
  applied_date: "2026-04-21",
  job_description: "Lead a small team...",
  reply_status: "replied",
};

beforeEach(() => {
  limitMock.mockReset();
  updateEqSelectSingleMock.mockReset();
});

describe("ContactsList — empty state", () => {
  it("shows empty-state copy when there are no contacts", async () => {
    limitMock.mockResolvedValue({ data: [], error: null });

    render(
      <ContactsList refreshKey={0} onError={() => {}} onUpdated={() => {}} />
    );

    await waitFor(() =>
      expect(screen.getByText(/No contacts yet/i)).toBeInTheDocument()
    );
  });
});

describe("ContactsList — table rendering", () => {
  it("renders a row per contact", async () => {
    limitMock.mockResolvedValue({ data: [dana, sarah], error: null });

    render(
      <ContactsList refreshKey={0} onError={() => {}} onUpdated={() => {}} />
    );

    await waitFor(() => screen.getByText("Dana Ehrlich"));
    expect(screen.getByText("Dana Ehrlich")).toBeInTheDocument();
    expect(screen.getByText("Sarah Kim")).toBeInTheDocument();
    expect(screen.getByText("Clearbond")).toBeInTheDocument();
    expect(screen.getByText("Stripe")).toBeInTheDocument();
  });

  it("forwards Supabase errors to onError", async () => {
    limitMock.mockResolvedValue({ data: null, error: { message: "boom" } });

    const onError = vi.fn();
    render(
      <ContactsList refreshKey={0} onError={onError} onUpdated={() => {}} />
    );

    await waitFor(() =>
      expect(onError).toHaveBeenCalledWith(expect.stringMatching(/boom/i))
    );
  });
});

describe("ContactsList — side panel", () => {
  it("opens the side panel when a row is clicked", async () => {
    const user = userEvent.setup();
    limitMock.mockResolvedValue({ data: [dana], error: null });

    render(
      <ContactsList refreshKey={0} onError={() => {}} onUpdated={() => {}} />
    );

    await waitFor(() => screen.getByText("Dana Ehrlich"));
    await user.click(screen.getByText("Dana Ehrlich"));

    // Panel shows the email + the Update Status block (heading + button match,
    // so use the role-specific button query).
    expect(
      screen.getByRole("button", { name: /Update Status/i })
    ).toBeInTheDocument();
    // Check that detail panel rendered the email value
    expect(screen.getAllByText("dana@clearbond.com").length).toBeGreaterThan(0);
  });

  it("offers applied-mode stages when contact mode=applied", async () => {
    const user = userEvent.setup();
    limitMock.mockResolvedValue({ data: [sarah], error: null });

    render(
      <ContactsList refreshKey={0} onError={() => {}} onUpdated={() => {}} />
    );

    await waitFor(() => screen.getByText("Sarah Kim"));
    await user.click(screen.getByText("Sarah Kim"));

    const stageSelect = screen.getAllByRole("combobox")[0] as HTMLSelectElement;
    const optionValues = Array.from(stageSelect.options).map((o) => o.value);
    expect(optionValues).toContain("applied_intro_sent");
    expect(optionValues).toContain("applied_followup_sent");
    expect(optionValues).not.toContain("first_touch_sent"); // outreach-only
  });

  it("calls update + onSaved when the Update Status button is clicked", async () => {
    const user = userEvent.setup();
    limitMock.mockResolvedValue({ data: [dana], error: null });
    const updated = { ...dana, stage: "first_touch_sent", reply_status: "replied" };
    updateEqSelectSingleMock.mockResolvedValue({ data: updated, error: null });

    const onUpdated = vi.fn();
    render(
      <ContactsList refreshKey={0} onError={() => {}} onUpdated={onUpdated} />
    );

    await waitFor(() => screen.getByText("Dana Ehrlich"));
    await user.click(screen.getByText("Dana Ehrlich"));

    await user.click(screen.getByRole("button", { name: /Update Status/i }));

    await waitFor(() => expect(updateEqSelectSingleMock).toHaveBeenCalled());
    expect(onUpdated).toHaveBeenCalled();
  });

  it("propagates update errors via onError", async () => {
    const user = userEvent.setup();
    limitMock.mockResolvedValue({ data: [dana], error: null });
    updateEqSelectSingleMock.mockResolvedValue({
      data: null,
      error: { message: "permission denied" },
    });

    const onError = vi.fn();
    render(
      <ContactsList refreshKey={0} onError={onError} onUpdated={() => {}} />
    );

    await waitFor(() => screen.getByText("Dana Ehrlich"));
    await user.click(screen.getByText("Dana Ehrlich"));

    await user.click(screen.getByRole("button", { name: /Update Status/i }));

    await waitFor(() =>
      expect(onError).toHaveBeenCalledWith(
        expect.stringMatching(/permission denied/i)
      )
    );
  });

  it("closes the panel when the close button is clicked", async () => {
    const user = userEvent.setup();
    limitMock.mockResolvedValue({ data: [dana], error: null });

    render(
      <ContactsList refreshKey={0} onError={() => {}} onUpdated={() => {}} />
    );

    await waitFor(() => screen.getByText("Dana Ehrlich"));
    await user.click(screen.getByText("Dana Ehrlich"));
    expect(
      screen.getByRole("button", { name: /Update Status/i })
    ).toBeInTheDocument();

    await user.click(screen.getByLabelText(/Close panel/i));
    expect(
      screen.queryByRole("button", { name: /Update Status/i })
    ).toBeNull();
  });
});
