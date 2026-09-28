import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ApplicationsPage } from "./ApplicationsPage";

vi.mock("@/components/ui/Tooltip", () => ({
  Tooltip: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));

const toastErrorMock = vi.fn();
const toastSuccessMock = vi.fn();
vi.mock("sonner", () => ({
  toast: {
    error: (...args: unknown[]) => toastErrorMock(...args),
    success: (...args: unknown[]) => toastSuccessMock(...args),
  },
}));

const sampleApplications = [
  { id: "1", contact_id: null, company: "Acme", role: "PM", job_url: null, source: "manual",
    stage: "saved", applied_date: null, notes: null, posting_snapshot: null,
    created_at: "2026-08-26T00:00:00Z", updated_at: "2026-08-26T00:00:00Z" },
  { id: "2", contact_id: null, company: "Globex", role: "Eng", job_url: null, source: "manual",
    stage: "applied", applied_date: "2026-08-20", notes: null, posting_snapshot: null,
    created_at: "2026-08-20T00:00:00Z", updated_at: "2026-08-20T00:00:00Z" },
];

const pickedApplication = {
  id: "3", contact_id: null, company: "LangChain", role: "PM", job_url: null, source: "jobright",
  stage: "saved", applied_date: null, notes: null, posting_snapshot: null,
  resume_file_ref: null, cover_letter_file_ref: null,
  pick_verdict: "strong", pick_score: 0.82, pick_reasoning: "Direct title match.",
  apply_preview: null, apply_blocked_reason: null,
  created_at: "2026-08-30T00:00:00Z", updated_at: "2026-08-30T00:00:00Z",
};
const blockedApplication = {
  id: "4", contact_id: null, company: "Starz", role: "PM", job_url: null, source: "jobright",
  stage: "saved", applied_date: null, notes: null, posting_snapshot: null,
  resume_file_ref: null, cover_letter_file_ref: null,
  pick_verdict: null, pick_score: null, pick_reasoning: null,
  apply_preview: null, apply_blocked_reason: "workday -- permanently excluded",
  created_at: "2026-08-30T00:00:00Z", updated_at: "2026-08-30T00:00:00Z",
};
const readyApplication = {
  id: "5", contact_id: null, company: "Ashby Co", role: "PM", job_url: null, source: "jobright",
  stage: "ready_to_submit", applied_date: null, notes: null, posting_snapshot: null,
  resume_file_ref: "resumes/5/resume.pdf", cover_letter_file_ref: "resumes/5/cl.pdf",
  pick_verdict: "strong", pick_score: 0.9, pick_reasoning: "Great fit.",
  apply_preview: { platform: "ashby", field_values: { name: "Kishore" }, eligibility_answers: {}, screening_answers: { "Why this role?": "Because of the mission." } },
  apply_blocked_reason: null,
  created_at: "2026-08-30T00:00:00Z", updated_at: "2026-08-30T00:00:00Z",
};

beforeEach(() => {
  toastErrorMock.mockClear();
  toastSuccessMock.mockClear();
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string, opts?: RequestInit) => {
      if (!opts || opts.method === undefined) {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            applications: [
              ...sampleApplications,
              pickedApplication,
              blockedApplication,
              readyApplication,
            ],
          }),
        } as Response);
      }
      return Promise.resolve({
        ok: true,
        json: async () => ({ application: { ...sampleApplications[0], stage: "applied" } }),
      } as Response);
    })
  );
});

describe("ApplicationsPage", () => {
  it("renders fetched applications", async () => {
    render(<ApplicationsPage />);
    expect(await screen.findByText("Acme")).toBeInTheDocument();
    expect(screen.getByText("Globex")).toBeInTheDocument();
  });

  it("adds a new application via the form", async () => {
    const user = userEvent.setup();
    render(<ApplicationsPage />);
    await screen.findByText("Acme");
    await user.type(screen.getByLabelText("Company"), "NewCo");
    await user.type(screen.getByLabelText("Role"), "Designer");
    await user.click(screen.getByRole("button", { name: /add application/i }));
    await waitFor(() => {
      expect(global.fetch).toHaveBeenCalledWith(
        "/api/applications",
        expect.objectContaining({ method: "POST" })
      );
    });
  });

  it("shows an error and does not submit when company and role are blank", async () => {
    const user = userEvent.setup();
    render(<ApplicationsPage />);
    await screen.findByText("Acme");
    await user.click(screen.getByRole("button", { name: /add application/i }));
    await waitFor(() => {
      expect(toastErrorMock).toHaveBeenCalledWith("Company and role are required");
    });
    expect(global.fetch).not.toHaveBeenCalledWith(
      "/api/applications",
      expect.objectContaining({ method: "POST" })
    );
  });
});

describe("ApplicationsPage -- pipeline visibility", () => {
  it("shows a pick-verdict badge for a scored row", async () => {
    render(<ApplicationsPage />);
    const cell = await screen.findByText("LangChain");
    const row = cell.closest("tr");
    expect(row).not.toBeNull();
    expect(within(row as HTMLElement).getByText("strong")).toBeInTheDocument();
  });

  it("shows the blocked reason for an excluded row", async () => {
    render(<ApplicationsPage />);
    await screen.findByText("Starz");
    expect(screen.getByText(/workday -- permanently excluded/i)).toBeInTheDocument();
  });

  it("shows an Approve & Submit button for a ready_to_submit row", async () => {
    render(<ApplicationsPage />);
    await screen.findByText("Ashby Co");
    expect(screen.getByRole("button", { name: /approve & submit/i })).toBeInTheDocument();
  });

  it("clicking Approve & Submit posts to the submit route", async () => {
    const user = userEvent.setup();
    render(<ApplicationsPage />);
    await screen.findByText("Ashby Co");
    await user.click(screen.getByRole("button", { name: /approve & submit/i }));
    await waitFor(() => {
      expect(global.fetch).toHaveBeenCalledWith(
        "/api/applications/5/submit",
        expect.objectContaining({ method: "POST" })
      );
    });
  });
});
