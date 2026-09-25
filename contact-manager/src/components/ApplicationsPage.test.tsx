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
  created_at: "2026-08-30T00:00:00Z", updated_at: "2026-08-30T00:00:00Z",
};

beforeEach(() => {
  sessionStorage.clear();
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

    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
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
            },
          }),
        } as Response);
      }
      return Promise.resolve({ ok: true, json: async () => ({ applications: [] }) } as Response);
    });

    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
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

  it("still shows the ambiguous timeout message when polling exceeds the timeout with no resolution either way", async () => {
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
      await vi.advanceTimersByTimeAsync(95000);
    });

    expect(toastErrorMock).toHaveBeenCalledWith("Still processing -- check back in a bit");
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
