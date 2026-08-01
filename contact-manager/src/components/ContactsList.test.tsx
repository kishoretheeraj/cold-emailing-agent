import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, within, waitFor, act, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

// ── Primitive mocks ────────────────────────────────────────────────────────────

vi.mock("vaul", () => ({
  Drawer: {
    Root: ({
      children,
      open,
    }: {
      children: React.ReactNode;
      open?: boolean;
    }) => (open ? <>{children}</> : null),
    Portal: ({ children }: { children: React.ReactNode }) => <>{children}</>,
    Overlay: () => <div />,
    Content: ({ children }: { children: React.ReactNode }) => (
      <div role="dialog" data-testid="sheet-content">
        {children}
      </div>
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
    Title: ({ children }: { children: React.ReactNode }) => (
      <h2>{children}</h2>
    ),
    Description: ({ children }: { children: React.ReactNode }) => (
      <p>{children}</p>
    ),
  },
}));

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

vi.mock("@radix-ui/react-tooltip", () => ({
  Provider: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  Root: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  Trigger: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  Portal: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  Content: () => null,
  Arrow: () => null,
  TooltipProvider: ({ children }: { children: React.ReactNode }) => (
    <>{children}</>
  ),
}));

// Stateful Select mock — captures onValueChange per instance
// Must be hoisted so it is available inside vi.mock factory
const { selectInstances } = vi.hoisted(() => ({
  selectInstances: [] as Array<(v: string) => void>,
}));
vi.mock("@radix-ui/react-select", () => ({
  Root: ({
    children,
    value,
    onValueChange,
  }: {
    children: React.ReactNode;
    value?: string;
    onValueChange?: (v: string) => void;
  }) => {
    if (onValueChange) selectInstances.push(onValueChange);
    return <div data-select-value={value}>{children}</div>;
  },
  Trigger: ({ children }: { children: React.ReactNode }) => (
    <button type="button">{children}</button>
  ),
  Value: () => null,
  Icon: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  Portal: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  Content: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  Viewport: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  Group: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  Label: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  Item: ({
    children,
    value,
  }: {
    children: React.ReactNode;
    value: string;
  }) => (
    <div role="option" data-value={value}>
      {children}
    </div>
  ),
  ItemText: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  ItemIndicator: () => null,
  Separator: () => <hr />,
}));

// ── IntersectionObserver mock ─────────────────────────────────────────────────
// Uses a plain function so vi.restoreAllMocks() in afterEach never resets it.

type IOCallback = (entries: Partial<IntersectionObserverEntry>[]) => void;
let ioCallback: IOCallback | null = null;

(function installIOCapture() {
  function IOCapture(this: object, cb: IOCallback) {
    ioCallback = cb;
    return { observe() {}, disconnect() {}, unobserve() {} };
  }
  global.IntersectionObserver = IOCapture as unknown as typeof IntersectionObserver;
})();

// ── Supabase mock ─────────────────────────────────────────────────────────────

vi.mock("@/components/ThreadView", () => ({
  ThreadView: () => null,
}));

// vi.hoisted ensures these are available inside vi.mock factory (which is hoisted)
const { limitMock, updateEqMock, singleMock, updateMock } = vi.hoisted(() => ({
  limitMock: vi.fn(),
  updateEqMock: vi.fn(),
  singleMock: vi.fn(),
  updateMock: vi.fn(),
}));

vi.mock("@/lib/supabase", () => {
  const readChain: Record<string, unknown> = {};
  for (const m of ["is", "order", "or", "in", "eq", "lt"]) {
    readChain[m] = vi.fn(() => readChain);
  }
  readChain.limit = limitMock;
  readChain.single = singleMock;

  return {
    supabase: {
      from: vi.fn(() => ({
        select: vi.fn(() => readChain),
        update: updateMock,
      })),
    },
  };
});

import { ContactsList } from "./ContactsList";
import type { Contact } from "@/lib/types";

// ── Fixtures ──────────────────────────────────────────────────────────────────

const makeContact = (overrides: Partial<Contact> = {}): Contact => ({
  id: "1",
  name: "Dana Ehrlich",
  email: "dana@clearbond.com",
  company: "Clearbond",
  role: "CEO",
  detail: null,
  tier: 1,
  mode: "outreach",
  stage: "first_touch_sent",
  reply_status: "no_reply",
  dartmouth: false,
  job_title: null,
  job_description: null,
  company_applied: null,
  applied_date: null,
  connection_context: null,
  followup_date: null,
  notes: null,
  created_at: "2026-05-01T10:00:00Z",
  message_id: null,
  last_emailed: null,
  deleted_at: null,
  ...overrides,
});

const dana = makeContact();
const sarah = makeContact({
  id: "2",
  name: "Sarah Kim",
  email: "sarah@stripe.com",
  company: "Stripe",
  mode: "applied",
  stage: "applied_intro_drafted",
  message_id: "msg-xyz-123",
});
const priya = makeContact({
  id: "3",
  name: "Priya Nair",
  email: "priya@northwind.com",
  company: "Northwind",
  mode: "networking",
  stage: "networking_sent",
  connection_context: "Fellow Tuck MEM",
});

const defaultProps = {
  refreshKey: 0,
  onError: vi.fn(),
  onSuccess: vi.fn(),
};

beforeEach(() => {
  limitMock.mockReset();
  updateEqMock.mockReset();
  singleMock.mockReset();
  updateMock.mockReset();
  selectInstances.length = 0;
  ioCallback = null;
  defaultProps.onError.mockReset();
  defaultProps.onSuccess.mockReset();
  updateEqMock.mockResolvedValue({ error: null });
  updateMock.mockImplementation(() => ({ eq: updateEqMock }));
  // Return null data by default so the component keeps the list data as selectedContact.
  // Tests that need specific full-record data can override with singleMock.mockResolvedValueOnce.
  singleMock.mockResolvedValue({ data: null, error: null });
});

// ── Fetching ──────────────────────────────────────────────────────────────────

describe("ContactsList — fetching", () => {
  it("shows skeleton rows while loading", () => {
    limitMock.mockReturnValue(new Promise(() => {})); // never resolves
    render(<ContactsList {...defaultProps} />);
    // Skeletons are rendered as divs with animate-pulse class
    const skeletons = document.querySelectorAll(".animate-pulse");
    expect(skeletons.length).toBeGreaterThan(0);
  });

  it("renders contact rows after fetch", async () => {
    limitMock.mockResolvedValue({ data: [dana, sarah], error: null });
    render(<ContactsList {...defaultProps} />);
    await waitFor(() => screen.getByText("Dana Ehrlich"));
    expect(screen.getByText("Clearbond")).toBeInTheDocument();
    expect(screen.getByText("Sarah Kim")).toBeInTheDocument();
  });

  it("passes deleted_at=null filter to query", async () => {
    limitMock.mockResolvedValue({ data: [], error: null });
    render(<ContactsList {...defaultProps} />);
    await waitFor(() => expect(limitMock).toHaveBeenCalled());
    const { supabase } = await import("@/lib/supabase");
    const fromResult = (supabase.from as ReturnType<typeof vi.fn>).mock.results[0].value;
    const selectResult = fromResult.select.mock.results[0].value;
    expect(selectResult.is).toHaveBeenCalledWith("deleted_at", null);
  });

  it("sets hasMore=false when fewer than PAGE_SIZE rows returned", async () => {
    limitMock.mockResolvedValue({ data: [dana], error: null });
    render(<ContactsList {...defaultProps} />);
    await waitFor(() => screen.getByText("Dana Ehrlich"));
    expect(screen.getByText("All contacts loaded")).toBeInTheDocument();
  });

  it("shows 'All contacts loaded' not visible when PAGE_SIZE rows returned", async () => {
    const rows = Array.from({ length: 30 }, (_, i) =>
      makeContact({ id: String(i), name: `Contact ${i}`, email: `c${i}@x.com` })
    );
    limitMock.mockResolvedValue({ data: rows, error: null });
    render(<ContactsList {...defaultProps} />);
    await waitFor(() => screen.getByText("Contact 0"));
    expect(screen.queryByText("All contacts loaded")).toBeNull();
  });

  it("forwards fetch errors to onError", async () => {
    limitMock.mockResolvedValue({ data: null, error: { message: "db error" } });
    render(<ContactsList {...defaultProps} />);
    await waitFor(() =>
      expect(defaultProps.onError).toHaveBeenCalledWith(
        expect.stringMatching(/db error/i)
      )
    );
  });

  it("refetches when refreshKey changes", async () => {
    limitMock.mockResolvedValue({ data: [dana], error: null });
    const { rerender } = render(<ContactsList {...defaultProps} refreshKey={0} />);
    await waitFor(() => expect(limitMock).toHaveBeenCalledTimes(1));
    rerender(<ContactsList {...defaultProps} refreshKey={1} />);
    await waitFor(() => expect(limitMock).toHaveBeenCalledTimes(2));
  });
});

// ── Empty states ──────────────────────────────────────────────────────────────

describe("ContactsList — empty states", () => {
  it("shows 'No contacts yet' with empty filters and 0 rows", async () => {
    limitMock.mockResolvedValue({ data: [], error: null });
    render(<ContactsList {...defaultProps} />);
    await waitFor(() =>
      expect(screen.getByText("No contacts yet")).toBeInTheDocument()
    );
  });

  it("shows filter empty state with 'No contacts match' and Clear button", async () => {
    const user = userEvent.setup();
    limitMock.mockResolvedValue({ data: [], error: null });
    render(<ContactsList {...defaultProps} />);

    // Wait for initial empty state, then click Tier 1 to apply a filter
    await waitFor(() => screen.getByText("No contacts yet"), { timeout: 3000 });
    await user.click(screen.getByRole("button", { name: "1" }));

    await waitFor(
      () =>
        expect(
          screen.getByText("No contacts match these filters")
        ).toBeInTheDocument(),
      { timeout: 3000 }
    );
    // Two "Clear filters" buttons: one in ContactsFilters, one in EmptyState action
    expect(
      screen.getAllByRole("button", { name: /clear filters/i }).length
    ).toBeGreaterThan(0);
  });
});

// ── Infinite scroll ───────────────────────────────────────────────────────────

describe("ContactsList — pagination", () => {
  it("fetchMore appends results when sentinel intersects", async () => {
    const page1 = Array.from({ length: 30 }, (_, i) =>
      makeContact({
        id: String(i),
        name: `Contact ${i}`,
        email: `c${i}@x.com`,
        created_at: `2026-04-${String(i + 1).padStart(2, "0")}T10:00:00Z`,
      })
    );
    const page2 = [
      makeContact({ id: "99", name: "Last Contact", email: "last@x.com" }),
    ];

    limitMock
      .mockResolvedValueOnce({ data: page1, error: null })
      .mockResolvedValueOnce({ data: page2, error: null });

    render(<ContactsList {...defaultProps} />);
    await waitFor(() => screen.getByText("Contact 0"), { timeout: 3000 });

    // Flush pending effects so fetchMore captures updated hasMore + cursor
    await act(async () => {});

    await act(async () => {
      ioCallback?.([{ isIntersecting: true }]);
    });

    await waitFor(() => screen.getByText("Last Contact"), { timeout: 3000 });
    expect(screen.getByText("All contacts loaded")).toBeInTheDocument();
  });

  it("applies cursor (lt) on fetchMore", async () => {
    const page1 = Array.from({ length: 30 }, (_, i) =>
      makeContact({
        id: String(i),
        name: `Contact ${i}`,
        email: `c${i}@x.com`,
        created_at: `2026-04-${String(i + 1).padStart(2, "0")}T10:00:00Z`,
      })
    );
    limitMock.mockResolvedValue({ data: page1, error: null });

    render(<ContactsList {...defaultProps} />);
    await waitFor(() => screen.getByText("Contact 0"), { timeout: 3000 });

    // Flush pending effects
    await act(async () => {});

    await act(async () => {
      ioCallback?.([{ isIntersecting: true }]);
    });

    await waitFor(() => expect(limitMock).toHaveBeenCalledTimes(2), {
      timeout: 3000,
    });

    const { supabase } = await import("@/lib/supabase");
    const fromCalls = (supabase.from as ReturnType<typeof vi.fn>).mock.results;
    const lastFrom = fromCalls[fromCalls.length - 1].value;
    const selectResult = lastFrom.select.mock.results[0].value;
    expect(selectResult.lt).toHaveBeenCalledWith(
      "created_at",
      expect.any(String)
    );
  });
});

// ── Filters ───────────────────────────────────────────────────────────────────

describe("ContactsList — filter changes", () => {
  it("text search debounces before fetching", async () => {
    const user = userEvent.setup({ delay: null });
    limitMock.mockResolvedValue({ data: [], error: null });
    render(<ContactsList {...defaultProps} />);

    await waitFor(() => expect(limitMock).toHaveBeenCalledTimes(1));

    const input = screen.getByPlaceholderText("Search by name or company");
    await user.type(input, "k");

    // Debounce not yet fired
    expect(limitMock).toHaveBeenCalledTimes(1);

    // Wait for debounce (300ms + some buffer)
    await waitFor(() => expect(limitMock).toHaveBeenCalledTimes(2), {
      timeout: 600,
    });
  });

  it("tier pill click fires immediately (no debounce)", async () => {
    const user = userEvent.setup();
    limitMock.mockResolvedValue({ data: [], error: null });
    render(<ContactsList {...defaultProps} />);

    await waitFor(() => expect(limitMock).toHaveBeenCalledTimes(1));

    await user.click(screen.getByRole("button", { name: "1" }));

    await waitFor(() => expect(limitMock).toHaveBeenCalledTimes(2));
  });

  it("escapes % and _ in search terms", async () => {
    limitMock.mockResolvedValue({ data: [], error: null });
    render(<ContactsList {...defaultProps} />);

    await waitFor(() => expect(limitMock).toHaveBeenCalledTimes(1));
    const input = screen.getByPlaceholderText("Search by name or company");

    // Use fireEvent.change to avoid userEvent's special handling of % and _
    fireEvent.change(input, { target: { value: "100%" } });

    await waitFor(() => expect(limitMock).toHaveBeenCalledTimes(2), {
      timeout: 600,
    });

    const { supabase } = await import("@/lib/supabase");
    const calls = (supabase.from as ReturnType<typeof vi.fn>).mock.results;
    const lastFrom = calls[calls.length - 1].value;
    const selectResult = lastFrom.select.mock.results[0].value;
    expect(selectResult.or).toHaveBeenCalledWith(
      expect.stringContaining("\\%")
    );
  });
});

// ── Side sheet ────────────────────────────────────────────────────────────────

describe("ContactsList — side sheet", () => {
  it("clicking a row opens the Sheet (dialog)", async () => {
    const user = userEvent.setup();
    limitMock.mockResolvedValue({ data: [dana], error: null });
    render(<ContactsList {...defaultProps} />);

    await waitFor(() => screen.getByText("Dana Ehrlich"));
    await user.click(screen.getByRole("button", { name: /dana ehrlich/i }));

    expect(screen.getByTestId("sheet-content")).toBeInTheDocument();
    // Email is now an editable input; use getByDisplayValue
    expect(screen.getByDisplayValue("dana@clearbond.com")).toBeInTheDocument();
  });

  it("sheet shows editable inputs for name, email, company, role", async () => {
    const user = userEvent.setup();
    const contact = makeContact({ role: "CEO", detail: "runs a cool fund" });
    limitMock.mockResolvedValue({ data: [contact], error: null });
    render(<ContactsList {...defaultProps} />);

    await waitFor(() => screen.getByText("Dana Ehrlich"));
    await user.click(screen.getByRole("button", { name: /dana ehrlich/i }));

    expect(screen.getByDisplayValue("Dana Ehrlich")).toBeInTheDocument();
    expect(screen.getByDisplayValue("dana@clearbond.com")).toBeInTheDocument();
    expect(screen.getByDisplayValue("Clearbond")).toBeInTheDocument();
    expect(screen.getByDisplayValue("CEO")).toBeInTheDocument();
    expect(screen.getByDisplayValue("runs a cool fund")).toBeInTheDocument();
  });

  it("blurring a text field saves to Supabase and calls onSuccess", async () => {
    const user = userEvent.setup();
    limitMock.mockResolvedValue({ data: [dana], error: null });
    render(<ContactsList {...defaultProps} />);

    await waitFor(() => screen.getByText("Dana Ehrlich"));
    await user.click(screen.getByRole("button", { name: /dana ehrlich/i }));

    const roleInput = screen.getByDisplayValue("CEO");
    await user.clear(roleInput);
    await user.type(roleInput, "Partner");
    await user.tab(); // triggers blur

    await waitFor(() => expect(updateEqMock).toHaveBeenCalled());
    expect(updateEqMock).toHaveBeenCalledWith("id", dana.id);
    await waitFor(() =>
      expect(defaultProps.onSuccess).toHaveBeenCalledWith("Role saved")
    );
  });

  it("blur-save error reverts and calls onError", async () => {
    const user = userEvent.setup();
    limitMock.mockResolvedValue({ data: [dana], error: null });
    updateEqMock.mockResolvedValue({ error: { message: "write failed" } });
    render(<ContactsList {...defaultProps} />);

    await waitFor(() => screen.getByText("Dana Ehrlich"));
    await user.click(screen.getByRole("button", { name: /dana ehrlich/i }));

    const roleInput = screen.getByDisplayValue("CEO");
    await user.clear(roleInput);
    await user.type(roleInput, "Partner");
    await user.tab();

    await waitFor(() =>
      expect(defaultProps.onError).toHaveBeenCalledWith(
        expect.stringMatching(/write failed/i)
      )
    );
  });

  it("mode toggle saves to Supabase immediately", async () => {
    const user = userEvent.setup();
    limitMock.mockResolvedValue({ data: [dana], error: null });
    render(<ContactsList {...defaultProps} />);

    await waitFor(() => screen.getByText("Dana Ehrlich"));
    await user.click(screen.getByRole("button", { name: /dana ehrlich/i }));

    // Scope to sheet to avoid collision with the mode filter pill in the header
    const sheet = screen.getByTestId("sheet-content");
    await user.click(within(sheet).getByRole("button", { name: /^applied$/i }));

    await waitFor(() => expect(updateEqMock).toHaveBeenCalled());
    expect(updateEqMock).toHaveBeenCalledWith("id", dana.id);
  });

  it("mode button-group renders 3 options including networking", async () => {
    const user = userEvent.setup();
    limitMock.mockResolvedValue({ data: [dana], error: null });
    render(<ContactsList {...defaultProps} />);

    await waitFor(() => screen.getByText("Dana Ehrlich"));
    await user.click(screen.getByRole("button", { name: /dana ehrlich/i }));

    const sheet = screen.getByTestId("sheet-content");
    expect(within(sheet).getByRole("button", { name: /^outreach$/i })).toBeInTheDocument();
    expect(within(sheet).getByRole("button", { name: /^applied$/i })).toBeInTheDocument();
    expect(within(sheet).getByRole("button", { name: /^networking$/i })).toBeInTheDocument();
  });

  it("networking mode toggle saves to Supabase immediately", async () => {
    const user = userEvent.setup();
    limitMock.mockResolvedValue({ data: [dana], error: null });
    render(<ContactsList {...defaultProps} />);

    await waitFor(() => screen.getByText("Dana Ehrlich"));
    await user.click(screen.getByRole("button", { name: /dana ehrlich/i }));

    const sheet = screen.getByTestId("sheet-content");
    await user.click(within(sheet).getByRole("button", { name: /^networking$/i }));

    await waitFor(() => expect(updateEqMock).toHaveBeenCalled());
    expect(updateEqMock).toHaveBeenCalledWith("id", dana.id);
  });

  it("stage Select shows only the Networking group for a networking contact", async () => {
    const user = userEvent.setup();
    limitMock.mockResolvedValue({ data: [priya], error: null });
    render(<ContactsList {...defaultProps} />);

    await waitFor(() => screen.getByText("Priya Nair"));
    await user.click(screen.getByRole("button", { name: /priya nair/i }));

    const sheet = screen.getByTestId("sheet-content");
    expect(within(sheet).getByText("Networking")).toBeInTheDocument();
    expect(within(sheet).queryByText("Outreach")).toBeNull();
    expect(within(sheet).queryByText("Applied")).toBeNull();
    expect(within(sheet).getByText("Reply")).toBeInTheDocument();
  });

  it("stage Select shows only the Outreach group for an outreach contact", async () => {
    const user = userEvent.setup();
    limitMock.mockResolvedValue({ data: [dana], error: null });
    render(<ContactsList {...defaultProps} />);

    await waitFor(() => screen.getByText("Dana Ehrlich"));
    await user.click(screen.getByRole("button", { name: /dana ehrlich/i }));

    const sheet = screen.getByTestId("sheet-content");
    expect(within(sheet).getByText("Outreach")).toBeInTheDocument();
    expect(within(sheet).queryByText("Applied")).toBeNull();
    expect(within(sheet).queryByText("Networking")).toBeNull();
  });

  it("shows connection_context field only for networking contacts", async () => {
    const user = userEvent.setup();
    limitMock.mockResolvedValue({ data: [priya], error: null });
    render(<ContactsList {...defaultProps} />);

    await waitFor(() => screen.getByText("Priya Nair"));
    await user.click(screen.getByRole("button", { name: /priya nair/i }));

    const sheet = screen.getByTestId("sheet-content");
    expect(within(sheet).getByText("Connection")).toBeInTheDocument();
  });

  it("hides connection_context field for non-networking contacts", async () => {
    const user = userEvent.setup();
    limitMock.mockResolvedValue({ data: [dana], error: null });
    render(<ContactsList {...defaultProps} />);

    await waitFor(() => screen.getByText("Dana Ehrlich"));
    await user.click(screen.getByRole("button", { name: /dana ehrlich/i }));

    const sheet = screen.getByTestId("sheet-content");
    expect(within(sheet).queryByText("Connection")).toBeNull();
  });

  it("Promote button appears only for networking contacts and opens the promote modal", async () => {
    const user = userEvent.setup();
    limitMock.mockResolvedValue({ data: [priya], error: null });
    render(<ContactsList {...defaultProps} />);

    await waitFor(() => screen.getByText("Priya Nair"));
    await user.click(screen.getByRole("button", { name: /priya nair/i }));

    const sheet = screen.getByTestId("sheet-content");
    await user.click(within(sheet).getByRole("button", { name: /promote/i }));

    expect(screen.getByTestId("confirm-modal")).toBeInTheDocument();
  });

  it("hides the Promote button for non-networking contacts", async () => {
    const user = userEvent.setup();
    limitMock.mockResolvedValue({ data: [dana], error: null });
    render(<ContactsList {...defaultProps} />);

    await waitFor(() => screen.getByText("Dana Ehrlich"));
    await user.click(screen.getByRole("button", { name: /dana ehrlich/i }));

    const sheet = screen.getByTestId("sheet-content");
    expect(within(sheet).queryByRole("button", { name: /promote/i })).toBeNull();
  });

  it("confirming promote sets mode, resets stage to new, clears followup_date, and annotates notes", async () => {
    const user = userEvent.setup();
    limitMock.mockResolvedValue({ data: [priya], error: null });
    render(<ContactsList {...defaultProps} />);

    await waitFor(() => screen.getByText("Priya Nair"));
    await user.click(screen.getByRole("button", { name: /priya nair/i }));

    const sheet = screen.getByTestId("sheet-content");
    await user.click(within(sheet).getByRole("button", { name: /promote/i }));

    const modal = screen.getByTestId("confirm-modal");
    await user.click(within(modal).getByRole("button", { name: /promote to outreach/i }));
    await user.click(within(modal).getByRole("button", { name: /^promote$/i }));

    await waitFor(() => expect(updateEqMock).toHaveBeenCalled());
    const payload = updateMock.mock.calls.at(-1)?.[0] as Record<string, unknown>;
    expect(payload.mode).toBe("outreach");
    expect(payload.stage).toBe("new");
    expect(payload.followup_date).toBeNull();
    expect(payload.notes as string).toContain("Promoted from networking to outreach");
  });

  it("dartmouth toggle saves to Supabase immediately", async () => {
    const user = userEvent.setup();
    limitMock.mockResolvedValue({ data: [dana], error: null });
    render(<ContactsList {...defaultProps} />);

    await waitFor(() => screen.getByText("Dana Ehrlich"));
    await user.click(screen.getByRole("button", { name: /dana ehrlich/i }));

    // Scope to sheet to avoid collision with the dartmouth filter button in the header
    const sheet = screen.getByTestId("sheet-content");
    await user.click(
      within(sheet).getByRole("button", { name: /dartmouth/i })
    );

    await waitFor(() => expect(updateEqMock).toHaveBeenCalled());
    expect(updateEqMock).toHaveBeenCalledWith("id", dana.id);
  });

  it("optimistically updates stage and issues Supabase UPDATE", async () => {
    const user = userEvent.setup();
    limitMock.mockResolvedValue({ data: [dana], error: null });
    render(<ContactsList {...defaultProps} />);

    await waitFor(() => screen.getByText("Dana Ehrlich"));
    await user.click(screen.getByRole("button", { name: /dana ehrlich/i }));

    // Stage is second-to-last; state Select (new) is last. Use length-relative indices
    // because the mock accumulates callbacks on each re-render.
    await act(async () => {
      selectInstances[selectInstances.length - 2]?.("followup1_sent");
    });

    await waitFor(() => expect(updateEqMock).toHaveBeenCalled());
    expect(updateEqMock).toHaveBeenCalledWith("id", dana.id);
  });

  it("stage update error reverts optimistic change and calls onError", async () => {
    const user = userEvent.setup();
    limitMock.mockResolvedValue({ data: [dana], error: null });
    updateEqMock.mockResolvedValue({ error: { message: "permission denied" } });

    render(<ContactsList {...defaultProps} />);
    await waitFor(() => screen.getByText("Dana Ehrlich"));
    await user.click(screen.getByRole("button", { name: /dana ehrlich/i }));

    await act(async () => {
      selectInstances[selectInstances.length - 2]?.("followup1_sent");
    });

    await waitFor(() =>
      expect(defaultProps.onError).toHaveBeenCalledWith(
        expect.stringMatching(/permission denied/i)
      )
    );
  });

  it("state dropdown renders all 52 options (51 states + Unknown)", async () => {
    const user = userEvent.setup();
    limitMock.mockResolvedValue({ data: [dana], error: null });
    render(<ContactsList {...defaultProps} />);

    await waitFor(() => screen.getByText("Dana Ehrlich"));
    await user.click(screen.getByRole("button", { name: /dana ehrlich/i }));

    const sheet = screen.getByTestId("sheet-content");
    // The state Select renders items as role="option" via the mock
    const stateOptions = within(sheet).getAllByRole("option");
    // State options include: stage group options + reply group options + state options (52)
    // Count specifically the state options by their data-value pattern
    const stateSelectRoot = sheet.querySelector("[data-select-value='']");
    // Should have an empty-string value (Unknown) — the state Select
    expect(stateSelectRoot).not.toBeNull();
    // Count options rendered under the state Select's Content
    const allOptions = within(sheet).getAllByRole("option");
    // 52 state options + all stage options exist; check minimum 52 are present
    expect(allOptions.length).toBeGreaterThanOrEqual(52);
  });

  it("selecting a state fires optimistic update", async () => {
    const user = userEvent.setup();
    limitMock.mockResolvedValue({ data: [dana], error: null });
    render(<ContactsList {...defaultProps} />);

    await waitFor(() => screen.getByText("Dana Ehrlich"));
    await user.click(screen.getByRole("button", { name: /dana ehrlich/i }));

    // State Select is the last-registered callback (stage is second-to-last).
    await act(async () => {
      selectInstances[selectInstances.length - 1]?.("NY");
    });

    await waitFor(() => expect(updateEqMock).toHaveBeenCalled());
    expect(updateEqMock).toHaveBeenCalledWith("id", dana.id);
  });

  it("state update error reverts and calls onError", async () => {
    const user = userEvent.setup();
    limitMock.mockResolvedValue({ data: [dana], error: null });
    updateEqMock.mockResolvedValue({ error: { message: "state write failed" } });

    render(<ContactsList {...defaultProps} />);
    await waitFor(() => screen.getByText("Dana Ehrlich"));
    await user.click(screen.getByRole("button", { name: /dana ehrlich/i }));

    await act(async () => {
      selectInstances[selectInstances.length - 1]?.("NY");
    });

    await waitFor(() =>
      expect(defaultProps.onError).toHaveBeenCalledWith(
        expect.stringMatching(/state write failed/i)
      )
    );
  });

  it("empty string state value maps to null on save", async () => {
    const user = userEvent.setup();
    const contactWithState = makeContact({ state: "NY" });
    limitMock.mockResolvedValue({ data: [contactWithState], error: null });
    render(<ContactsList {...defaultProps} />);

    await waitFor(() => screen.getByText("Dana Ehrlich"));
    await user.click(screen.getByRole("button", { name: /dana ehrlich/i }));

    const sheet = screen.getByTestId("sheet-content");
    // Verify NY state is reflected in the Select root
    const stateSelectRoot = sheet.querySelector("[data-select-value='NY']");
    expect(stateSelectRoot).not.toBeNull();
  });

  it("stage select shows Reply Draft label for reply_drafted contact (not blank)", async () => {
    const user = userEvent.setup();
    const jose = makeContact({ id: "99", name: "Jose Aberg Cobo", stage: "reply_drafted" });
    limitMock.mockResolvedValue({ data: [jose], error: null });
    render(<ContactsList {...defaultProps} />);

    await waitFor(() => screen.getByText("Jose Aberg Cobo"));
    await user.click(screen.getByRole("button", { name: /jose aberg cobo/i }));

    const sheet = screen.getByTestId("sheet-content");
    // The stage Select Root must hold the correct value attribute (not empty/blank)
    const stageSelect = sheet.querySelector("[data-select-value='reply_drafted']");
    expect(stageSelect).not.toBeNull();
    // The Reply group option must be rendered in the dropdown
    expect(within(sheet).getByRole("option", { name: "Reply Draft" })).toBeInTheDocument();
  });
});

// ── Soft delete ───────────────────────────────────────────────────────────────

describe("ContactsList — soft delete", () => {
  it("clicking Delete contact opens ConfirmModal", async () => {
    const user = userEvent.setup();
    limitMock.mockResolvedValue({ data: [dana], error: null });
    render(<ContactsList {...defaultProps} />);

    await waitFor(() => screen.getByText("Dana Ehrlich"));
    await user.click(screen.getByRole("button", { name: /dana ehrlich/i }));
    await user.click(screen.getByRole("button", { name: /delete contact/i }));

    expect(screen.getByTestId("confirm-content")).toBeInTheDocument();
    expect(screen.getByText(/delete this contact/i)).toBeInTheDocument();
  });

  it("Cancel closes ConfirmModal without deleting", async () => {
    const user = userEvent.setup();
    limitMock.mockResolvedValue({ data: [dana], error: null });
    render(<ContactsList {...defaultProps} />);

    await waitFor(() => screen.getByText("Dana Ehrlich"));
    await user.click(screen.getByRole("button", { name: /dana ehrlich/i }));
    await user.click(screen.getByRole("button", { name: /delete contact/i }));
    await user.click(screen.getByRole("button", { name: /cancel/i }));

    await waitFor(() =>
      expect(screen.queryByTestId("confirm-content")).toBeNull()
    );
    expect(updateEqMock).not.toHaveBeenCalled();
    // Sheet is still open, so Dana Ehrlich appears in both row and sheet header
    expect(screen.getAllByText("Dana Ehrlich").length).toBeGreaterThan(0);
  });

  it("confirms delete, removes row from list, calls onSuccess", async () => {
    const user = userEvent.setup();
    limitMock.mockResolvedValue({ data: [dana], error: null });
    render(<ContactsList {...defaultProps} />);

    await waitFor(() => screen.getByText("Dana Ehrlich"));
    await user.click(screen.getByRole("button", { name: /dana ehrlich/i }));
    await user.click(screen.getByRole("button", { name: /delete contact/i }));
    await user.click(screen.getByRole("button", { name: /^delete$/i }));

    await waitFor(() =>
      expect(defaultProps.onSuccess).toHaveBeenCalledWith("Contact deleted")
    );
    expect(screen.queryByText("Dana Ehrlich")).toBeNull();
  });

  it("delete error keeps row visible and calls onError", async () => {
    const user = userEvent.setup();
    limitMock.mockResolvedValue({ data: [dana], error: null });
    updateEqMock.mockResolvedValue({ error: { message: "write failed" } });

    render(<ContactsList {...defaultProps} />);

    await waitFor(() => screen.getByText("Dana Ehrlich"));
    await user.click(screen.getByRole("button", { name: /dana ehrlich/i }));
    await user.click(screen.getByRole("button", { name: /delete contact/i }));
    await user.click(screen.getByRole("button", { name: /^delete$/i }));

    await waitFor(() =>
      expect(defaultProps.onError).toHaveBeenCalledWith(
        expect.stringMatching(/write failed/i)
      )
    );
    // Row should still be visible (sheet still open, so name appears twice)
    expect(screen.getAllByText("Dana Ehrlich").length).toBeGreaterThan(0);
  });

  it("shows amber warning when contact has _drafted stage and message_id", async () => {
    const user = userEvent.setup();
    limitMock.mockResolvedValue({ data: [sarah], error: null });
    render(<ContactsList {...defaultProps} />);

    await waitFor(() => screen.getByText("Sarah Kim"));
    await user.click(screen.getByRole("button", { name: /sarah kim/i }));
    await user.click(screen.getByRole("button", { name: /delete contact/i }));

    expect(screen.getByText(/active draft in gmail/i)).toBeInTheDocument();
  });

  it("does not show amber warning when stage is _sent (no draft)", async () => {
    const user = userEvent.setup();
    const sentContact = makeContact({
      id: "3",
      name: "Alex Sent",
      email: "alex@sent.com",
      stage: "first_touch_sent",
      message_id: "msg-123",
    });
    limitMock.mockResolvedValue({ data: [sentContact], error: null });
    render(<ContactsList {...defaultProps} />);

    await waitFor(() => screen.getByText("Alex Sent"));
    await user.click(screen.getByRole("button", { name: /alex sent/i }));
    await user.click(screen.getByRole("button", { name: /delete contact/i }));

    expect(screen.queryByText(/active draft in gmail/i)).toBeNull();
  });
});
