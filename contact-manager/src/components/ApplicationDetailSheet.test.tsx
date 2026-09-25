import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ApplicationDetailSheet } from "./ApplicationDetailSheet";
import type { JobApplication } from "@/lib/types";

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
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
    vi.fn((url: string, opts?: RequestInit) => {
      if (typeof url === "string" && url.includes("/files")) {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            resume_url: "https://signed.example/resume.pdf",
            resume_error: false,
            cover_letter_url: null,
            cover_letter_error: false,
          }),
        } as Response);
      }
      if (opts?.method === "PATCH") {
        const patchBody = JSON.parse((opts.body as string) ?? "{}");
        return Promise.resolve({
          ok: true,
          json: async () => ({ application: { ...baseApplication, ...patchBody } }),
        } as Response);
      }
      return Promise.resolve({ ok: true, json: async () => ({ application: {} }) } as Response);
    })
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

describe("ApplicationDetailSheet -- apply preview (U3/U11)", () => {
  const appWithAnswers: JobApplication = {
    ...baseApplication,
    apply_preview: {
      platform: "ashby",
      field_values: { name: "Kishore" },
      eligibility_answers: { "Authorized to work in the US?": "Yes" },
      screening_answers: { "Why this role?": "Because of the mission." },
    },
  };

  it("renders the platform and field values", async () => {
    render(<ApplicationDetailSheet application={appWithAnswers} onClose={() => {}} />);
    // Scoped to the platform line specifically -- baseApplication.company is "Ashby Co",
    // which also matches a bare /ashby/i against the sheet's <h2> title.
    expect(screen.getByText(/platform: ashby/i)).toBeInTheDocument();
    expect(screen.getByText(/Kishore/)).toBeInTheDocument();
  });

  it("renders screening and eligibility answers as editable fields", async () => {
    render(<ApplicationDetailSheet application={appWithAnswers} onClose={() => {}} />);
    expect(await screen.findByDisplayValue("Because of the mission.")).toBeInTheDocument();
    expect(screen.getByDisplayValue("Yes")).toBeInTheDocument();
  });

  it("edits a screening answer and saves it via PATCH with the merged apply_preview", async () => {
    const user = userEvent.setup();
    render(<ApplicationDetailSheet application={appWithAnswers} onClose={() => {}} />);
    const textarea = await screen.findByDisplayValue("Because of the mission.");
    await user.clear(textarea);
    await user.type(textarea, "Because I love the product.");
    await user.click(screen.getByRole("button", { name: /save changes/i }));

    await waitFor(() => {
      const patchCall = (global.fetch as ReturnType<typeof vi.fn>).mock.calls.find(
        ([url, opts]) => url === "/api/applications/5" && (opts as RequestInit | undefined)?.method === "PATCH"
      );
      expect(patchCall).toBeDefined();
      const body = JSON.parse((patchCall![1] as RequestInit).body as string);
      expect(body.apply_preview.screening_answers["Why this role?"]).toBe("Because I love the product.");
      expect(body.apply_preview.eligibility_answers["Authorized to work in the US?"]).toBe("Yes");
      expect(body.apply_preview.platform).toBe("ashby");
    });
  });

  it("shows a placeholder when there is no apply_preview yet", () => {
    render(
      <ApplicationDetailSheet application={{ ...baseApplication, apply_preview: null }} onClose={() => {}} />
    );
    expect(screen.getByText("No application preview yet.")).toBeInTheDocument();
  });

  it("calls onSaved with the PATCH response's application on a successful save (I7)", async () => {
    const user = userEvent.setup();
    const onSaved = vi.fn();
    render(<ApplicationDetailSheet application={appWithAnswers} onClose={() => {}} onSaved={onSaved} />);
    const textarea = await screen.findByDisplayValue("Because of the mission.");
    await user.clear(textarea);
    await user.type(textarea, "Because I love the product.");
    await user.click(screen.getByRole("button", { name: /save changes/i }));

    await waitFor(() => {
      expect(onSaved).toHaveBeenCalledWith(
        expect.objectContaining({
          apply_preview: expect.objectContaining({
            screening_answers: expect.objectContaining({ "Why this role?": "Because I love the product." }),
          }),
        })
      );
    });
  });

  it("reflects the saved answer once the parent re-renders with the onSaved value, not the stale original (I7)", async () => {
    const user = userEvent.setup();
    let current = appWithAnswers;
    const onSaved = vi.fn((updated: JobApplication) => {
      current = updated;
    });
    const { rerender } = render(
      <ApplicationDetailSheet application={current} onClose={() => {}} onSaved={onSaved} />
    );
    const textarea = await screen.findByDisplayValue("Because of the mission.");
    await user.clear(textarea);
    await user.type(textarea, "Because I love the product.");
    await user.click(screen.getByRole("button", { name: /save changes/i }));
    await waitFor(() => expect(onSaved).toHaveBeenCalled());

    rerender(<ApplicationDetailSheet application={current} onClose={() => {}} onSaved={onSaved} />);
    expect(screen.getByDisplayValue("Because I love the product.")).toBeInTheDocument();
    expect(screen.queryByDisplayValue("Because of the mission.")).not.toBeInTheDocument();
  });
});

describe("ApplicationDetailSheet -- pick and cost sections (U6/U9/U15)", () => {
  it("renders the pick verdict badge, score, and full reasoning", async () => {
    render(<ApplicationDetailSheet application={baseApplication} onClose={() => {}} />);
    expect(screen.getByText("strong")).toBeInTheDocument();
    expect(screen.getByText(/0\.9/)).toBeInTheDocument();
    expect(screen.getByText("Great fit.")).toBeInTheDocument();
  });

  it("shows a not-yet-scored placeholder when pick_verdict is null", () => {
    render(
      <ApplicationDetailSheet application={{ ...baseApplication, pick_verdict: null }} onClose={() => {}} />
    );
    expect(screen.getByText("Not yet scored.")).toBeInTheDocument();
  });

  it("renders cost fields when present", async () => {
    const withCost = {
      ...baseApplication,
      resume_cost_usd: 0.42,
      resume_tokens_input: 1000,
      resume_tokens_output: 500,
    };
    render(<ApplicationDetailSheet application={withCost} onClose={() => {}} />);
    expect(screen.getByText(/0\.4200/)).toBeInTheDocument();
    expect(screen.getByText(/1000/)).toBeInTheDocument();
    expect(screen.getByText(/500/)).toBeInTheDocument();
  });

  it("omits the cost section entirely when all cost fields are null", async () => {
    render(<ApplicationDetailSheet application={baseApplication} onClose={() => {}} />);
    expect(screen.queryByText("Cost")).not.toBeInTheDocument();
  });
});
