import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ApplicationsPage } from "./ApplicationsPage";

vi.mock("@/components/ui/Tooltip", () => ({
  Tooltip: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));

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

const toastErrorMock = vi.fn();
const toastSuccessMock = vi.fn();
vi.mock("sonner", () => ({
  toast: {
    error: (...args: unknown[]) => toastErrorMock(...args),
    success: (...args: unknown[]) => toastSuccessMock(...args),
  },
}));

// I12: every fixture below includes the full JobApplication field set (source_channel,
// resume_cost_usd/resume_tokens_input/resume_tokens_output, approved_at) with plausible
// null/default values -- an earlier draft of this plan's Self-Review claimed this was already
// true and it wasn't. Harmless at runtime either way (the fetch mock is untyped), but keeping
// these complete avoids the fixtures silently drifting from the real JobApplication shape.
const sampleApplications = [
  { id: "1", contact_id: null, company: "Acme", role: "PM", job_url: null, source: "manual",
    source_channel: null, stage: "saved", applied_date: null, notes: null, posting_snapshot: null,
    resume_file_ref: null, cover_letter_file_ref: null,
    resume_cost_usd: null, resume_tokens_input: null, resume_tokens_output: null,
    pick_verdict: null, pick_score: null, pick_reasoning: null,
    apply_preview: null, apply_blocked_reason: null, approved_at: null,
    created_at: "2026-08-26T00:00:00Z", updated_at: "2026-08-26T00:00:00Z" },
  { id: "2", contact_id: null, company: "Globex", role: "Eng", job_url: null, source: "manual",
    source_channel: null, stage: "applied", applied_date: "2026-08-20", notes: null, posting_snapshot: null,
    resume_file_ref: null, cover_letter_file_ref: null,
    resume_cost_usd: null, resume_tokens_input: null, resume_tokens_output: null,
    pick_verdict: null, pick_score: null, pick_reasoning: null,
    apply_preview: null, apply_blocked_reason: null, approved_at: null,
    created_at: "2026-08-20T00:00:00Z", updated_at: "2026-08-20T00:00:00Z" },
];

const pickedApplication = {
  id: "3", contact_id: null, company: "LangChain", role: "PM", job_url: null, source: "jobright",
  source_channel: null, stage: "saved", applied_date: null, notes: null, posting_snapshot: null,
  resume_file_ref: null, cover_letter_file_ref: null,
  resume_cost_usd: null, resume_tokens_input: null, resume_tokens_output: null,
  pick_verdict: "strong", pick_score: 0.82, pick_reasoning: "Direct title match.",
  apply_preview: null, apply_blocked_reason: null, approved_at: null,
  created_at: "2026-08-30T00:00:00Z", updated_at: "2026-08-30T00:00:00Z",
};
const blockedApplication = {
  id: "4", contact_id: null, company: "Starz", role: "PM", job_url: null, source: "jobright",
  source_channel: null, stage: "saved", applied_date: null, notes: null, posting_snapshot: null,
  resume_file_ref: null, cover_letter_file_ref: null,
  resume_cost_usd: null, resume_tokens_input: null, resume_tokens_output: null,
  pick_verdict: null, pick_score: null, pick_reasoning: null,
  apply_preview: null, apply_blocked_reason: "workday -- permanently excluded", approved_at: null,
  created_at: "2026-08-30T00:00:00Z", updated_at: "2026-08-30T00:00:00Z",
};
const readyApplication = {
  id: "5", contact_id: null, company: "Ashby Co", role: "PM", job_url: "https://jobs.example/5",
  source: "jobright", source_channel: "ashby",
  stage: "ready_to_submit", applied_date: null, notes: null,
  posting_snapshot: { description: "Own the roadmap.", location: "Remote" },
  resume_file_ref: "resumes/5/resume.pdf", cover_letter_file_ref: "resumes/5/cl.pdf",
  resume_cost_usd: null, resume_tokens_input: null, resume_tokens_output: null,
  pick_verdict: "strong", pick_score: 0.9, pick_reasoning: "Great fit.",
  apply_preview: { platform: "ashby", field_values: { name: "Kishore" }, eligibility_answers: {}, screening_answers: { "Why this role?": "Because of the mission." } },
  apply_blocked_reason: null,
  approved_at: null,
  created_at: "2026-08-30T00:00:00Z", updated_at: "2026-08-30T00:00:00Z",
};

beforeEach(() => {
  toastErrorMock.mockClear();
  toastSuccessMock.mockClear();
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string, opts?: RequestInit) => {
      if (typeof url === "string" && url.includes("/files")) {
        return Promise.resolve({
          ok: true,
          json: async () => ({ resume_url: null, cover_letter_url: null }),
        } as Response);
      }
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
      if (opts?.method === "PATCH" && typeof opts.body === "string") {
        const patchBody = JSON.parse(opts.body);
        if (patchBody.apply_preview) {
          return Promise.resolve({
            ok: true,
            json: async () => ({ application: { ...readyApplication, ...patchBody } }),
          } as Response);
        }
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

  it("colors the pick-verdict badge by verdict", async () => {
    render(<ApplicationsPage />);
    const cell = await screen.findByText("LangChain");
    const row = cell.closest("tr") as HTMLElement;
    const badge = within(row).getByText("strong");
    expect(badge.className).toContain("emerald");
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

describe("ApplicationsPage -- detail sheet (U1/U2)", () => {
  it("opens the detail sheet with job details when View is clicked", async () => {
    const user = userEvent.setup();
    render(<ApplicationsPage />);
    await screen.findByText("Ashby Co");
    const row = screen.getByText("Ashby Co").closest("tr") as HTMLElement;
    await user.click(within(row).getByRole("button", { name: /view/i }));
    await waitFor(() => {
      expect(screen.getByTestId("sheet-content")).toBeInTheDocument();
    });
    expect(within(screen.getByTestId("sheet-content")).getByText("https://jobs.example/5")).toBeInTheDocument();
  });
});

describe("ApplicationsPage -- apply-preview save propagates to the table (I7)", () => {
  it("updates the table's screening-answer count after Save changes in the sheet", async () => {
    const user = userEvent.setup();
    render(<ApplicationsPage />);
    await screen.findByText("Ashby Co");
    const row = screen.getByText("Ashby Co").closest("tr") as HTMLElement;
    await user.click(within(row).getByRole("button", { name: /view/i }));
    const sheet = await screen.findByTestId("sheet-content");
    const textarea = await within(sheet).findByDisplayValue("Because of the mission.");
    await user.clear(textarea);
    await user.type(textarea, "Because I love the product.");
    await user.click(within(sheet).getByRole("button", { name: /save changes/i }));

    await waitFor(() => {
      expect(within(row).getByText("1 screening answer(s)")).toBeInTheDocument();
    });
    // The count text existed before the save too (readyApplication already had one answer) --
    // the real assertion is that a second edit-and-save cycle starts from the saved value, not
    // the original. Re-open the sheet and confirm the textarea now shows the saved text.
    await user.click(within(row).getByRole("button", { name: /view/i }));
    expect(
      await within(await screen.findByTestId("sheet-content")).findByDisplayValue(
        "Because I love the product."
      )
    ).toBeInTheDocument();
  });
});
