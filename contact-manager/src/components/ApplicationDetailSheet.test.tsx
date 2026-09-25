import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { ApplicationDetailSheet } from "./ApplicationDetailSheet";
import type { JobApplication } from "@/lib/types";

vi.mock("vaul", () => ({
  Drawer: {
    Root: ({ children, open }: { children: React.ReactNode; open?: boolean }) =>
      open ? <>{children}</> : null,
    Portal: ({ children }: { children: React.ReactNode }) => <>{children}</>,
    Overlay: () => <div />,
    Content: ({ children }: { children: React.ReactNode }) => (
      <div role="dialog" data-testid="sheet-content">{children}</div>
    ),
    Trigger: ({ children }: { children: React.ReactNode }) => <>{children}</>,
    Close: ({
      children,
      onClick,
      "aria-label": ariaLabel,
    }: {
      children?: React.ReactNode;
      onClick?: () => void;
      "aria-label"?: string;
    }) => (
      <button type="button" onClick={onClick} aria-label={ariaLabel}>
        {children}
      </button>
    ),
    Title: ({ children }: { children: React.ReactNode }) => <h2>{children}</h2>,
    Description: ({ children }: { children: React.ReactNode }) => <p>{children}</p>,
  },
}));

const baseApplication: JobApplication = {
  id: "5",
  contact_id: null,
  company: "Ashby Co",
  role: "PM",
  job_url: "https://jobs.example/5",
  source: "jobright",
  source_channel: null,
  stage: "ready_to_submit",
  applied_date: null,
  notes: null,
  posting_snapshot: { description: "Build things.", location: "Remote" },
  resume_file_ref: "resumes/5/resume.pdf",
  cover_letter_file_ref: null,
  resume_cost_usd: null,
  resume_tokens_input: null,
  resume_tokens_output: null,
  pick_verdict: "strong",
  pick_score: 0.9,
  pick_reasoning: "Great fit.",
  apply_preview: {
    platform: "ashby",
    field_values: {},
    eligibility_answers: {},
    screening_answers: {},
  },
  apply_blocked_reason: null,
  approved_at: null,
  created_at: "2026-08-30T00:00:00Z",
  updated_at: "2026-08-30T00:00:00Z",
};

beforeEach(() => {
  vi.stubGlobal(
    "fetch",
    vi.fn(() =>
      Promise.resolve({
        ok: true,
        json: async () => ({
          resume_url: "https://signed.example/resume.pdf",
          resume_error: false,
          cover_letter_url: null,
          cover_letter_error: false,
        }),
      } as Response)
    )
  );
});

describe("ApplicationDetailSheet", () => {
  it("renders nothing when application is null", () => {
    render(<ApplicationDetailSheet application={null} onClose={() => {}} />);
    expect(screen.queryByTestId("sheet-content")).not.toBeInTheDocument();
  });

  it("renders the job url and posting_snapshot fields, including location", async () => {
    render(<ApplicationDetailSheet application={baseApplication} onClose={() => {}} />);
    expect(screen.getByText("https://jobs.example/5")).toBeInTheDocument();
    expect(screen.getByText("Build things.")).toBeInTheDocument();
    expect(screen.getByText("Remote")).toBeInTheDocument();
  });

  it("renders the resume iframe once the signed url loads", async () => {
    render(<ApplicationDetailSheet application={baseApplication} onClose={() => {}} />);
    await waitFor(() => {
      expect(screen.getByTitle("Resume")).toHaveAttribute("src", "https://signed.example/resume.pdf");
    });
  });

  it("shows a fallback message when a file ref is missing", async () => {
    render(<ApplicationDetailSheet application={baseApplication} onClose={() => {}} />);
    await waitFor(() => {
      expect(screen.getByText("No cover letter on file yet.")).toBeInTheDocument();
    });
  });

  it("fetches files for the given application id", async () => {
    render(<ApplicationDetailSheet application={baseApplication} onClose={() => {}} />);
    await waitFor(() => {
      expect(global.fetch).toHaveBeenCalledWith("/api/applications/5/files");
    });
  });

  it("shows a fallback message when no job url is present", () => {
    render(
      <ApplicationDetailSheet application={{ ...baseApplication, job_url: null }} onClose={() => {}} />
    );
    expect(screen.getByText("No job URL on file.")).toBeInTheDocument();
  });

  it("shows a distinct error state (I11) when signing the resume fails, never conflated with 'no file on record'", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve({
          ok: true,
          json: async () => ({
            resume_url: null,
            resume_error: true,
            cover_letter_url: null,
            cover_letter_error: false,
          }),
        } as Response)
      )
    );
    render(<ApplicationDetailSheet application={baseApplication} onClose={() => {}} />);
    await waitFor(() => {
      expect(screen.getByText(/couldn't load.*resume/i)).toBeInTheDocument();
    });
    expect(screen.queryByText("No resume on file yet.")).not.toBeInTheDocument();
    // The cover letter ref is genuinely absent on baseApplication -- that must still render
    // the ordinary "missing" copy, not the error copy, confirming the two states don't bleed.
    expect(screen.getByText("No cover letter on file yet.")).toBeInTheDocument();
  });

  it("renders an array-valued posting_snapshot field as a list instead of dropping it (M13)", () => {
    render(
      <ApplicationDetailSheet
        application={{
          ...baseApplication,
          posting_snapshot: {
            description: "Build things.",
            location: "Remote",
            responsibilities: ["Own the roadmap.", "Ship weekly."],
          },
        }}
        onClose={() => {}}
      />
    );
    expect(screen.getByText("Own the roadmap.")).toBeInTheDocument();
    expect(screen.getByText("Ship weekly.")).toBeInTheDocument();
  });
});
