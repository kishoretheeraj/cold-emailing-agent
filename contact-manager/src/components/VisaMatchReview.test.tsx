import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const {
  companyIntelSelectMock,
  employerSelectMock,
  companyIntelUpdateEqMock,
  companyIntelUpdateMock,
} = vi.hoisted(() => ({
  companyIntelSelectMock: vi.fn(),
  employerSelectMock: vi.fn(),
  companyIntelUpdateEqMock: vi.fn(),
  companyIntelUpdateMock: vi.fn(),
}));

vi.mock("@/lib/supabase", () => ({
  supabase: {
    from: vi.fn((table: string) => {
      if (table === "company_intel") {
        return {
          select: vi.fn(() => ({
            eq: vi.fn(() => ({
              order: companyIntelSelectMock,
            })),
          })),
          update: companyIntelUpdateMock,
        };
      }
      if (table === "employer_h1b_stats") {
        return {
          select: vi.fn(() => ({
            in: employerSelectMock,
          })),
        };
      }
      throw new Error(`Unexpected table: ${table}`);
    }),
  },
}));

import { VisaMatchReview } from "./VisaMatchReview";

const needsReviewCompany = {
  id: 42,
  normalized_name: "acme indst",
  raw_company_names: ["Acme Indst"],
  match_status: "needs_review",
  top_candidates: [
    { employer_id: 7, normalized_name: "acme industries", score: 84 },
  ],
};

const employer = {
  id: 7,
  display_name: "Acme Industries Inc",
  lca_recent_2fy: 12,
  latest_filing_fy: 2025,
  approval_rate: 0.92,
};

beforeEach(() => {
  companyIntelSelectMock.mockReset();
  employerSelectMock.mockReset();
  companyIntelUpdateEqMock.mockReset();
  companyIntelUpdateMock.mockReset();
  companyIntelUpdateEqMock.mockResolvedValue({ error: null });
  companyIntelUpdateMock.mockImplementation(() => ({ eq: companyIntelUpdateEqMock }));
  employerSelectMock.mockResolvedValue({ data: [employer] });
});

describe("VisaMatchReview — empty queue", () => {
  it("shows 'Nothing to review' when the needs_review queue is empty", async () => {
    companyIntelSelectMock.mockResolvedValue({ data: [], error: null });
    render(<VisaMatchReview />);
    await waitFor(() => screen.getByText(/nothing to review/i));
  });
});

describe("VisaMatchReview — reviewing a candidate", () => {
  beforeEach(() => {
    companyIntelSelectMock.mockResolvedValue({ data: [needsReviewCompany], error: null });
  });

  it("renders the target company and proposed match", async () => {
    render(<VisaMatchReview />);
    await waitFor(() => screen.getByText("Acme Indst"));
    expect(screen.getByText("Acme Industries Inc")).toBeInTheDocument();
    expect(screen.getByText(/84% match/i)).toBeInTheDocument();
  });

  it("Confirm writes match_status=confirmed with denormalized employer stats", async () => {
    const user = userEvent.setup();
    render(<VisaMatchReview />);
    await waitFor(() => screen.getByText("Acme Indst"));

    await user.click(screen.getByRole("button", { name: /^confirm$/i }));

    await waitFor(() => expect(companyIntelUpdateMock).toHaveBeenCalled());
    const payload = companyIntelUpdateMock.mock.calls[0][0];
    expect(payload.match_status).toBe("confirmed");
    expect(payload.matched_employer_id).toBe(7);
    expect(payload.sponsors_h1b).toBe(true);
    expect(payload.h1b_recent_count).toBe(12);
    expect(companyIntelUpdateEqMock).toHaveBeenCalledWith("id", 42);
  });

  it("No match writes match_status=rejected and leaves sponsors_h1b null", async () => {
    const user = userEvent.setup();
    render(<VisaMatchReview />);
    await waitFor(() => screen.getByText("Acme Indst"));

    await user.click(screen.getByRole("button", { name: /no match/i }));

    await waitFor(() => expect(companyIntelUpdateMock).toHaveBeenCalled());
    const payload = companyIntelUpdateMock.mock.calls[0][0];
    expect(payload.match_status).toBe("rejected");
    expect(payload.sponsors_h1b).toBeNull();
    expect(payload.matched_employer_id).toBeNull();
  });

  it("Skip advances to the summary without writing to Supabase", async () => {
    const user = userEvent.setup();
    render(<VisaMatchReview />);
    await waitFor(() => screen.getByText("Acme Indst"));

    await user.click(screen.getByRole("button", { name: /^skip$/i }));

    await waitFor(() => screen.getByText(/review complete/i));
    expect(companyIntelUpdateMock).not.toHaveBeenCalled();
    const skippedCard = screen.getByText("Skipped").parentElement;
    expect(skippedCard?.textContent).toContain("1");
  });

  it("summary bucket counts reflect confirm/reject/skip outcomes", async () => {
    const user = userEvent.setup();
    render(<VisaMatchReview />);
    await waitFor(() => screen.getByText("Acme Indst"));
    await user.click(screen.getByRole("button", { name: /^confirm$/i }));
    await waitFor(() => screen.getByText(/review complete/i));

    // Confirmed count should read 1
    const confirmedCard = screen.getByText("Confirmed").parentElement;
    expect(confirmedCard?.textContent).toContain("1");
  });
});

describe("VisaMatchReview — multiple candidates", () => {
  it("'Try next candidate' cycles the displayed candidate without writing to Supabase", async () => {
    const twoCandidates = {
      ...needsReviewCompany,
      top_candidates: [
        { employer_id: 7, normalized_name: "acme industries", score: 84 },
        { employer_id: 8, normalized_name: "acme intl", score: 81 },
      ],
    };
    companyIntelSelectMock.mockResolvedValue({ data: [twoCandidates], error: null });
    employerSelectMock.mockResolvedValue({
      data: [employer, { ...employer, id: 8, display_name: "Acme International LLC" }],
    });

    const user = userEvent.setup();
    render(<VisaMatchReview />);
    await waitFor(() => screen.getByText("Acme Industries Inc"));

    await user.click(screen.getByRole("button", { name: /try next candidate/i }));
    await waitFor(() => screen.getByText("Acme International LLC"));
    expect(companyIntelUpdateMock).not.toHaveBeenCalled();
  });
});
