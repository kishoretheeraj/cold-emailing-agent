import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, within, act, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ApplicationsPage } from "./ApplicationsPage";

// @testing-library/dom's waitFor/findBy* only detect Jest fake timers (it gates on
// `typeof jest`), not Vitest's -- under vi.useFakeTimers() its fallback setInterval/setTimeout
// polling is itself silently mocked and never fires, hanging forever even when the awaited
// condition is already true. The fake-timer tests below use fireEvent + explicit act() flushes
// instead (same precedent as QueuePage.test.tsx / RepliesPage.test.tsx). This safety net
// guards against a left-over vi.useFakeTimers() (e.g. from a test that threw before its own
// vi.useRealTimers() call) silently hanging an unrelated later test in this file.
afterEach(() => {
  vi.useRealTimers();
});

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

vi.mock("@radix-ui/react-select", async () => {
  const actual = await vi.importActual<typeof import("react")>("react");
  const SelectCtx = actual.createContext<{ onValueChange?: (v: string) => void }>({});
  return {
    Root: ({
      children,
      value,
      onValueChange,
    }: {
      children: React.ReactNode;
      value?: string;
      onValueChange?: (v: string) => void;
    }) => (
      <SelectCtx.Provider value={{ onValueChange }}>
        <div data-select-value={value}>{children}</div>
      </SelectCtx.Provider>
    ),
    Trigger: ({ children }: { children: React.ReactNode }) => <button type="button">{children}</button>,
    Value: () => null,
    Icon: ({ children }: { children: React.ReactNode }) => <>{children}</>,
    Portal: ({ children }: { children: React.ReactNode }) => <>{children}</>,
    Content: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
    Viewport: ({ children }: { children: React.ReactNode }) => <>{children}</>,
    Group: ({ children }: { children: React.ReactNode }) => <>{children}</>,
    Label: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
    Item: ({ children, value }: { children: React.ReactNode; value: string }) => {
      const ctx = actual.useContext(SelectCtx);
      return (
        <div role="option" data-value={value} onClick={() => ctx.onValueChange?.(value)}>
          {children}
        </div>
      );
    },
    ItemText: ({ children }: { children: React.ReactNode }) => <>{children}</>,
    ItemIndicator: () => null,
    Separator: () => <hr />,
  };
});

vi.mock("@radix-ui/react-dialog", () => ({
  Root: ({
    children,
    open,
    onOpenChange,
  }: {
    children: React.ReactNode;
    open?: boolean;
    onOpenChange?: (o: boolean) => void;
  }) =>
    open ? (
      <div data-testid="confirm-modal" onKeyDown={(e) => e.key === "Escape" && onOpenChange?.(false)}>
        {children}
      </div>
    ) : null,
  Portal: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  Overlay: () => <div />,
  Content: ({ children }: { children: React.ReactNode }) => (
    <div role="dialog" data-testid="confirm-content">
      {children}
    </div>
  ),
  Title: ({ children }: { children: React.ReactNode }) => <h2>{children}</h2>,
  Description: ({
    children,
    asChild,
  }: {
    children: React.ReactNode;
    asChild?: boolean;
  }) => (asChild ? <>{children}</> : <p>{children}</p>),
  Close: ({ children }: { children: React.ReactNode }) => <>{children}</>,
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
  automation_status: "ready_for_review", preview_revision_hash: "b".repeat(64), approved_revision_hash: null,
  created_at: "2026-08-30T00:00:00Z", updated_at: "2026-08-30T00:00:00Z",
};

beforeEach(() => {
  sessionStorage.clear();
  toastErrorMock.mockClear();
  toastSuccessMock.mockClear();
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string, opts?: RequestInit) => {
      if (typeof url === "string" && url.includes("/system-health")) {
        return Promise.resolve({ ok: true, json: async () => ({ health: [] }) } as Response);
      }
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

});

describe("ApplicationsPage -- Try again from a row's own apply_blocked_reason (C1)", () => {
  // A row can end up blocked in a PREVIOUS session (submit failed, tab closed or refreshed
  // before/without this tab's own polling ever observing it) -- blockedReasons (populated only
  // by this tab's client-side polling) must not be the only source for the Try again
  // affordance. This must show up purely from the API-loaded row's own apply_blocked_reason,
  // with blockedReasons empty (never populated -- no submit was ever triggered in this render).
  const previouslyBlockedApplication = {
    ...readyApplication,
    id: "6",
    company: "Previously Blocked Co",
    apply_blocked_reason: "submit() clicked Submit but found no confirmation on the page afterward",
    automation_status: "failed_retryable",
  };

  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn((url: string, opts?: RequestInit) => {
        if (typeof url === "string" && url.includes("/system-health")) {
          return Promise.resolve({ ok: true, json: async () => ({ health: [] }) } as Response);
        }
        if (typeof url === "string" && url.includes("/files")) {
          return Promise.resolve({
            ok: true,
            json: async () => ({ resume_url: null, cover_letter_url: null }),
          } as Response);
        }
        if (!opts || opts.method === undefined) {
          return Promise.resolve({
            ok: true,
            json: async () => ({ applications: [previouslyBlockedApplication] }),
          } as Response);
        }
        return Promise.resolve({ ok: true, json: async () => ({ ok: true }) } as Response);
      })
    );
  });

  it("shows the Try again affordance for a row whose apply_blocked_reason came from the API, not from client-side polling", async () => {
    render(<ApplicationsPage />);
    await screen.findByText("Previously Blocked Co");
    const row = screen.getByText("Previously Blocked Co").closest("tr") as HTMLElement;
    // The row's plain "Blocked" column and the Preview/Submit column's Try again state both
    // surface apply_blocked_reason text -- scope to the row and allow either/both, the point
    // here is the Try again button, not text uniqueness.
    expect(within(row).getAllByText(/no confirmation on the page afterward/i).length).toBeGreaterThan(0);
    expect(within(row).getByRole("button", { name: /try again/i })).toBeInTheDocument();
    // The plain Approve & Submit button must not also render for this row.
    expect(within(row).queryByRole("button", { name: /^approve & submit$/i })).not.toBeInTheDocument();
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

describe("ApplicationsPage -- filters and source columns (U7/U8/U12)", () => {
  it("renders both the source and filed-via columns", async () => {
    render(<ApplicationsPage />);
    const cell = await screen.findByText("Ashby Co");
    const row = cell.closest("tr") as HTMLElement;
    expect(within(row).getByText("jobright")).toBeInTheDocument();
    expect(within(row).getByText("ashby")).toBeInTheDocument();
  });

  it("refetches with the stage filter applied", async () => {
    const user = userEvent.setup();
    render(<ApplicationsPage />);
    await screen.findByText("Acme");
    const filter = screen.getByTestId("stage-filter");
    await waitFor(() => {
      expect(within(filter).getAllByRole("option").length).toBeGreaterThan(0);
    });
    (global.fetch as ReturnType<typeof vi.fn>).mockClear();
    const option = within(filter).getByRole("option", { name: "Applied" });
    await user.click(option);
    await waitFor(() => {
      expect(global.fetch).toHaveBeenCalledWith("/api/applications?stage=applied");
    });
  });

  it("refetches with the source filter applied", async () => {
    const user = userEvent.setup();
    render(<ApplicationsPage />);
    await screen.findByText("Acme");
    const filter = screen.getByTestId("source-filter");
    const option = within(filter).getByRole("option", { name: "linkedin" });
    (global.fetch as ReturnType<typeof vi.fn>).mockClear();
    await user.click(option);
    await waitFor(() => {
      expect(global.fetch).toHaveBeenCalledWith("/api/applications?source=linkedin");
    });
  });
});

describe("ApplicationsPage -- confirm modal and status polling (U4/U5)", () => {
  it("opens a confirm modal instead of submitting immediately", async () => {
    const user = userEvent.setup();
    render(<ApplicationsPage />);
    await screen.findByText("Ashby Co");
    await user.click(screen.getByRole("button", { name: /approve & submit/i }));
    expect(await screen.findByTestId("confirm-content")).toBeInTheDocument();
    expect(within(screen.getByTestId("confirm-content")).getByText(/Ashby Co/)).toBeInTheDocument();
    // Confirming has not happened yet -- no submit POST fired from the click alone.
    expect(global.fetch).not.toHaveBeenCalledWith(
      "/api/applications/5/submit",
      expect.objectContaining({ method: "POST" })
    );
  });

  it("sends the preview_revision_hash from the rendered row in the approve POST body (Review Focus 5)", async () => {
    const user = userEvent.setup();
    render(<ApplicationsPage />);
    await screen.findByText("Ashby Co");
    await user.click(screen.getByRole("button", { name: /approve & submit/i }));
    const modal = await screen.findByTestId("confirm-content");
    await user.click(within(modal).getByRole("button", { name: /approve & submit/i }));
    await waitFor(() => {
      expect(global.fetch).toHaveBeenCalledWith(
        "/api/applications/5/submit",
        expect.objectContaining({
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ revision_hash: "b".repeat(64) }),
        })
      );
    });
  });

  it("refuses to approve a row with no preview_revision_hash and never POSTs", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((url: string, opts?: RequestInit) => {
        if (typeof url === "string" && url.includes("/system-health")) {
          return Promise.resolve({ ok: true, json: async () => ({ health: [] }) } as Response);
        }
        if (!opts || opts.method === undefined) {
          return Promise.resolve({
            ok: true,
            json: async () => ({ applications: [{ ...readyApplication, preview_revision_hash: null }] }),
          } as Response);
        }
        return Promise.resolve({ ok: true, json: async () => ({ ok: true }) } as Response);
      })
    );
    const user = userEvent.setup();
    render(<ApplicationsPage />);
    await screen.findByText("Ashby Co");
    await user.click(screen.getByRole("button", { name: /approve & submit/i }));
    const modal = await screen.findByTestId("confirm-content");
    await user.click(within(modal).getByRole("button", { name: /approve & submit/i }));
    await waitFor(() => {
      expect(toastErrorMock).toHaveBeenCalledWith(
        "This preview has no revision hash yet -- reload and try again"
      );
    });
    expect(global.fetch).not.toHaveBeenCalledWith(
      "/api/applications/5/submit",
      expect.anything()
    );
  });

  it("submits only after confirming in the modal", async () => {
    const user = userEvent.setup();
    render(<ApplicationsPage />);
    await screen.findByText("Ashby Co");
    await user.click(screen.getByRole("button", { name: /approve & submit/i }));
    const modal = await screen.findByTestId("confirm-content");
    await user.click(within(modal).getByRole("button", { name: /approve & submit/i }));
    await waitFor(() => {
      expect(global.fetch).toHaveBeenCalledWith(
        "/api/applications/5/submit",
        expect.objectContaining({ method: "POST" })
      );
    });
  });

  // @testing-library/dom's waitFor/findBy*/user-event's internal waits rely on real
  // setInterval/setTimeout (or Jest's fake-timer detection, which doesn't recognize Vitest) --
  // under vi.useFakeTimers() those never fire without an explicit advance, so they hang forever
  // even when the awaited condition is already true. These fake-timer tests flush pending
  // microtasks via act() and use fireEvent + synchronous queries instead (same precedent as
  // QueuePage.test.tsx / RepliesPage.test.tsx), while keeping every assertion identical to
  // what waitFor/findBy would have checked.
  async function flushMicrotasks() {
    for (let i = 0; i < 10; i++) {
      await act(async () => {
        await Promise.resolve();
      });
    }
  }

  async function openModalAndConfirmSubmit() {
    render(<ApplicationsPage />);
    await flushMicrotasks();
    expect(screen.getByText("Ashby Co")).toBeInTheDocument();
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /approve & submit/i }));
    });
    const modal = screen.getByTestId("confirm-content");
    await act(async () => {
      fireEvent.click(within(modal).getByRole("button", { name: /approve & submit/i }));
    });
    await flushMicrotasks();
    expect(global.fetch).toHaveBeenCalledWith(
      "/api/applications/5/submit",
      expect.objectContaining({ method: "POST" })
    );
  }

  it("shows a submitting state for the row and polls GET /api/applications/5 until it reports applied (I8 -- single-row polling, not the filtered list)", async () => {
    vi.useFakeTimers();
    await openModalAndConfirmSubmit();
    expect(screen.getByText(/submitting/i)).toBeInTheDocument();

    (global.fetch as ReturnType<typeof vi.fn>).mockImplementationOnce((url: string) => {
      if (typeof url === "string" && url === "/api/applications/5") {
        return Promise.resolve({
          ok: true,
          json: async () => ({ application: { id: "5", stage: "applied", apply_blocked_reason: null } }),
        } as Response);
      }
      return Promise.resolve({ ok: true, json: async () => ({ applications: [] }) } as Response);
    });

    // C2: POLL_INTERVAL_MS is now 15s (was 5s) -- advance by the new interval, not the old one.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(15000);
    });

    expect(toastSuccessMock).toHaveBeenCalledWith("Application submitted");
    vi.useRealTimers();
  });

  it("shows a distinct failed/blocked state (not the generic timeout message) and a Try again action when the row reports apply_blocked_reason (I8)", async () => {
    vi.useFakeTimers();
    await openModalAndConfirmSubmit();

    (global.fetch as ReturnType<typeof vi.fn>).mockImplementationOnce((url: string) => {
      if (typeof url === "string" && url === "/api/applications/5") {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            application: {
              id: "5",
              stage: "ready_to_submit",
              apply_blocked_reason: "confirmation element not found after clicking Submit",
              automation_status: "failed_retryable",
            },
          }),
        } as Response);
      }
      return Promise.resolve({ ok: true, json: async () => ({ applications: [] }) } as Response);
    });

    // C2: POLL_INTERVAL_MS is now 15s (was 5s) -- advance by the new interval, not the old one.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(15000);
    });

    expect(screen.getByText(/confirmation element not found/i)).toBeInTheDocument();
    const tryAgainButton = screen.getByRole("button", { name: /try again/i });

    (global.fetch as ReturnType<typeof vi.fn>).mockImplementationOnce((url: string, opts?: RequestInit) => {
      if (typeof url === "string" && url === "/api/applications/5/reset-approval" && opts?.method === "POST") {
        return Promise.resolve({ ok: true, json: async () => ({ ok: true }) } as Response);
      }
      return Promise.resolve({ ok: true, json: async () => ({ applications: [] }) } as Response);
    });
    await act(async () => {
      fireEvent.click(tryAgainButton);
    });
    await flushMicrotasks();
    expect(global.fetch).toHaveBeenCalledWith(
      "/api/applications/5/reset-approval",
      expect.objectContaining({ method: "POST" })
    );
    // Once reset, the row is back to a plain Approve & Submit state, not stuck "submitting".
    // handleTryAgain's load() re-fetches the list -- flush again so the refetched (loading:
    // false) table renders before asserting on it.
    await flushMicrotasks();
    expect(screen.getByRole("button", { name: /approve & submit/i })).toBeInTheDocument();
    vi.useRealTimers();
  });

  // Merge review 2026-09-28, finding 5: a timeout used to just drop the row back to plain
  // "Approve & Submit" with a generic toast and no persisted state -- indistinguishable from a
  // row that was never submitted, even though approved_at is still set and a re-click would
  // 409. It now moves into a distinct, recoverable "may still be running" state instead.
  it("shows a distinct 'may still be running' state (not plain Approve & Submit) when polling exceeds the timeout with no resolution either way", async () => {
    vi.useFakeTimers();
    await openModalAndConfirmSubmit();

    (global.fetch as ReturnType<typeof vi.fn>).mockImplementation((url: string) => {
      if (typeof url === "string" && url === "/api/applications/5") {
        return Promise.resolve({
          ok: true,
          json: async () => ({ application: { id: "5", stage: "ready_to_submit", apply_blocked_reason: null } }),
        } as Response);
      }
      return Promise.resolve({ ok: true, json: async () => ({ applications: [] }) } as Response);
    });

    // C2: POLL_TIMEOUT_MS is now 16 minutes (was 90s). The stop check only runs on a tick
    // (every POLL_INTERVAL_MS=15s), so advance well past the timeout plus a full interval to
    // guarantee a tick actually lands after it, not just up to the exact boundary.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(17 * 60 * 1000 + 30000);
    });

    expect(toastErrorMock).toHaveBeenCalledWith(
      "Still processing after 16 minutes -- check status or reset to try again"
    );
    expect(screen.getByText(/taking longer than expected/i)).toBeInTheDocument();
    // Not the plain Approve & Submit button -- that would silently invite a re-click that just
    // 409s against the still-set approved_at with no explanation.
    expect(screen.queryByRole("button", { name: /^approve & submit$/i })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /check now/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /reset approval/i })).toBeInTheDocument();
    vi.useRealTimers();
  });

  it("persists the timed-out state to sessionStorage so a refresh doesn't silently re-offer Approve & Submit", async () => {
    vi.useFakeTimers();
    await openModalAndConfirmSubmit();
    (global.fetch as ReturnType<typeof vi.fn>).mockImplementation((url: string) => {
      if (typeof url === "string" && url === "/api/applications/5") {
        return Promise.resolve({
          ok: true,
          json: async () => ({ application: { id: "5", stage: "ready_to_submit", apply_blocked_reason: null } }),
        } as Response);
      }
      return Promise.resolve({ ok: true, json: async () => ({ applications: [] }) } as Response);
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(17 * 60 * 1000 + 30000);
    });

    const raw = sessionStorage.getItem("applications_timed_out_ids");
    expect(raw ? (JSON.parse(raw) as string[]) : []).toContain("5");
    vi.useRealTimers();
  });

  it("'Check now' re-checks status once and clears the timed-out state on a terminal result", async () => {
    vi.useFakeTimers();
    await openModalAndConfirmSubmit();
    (global.fetch as ReturnType<typeof vi.fn>).mockImplementation((url: string) => {
      if (typeof url === "string" && url === "/api/applications/5") {
        return Promise.resolve({
          ok: true,
          json: async () => ({ application: { id: "5", stage: "ready_to_submit", apply_blocked_reason: null } }),
        } as Response);
      }
      return Promise.resolve({ ok: true, json: async () => ({ applications: [] }) } as Response);
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(17 * 60 * 1000 + 30000);
    });
    expect(screen.getByRole("button", { name: /check now/i })).toBeInTheDocument();

    (global.fetch as ReturnType<typeof vi.fn>).mockImplementation((url: string) => {
      if (typeof url === "string" && url === "/api/applications/5") {
        return Promise.resolve({
          ok: true,
          json: async () => ({ application: { id: "5", stage: "applied", apply_blocked_reason: null } }),
        } as Response);
      }
      return Promise.resolve({ ok: true, json: async () => ({ applications: [] }) } as Response);
    });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /check now/i }));
    });
    await flushMicrotasks();

    expect(toastSuccessMock).toHaveBeenCalledWith("Application submitted");
    const raw = sessionStorage.getItem("applications_timed_out_ids");
    expect(raw ? (JSON.parse(raw) as string[]) : []).not.toContain("5");
    vi.useRealTimers();
  });

  it("reset approval also clears the timed-out state (not just a blocked reason)", async () => {
    vi.useFakeTimers();
    await openModalAndConfirmSubmit();
    (global.fetch as ReturnType<typeof vi.fn>).mockImplementation((url: string) => {
      if (typeof url === "string" && url === "/api/applications/5") {
        return Promise.resolve({
          ok: true,
          json: async () => ({ application: { id: "5", stage: "ready_to_submit", apply_blocked_reason: null } }),
        } as Response);
      }
      return Promise.resolve({ ok: true, json: async () => ({ applications: [] }) } as Response);
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(17 * 60 * 1000 + 30000);
    });

    (global.fetch as ReturnType<typeof vi.fn>).mockImplementation((url: string, opts?: RequestInit) => {
      if (typeof url === "string" && url === "/api/applications/5/reset-approval" && opts?.method === "POST") {
        return Promise.resolve({ ok: true, json: async () => ({ ok: true }) } as Response);
      }
      return Promise.resolve({ ok: true, json: async () => ({ applications: [] }) } as Response);
    });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /reset approval/i }));
    });
    await flushMicrotasks();

    const raw = sessionStorage.getItem("applications_timed_out_ids");
    expect(raw ? (JSON.parse(raw) as string[]) : []).not.toContain("5");
    vi.useRealTimers();
  });

  // Merge review 2026-09-28, finding 4: submittingIds reloaded from sessionStorage on mount, but
  // only doApprove() ever started a poll timer -- a restored id rendered "Submitting..." forever
  // with no timer resuming underneath it. The review's own reproduction calls out that this
  // needs an actual unmount/remount, not only a sessionStorage-write assertion (that only proves
  // the write happened, not that anything reads it back on mount).
  it("resumes polling for an id restored from sessionStorage on a fresh mount (finding 4)", async () => {
    sessionStorage.setItem("applications_submitting_ids", JSON.stringify(["5"]));
    vi.useFakeTimers();

    render(<ApplicationsPage />);
    await flushMicrotasks();
    expect(screen.getByText(/submitting/i)).toBeInTheDocument();

    (global.fetch as ReturnType<typeof vi.fn>).mockImplementationOnce((url: string) => {
      if (typeof url === "string" && url === "/api/applications/5") {
        return Promise.resolve({
          ok: true,
          json: async () => ({ application: { id: "5", stage: "applied", apply_blocked_reason: null } }),
        } as Response);
      }
      return Promise.resolve({ ok: true, json: async () => ({ applications: [] }) } as Response);
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(15000);
    });

    expect(toastSuccessMock).toHaveBeenCalledWith("Application submitted");
    vi.useRealTimers();
  });

  it("resumes polling after an actual unmount/remount cycle, not only after the initial mount", async () => {
    vi.useFakeTimers();
    const { unmount } = render(<ApplicationsPage />);
    await flushMicrotasks();
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /approve & submit/i }));
    });
    const modal = screen.getByTestId("confirm-content");
    await act(async () => {
      fireEvent.click(within(modal).getByRole("button", { name: /approve & submit/i }));
    });
    await flushMicrotasks();
    expect(screen.getByText(/submitting/i)).toBeInTheDocument();

    // Simulate a full page refresh: unmount (which clears this instance's in-memory
    // pollTimers ref) and mount a fresh component instance, the way a real navigation would.
    unmount();
    render(<ApplicationsPage />);
    await flushMicrotasks();
    expect(screen.getByText(/submitting/i)).toBeInTheDocument();

    (global.fetch as ReturnType<typeof vi.fn>).mockImplementationOnce((url: string) => {
      if (typeof url === "string" && url === "/api/applications/5") {
        return Promise.resolve({
          ok: true,
          json: async () => ({ application: { id: "5", stage: "applied", apply_blocked_reason: null } }),
        } as Response);
      }
      return Promise.resolve({ ok: true, json: async () => ({ applications: [] }) } as Response);
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(15000);
    });

    expect(toastSuccessMock).toHaveBeenCalledWith("Application submitted");
    vi.useRealTimers();
  });

  it("persists submittingIds to sessionStorage so a page refresh mid-poll doesn't re-enable Approve (M14 -- same precedent as QueuePage's skip-list)", async () => {
    const user = userEvent.setup();
    render(<ApplicationsPage />);
    await screen.findByText("Ashby Co");
    await user.click(screen.getByRole("button", { name: /approve & submit/i }));
    const modal = await screen.findByTestId("confirm-content");
    await user.click(within(modal).getByRole("button", { name: /approve & submit/i }));
    await waitFor(() => {
      expect(global.fetch).toHaveBeenCalledWith(
        "/api/applications/5/submit",
        expect.objectContaining({ method: "POST" })
      );
    });
    await waitFor(() => {
      const raw = sessionStorage.getItem("applications_submitting_ids");
      expect(raw ? (JSON.parse(raw) as string[]) : []).toContain("5");
    });
  });
});

describe("ApplicationsPage -- system health strip (U13/U14)", () => {
  it("fetches system health on mount", async () => {
    render(<ApplicationsPage />);
    await screen.findByText("Acme");
    await waitFor(() => {
      expect(global.fetch).toHaveBeenCalledWith("/api/system-health");
    });
  });
});

describe("ApplicationsPage -- automation_status gating (needs_confirmation, Review Focus 1)", () => {
  function stubList(apps: unknown[]) {
    vi.stubGlobal(
      "fetch",
      vi.fn((url: string, opts?: RequestInit) => {
        if (typeof url === "string" && url.includes("/system-health")) {
          return Promise.resolve({ ok: true, json: async () => ({ health: [] }) } as Response);
        }
        if (!opts || opts.method === undefined) {
          return Promise.resolve({ ok: true, json: async () => ({ applications: apps }) } as Response);
        }
        return Promise.resolve({ ok: true, json: async () => ({ ok: true }) } as Response);
      })
    );
  }

  const needsConfirmation = {
    ...readyApplication,
    id: "7",
    company: "Unsure Inc",
    automation_status: "needs_confirmation",
    apply_blocked_reason: "submit() raised after the Submit click",
  };

  it("a needs_confirmation row shows both resolve buttons and neither Approve & Submit nor Try again", async () => {
    stubList([needsConfirmation]);
    render(<ApplicationsPage />);
    await screen.findByText("Unsure Inc");
    const row = screen.getByText("Unsure Inc").closest("tr") as HTMLElement;
    expect(within(row).getByText(/submit may have gone through/i)).toBeInTheDocument();
    expect(within(row).getByRole("button", { name: /it went through/i })).toBeInTheDocument();
    expect(within(row).getByRole("button", { name: /it didn't go through/i })).toBeInTheDocument();
    expect(within(row).queryByRole("button", { name: /approve & submit/i })).not.toBeInTheDocument();
    expect(within(row).queryByRole("button", { name: /try again/i })).not.toBeInTheDocument();
  });

  it.each([
    ["It went through", true],
    ["It didn't go through", false],
  ])("clicking '%s' POSTs submitted=%s to resolve-confirmation and reloads", async (label, submitted) => {
    stubList([needsConfirmation]);
    const user = userEvent.setup();
    render(<ApplicationsPage />);
    await screen.findByText("Unsure Inc");
    await user.click(screen.getByRole("button", { name: label }));
    await waitFor(() => {
      expect(global.fetch).toHaveBeenCalledWith(
        "/api/applications/7/resolve-confirmation",
        expect.objectContaining({
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ submitted }),
        })
      );
    });
    // load() refetches the list after a successful resolution.
    await waitFor(() => {
      const listCalls = (global.fetch as ReturnType<typeof vi.fn>).mock.calls.filter(
        ([u, o]) => typeof u === "string" && u.startsWith("/api/applications") && !(o as RequestInit | undefined)?.method
      );
      expect(listCalls.length).toBeGreaterThanOrEqual(2);
    });
  });

  it("shows a toast and does not reload when recording the resolution fails", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((url: string, opts?: RequestInit) => {
        if (typeof url === "string" && url.includes("/system-health")) {
          return Promise.resolve({ ok: true, json: async () => ({ health: [] }) } as Response);
        }
        if (!opts || opts.method === undefined) {
          return Promise.resolve({ ok: true, json: async () => ({ applications: [needsConfirmation] }) } as Response);
        }
        return Promise.resolve({ ok: false, json: async () => ({ error: "x" }) } as Response);
      })
    );
    const user = userEvent.setup();
    render(<ApplicationsPage />);
    await screen.findByText("Unsure Inc");
    await user.click(screen.getByRole("button", { name: "It went through" }));
    await waitFor(() => {
      expect(toastErrorMock).toHaveBeenCalledWith("Could not record that -- try again");
    });
  });

  it("a failed_retryable row with a reason renders Try again", async () => {
    stubList([{ ...readyApplication, id: "8", company: "Retry Co", automation_status: "failed_retryable", apply_blocked_reason: "boom before click" }]);
    render(<ApplicationsPage />);
    await screen.findByText("Retry Co");
    const row = screen.getByText("Retry Co").closest("tr") as HTMLElement;
    expect(within(row).getByRole("button", { name: /try again/i })).toBeInTheDocument();
  });

  it("a row with a reason but automation_status=submitting renders no Try again, only the reason text", async () => {
    stubList([{ ...readyApplication, id: "9", company: "Busy Co", automation_status: "submitting", apply_blocked_reason: "stale reason" }]);
    render(<ApplicationsPage />);
    await screen.findByText("Busy Co");
    const row = screen.getByText("Busy Co").closest("tr") as HTMLElement;
    expect(within(row).queryByRole("button", { name: /try again/i })).not.toBeInTheDocument();
    expect(within(row).queryByRole("button", { name: /approve & submit/i })).not.toBeInTheDocument();
    expect(within(row).getAllByText(/stale reason/i).length).toBeGreaterThan(0);
  });

  it.each([
    ["idle", { approve: false, tryAgain: false, reset: false, resolve: false }],
    ["preparing", { approve: false, tryAgain: false, reset: false, resolve: false }],
    ["needs_input", { approve: false, tryAgain: false, reset: false, resolve: false }],
    ["ready_for_review", { approve: true, tryAgain: false, reset: false, resolve: false }],
    ["approved", { approve: false, tryAgain: false, reset: true, resolve: false }],
    ["submitting", { approve: false, tryAgain: false, reset: false, resolve: false }],
    ["submitted", { approve: false, tryAgain: false, reset: false, resolve: false }],
    ["needs_confirmation", { approve: false, tryAgain: false, reset: false, resolve: true }],
    ["failed_retryable", { approve: false, tryAgain: true, reset: false, resolve: false }],
    ["failed_terminal", { approve: false, tryAgain: false, reset: false, resolve: false }],
    ["unsupported", { approve: false, tryAgain: false, reset: false, resolve: false }],
  ])("action buttons for automation_status=%s", async (status, want) => {
    stubList([{ ...readyApplication, id: "20", company: "Matrix Co", automation_status: status, apply_blocked_reason: "some reason" }]);
    render(<ApplicationsPage />);
    await screen.findByText("Matrix Co");
    const row = screen.getByText("Matrix Co").closest("tr") as HTMLElement;
    const has = (n: RegExp) => within(row).queryByRole("button", { name: n }) !== null;
    expect(has(/^approve & submit$/i)).toBe(want.approve);
    expect(has(/^try again$/i)).toBe(want.tryAgain);
    expect(has(/^reset approval$/i)).toBe(want.reset);
    expect(has(/it went through/i)).toBe(want.resolve);
  });

  it("ready_for_review with a stale reason shows it dim above Approve & Submit, no Try again", async () => {
    stubList([{ ...readyApplication, id: "21", company: "Stale Co", automation_status: "ready_for_review", apply_blocked_reason: "old failure" }]);
    render(<ApplicationsPage />);
    await screen.findByText("Stale Co");
    const row = screen.getByText("Stale Co").closest("tr") as HTMLElement;
    expect(within(row).getAllByText(/old failure/i).length).toBeGreaterThan(0);
    expect(within(row).getByRole("button", { name: /^approve & submit$/i })).toBeInTheDocument();
    expect(within(row).queryByRole("button", { name: /try again/i })).not.toBeInTheDocument();
  });

  it("Try again clears the polled live status so the reloaded row's status shows", async () => {
    let listCalls = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn((url: string, opts?: RequestInit) => {
        if (typeof url === "string" && url.includes("/system-health")) {
          return Promise.resolve({ ok: true, json: async () => ({ health: [] }) } as Response);
        }
        if (!opts || opts.method === undefined) {
          listCalls++;
          const status = listCalls === 1 ? "failed_retryable" : "ready_for_review";
          return Promise.resolve({
            ok: true,
            json: async () => ({ applications: [{ ...readyApplication, id: "22", company: "Live Co", automation_status: status, apply_blocked_reason: listCalls === 1 ? "boom" : null }] }),
          } as Response);
        }
        return Promise.resolve({ ok: true, json: async () => ({ ok: true }) } as Response);
      })
    );
    const user = userEvent.setup();
    render(<ApplicationsPage />);
    await screen.findByText("Live Co");
    await user.click(screen.getByRole("button", { name: /^try again$/i }));
    const row = screen.getByText("Live Co").closest("tr") as HTMLElement;
    await waitFor(() => expect(within(row).getByText("Ready for review")).toBeInTheDocument());
  });

  it("renders an automation status badge for a non-idle row, and none for idle", async () => {
    stubList([
      { ...readyApplication, id: "10", company: "Badge Co", automation_status: "approved", apply_blocked_reason: null },
      { ...readyApplication, id: "11", company: "Idle Co", stage: "saved", apply_preview: null, automation_status: "idle", apply_blocked_reason: null },
    ]);
    render(<ApplicationsPage />);
    await screen.findByText("Badge Co");
    const badgeRow = screen.getByText("Badge Co").closest("tr") as HTMLElement;
    expect(within(badgeRow).getByText("Approved / queued")).toBeInTheDocument();
    const idleRow = screen.getByText("Idle Co").closest("tr") as HTMLElement;
    expect(within(idleRow).queryByText("Idle")).not.toBeInTheDocument();
  });

  it("polling that observes needs_confirmation stops with the unknown-outcome toast and swaps in the resolve buttons", async () => {
    vi.useFakeTimers();
    render(<ApplicationsPage />);
    for (let i = 0; i < 10; i++) await act(async () => { await Promise.resolve(); });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /approve & submit/i }));
    });
    const modal = screen.getByTestId("confirm-content");
    await act(async () => {
      fireEvent.click(within(modal).getByRole("button", { name: /approve & submit/i }));
    });
    for (let i = 0; i < 10; i++) await act(async () => { await Promise.resolve(); });

    (global.fetch as ReturnType<typeof vi.fn>).mockImplementationOnce((url: string) => {
      if (url === "/api/applications/5") {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            application: {
              id: "5",
              stage: "ready_to_submit",
              automation_status: "needs_confirmation",
              apply_blocked_reason: "raised after click",
            },
          }),
        } as Response);
      }
      return Promise.resolve({ ok: true, json: async () => ({ applications: [] }) } as Response);
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(15000);
    });
    expect(toastErrorMock).toHaveBeenCalledWith(
      "Submit outcome unknown -- confirm whether it went through"
    );
    expect(screen.getByRole("button", { name: /it went through/i })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /try again/i })).not.toBeInTheDocument();
    vi.useRealTimers();
  });
});

describe("ApplicationsPage -- needs_input re-prepare and needs_confirmation reason", () => {
  function stubList(apps: unknown[]) {
    vi.stubGlobal(
      "fetch",
      vi.fn((url: string, opts?: RequestInit) => {
        if (typeof url === "string" && url.includes("/system-health")) {
          return Promise.resolve({ ok: true, json: async () => ({ health: [] }) } as Response);
        }
        if (!opts || opts.method === undefined) {
          return Promise.resolve({ ok: true, json: async () => ({ applications: apps }) } as Response);
        }
        return Promise.resolve({ ok: true, json: async () => ({ ok: true }) } as Response);
      })
    );
  }

  it("a needs_input row shows its reason and a Re-prepare button that POSTs requeue-preview", async () => {
    stubList([{ ...readyApplication, id: "31", company: "Drift Co", automation_status: "needs_input", apply_blocked_reason: "form changed since you reviewed it" }]);
    const user = userEvent.setup();
    render(<ApplicationsPage />);
    await screen.findByText("Drift Co");
    const row = screen.getByText("Drift Co").closest("tr") as HTMLElement;
    expect(within(row).getAllByText(/form changed since you reviewed it/i).length).toBeGreaterThan(0);
    expect(within(row).queryByRole("button", { name: /approve & submit/i })).not.toBeInTheDocument();
    await user.click(within(row).getByRole("button", { name: /re-prepare/i }));
    await waitFor(() => {
      expect(global.fetch).toHaveBeenCalledWith(
        "/api/applications/31/requeue-preview",
        expect.objectContaining({ method: "POST" })
      );
    });
    await waitFor(() => {
      const listCalls = (global.fetch as ReturnType<typeof vi.fn>).mock.calls.filter(
        ([u, o]) => typeof u === "string" && u.startsWith("/api/applications") && !(o as RequestInit | undefined)?.method
      );
      expect(listCalls.length).toBeGreaterThanOrEqual(2);
    });
  });

  it("a needs_input row with no reason falls back to 'Needs your input'", async () => {
    stubList([{ ...readyApplication, id: "32", company: "Blank Co", automation_status: "needs_input", apply_blocked_reason: null }]);
    render(<ApplicationsPage />);
    await screen.findByText("Blank Co");
    const row = screen.getByText("Blank Co").closest("tr") as HTMLElement;
    expect(within(row).getAllByText(/needs your input/i).length).toBeGreaterThan(0);
  });

  it("a needs_confirmation row shows the reconciler's reason above the buttons", async () => {
    stubList([{ ...readyApplication, id: "33", company: "Recon Co", automation_status: "needs_confirmation", apply_blocked_reason: "No receipt email found after 24h" }]);
    render(<ApplicationsPage />);
    await screen.findByText("Recon Co");
    const row = screen.getByText("Recon Co").closest("tr") as HTMLElement;
    expect(within(row).getAllByText(/no receipt email found/i).length).toBeGreaterThan(0);
  });
});
