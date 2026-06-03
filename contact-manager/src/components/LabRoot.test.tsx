import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { LabRoot } from "./LabRoot";

// ── Mocks ─────────────────────────────────────────────────────────────────────

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
  Toaster: () => null,
}));

// Radix Dialog for ConfirmModal
vi.mock("@radix-ui/react-dialog", () => ({
  Root: ({ children, open }: { children: React.ReactNode; open?: boolean }) =>
    open ? <div data-testid="dialog-root">{children}</div> : null,
  Portal: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  Overlay: () => <div data-testid="dialog-overlay" />,
  Content: ({ children }: { children: React.ReactNode }) => (
    <div data-testid="dialog-content">{children}</div>
  ),
  Title: ({ children }: { children: React.ReactNode }) => <h2>{children}</h2>,
  Description: ({ children, asChild }: { children: React.ReactNode; asChild?: boolean }) =>
    asChild ? <>{children}</> : <p>{children}</p>,
}));

// vi.hoisted so these are available inside vi.mock factory below
const { limitMock, singleMock, updateMock } = vi.hoisted(() => ({
  limitMock: vi.fn(),
  singleMock: vi.fn(),
  updateMock: vi.fn(),
}));

vi.mock("@/lib/supabase", () => {
  // Shared chain for contacts list queries
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const chain: Record<string, any> = {};
  for (const m of ["is", "order", "or"]) {
    chain[m] = vi.fn(() => chain);
  }
  chain.eq = vi.fn(() => ({ single: singleMock }));
  chain.limit = limitMock;

  return {
    supabase: {
      from: vi.fn((table: string) => {
        if (table === "prompts") {
          return {
            select: vi.fn().mockResolvedValue({
              data: [
                { key: "sender_profile", value: "I am Kishore." },
                { key: "outreach_prompt", value: "Write for {name}." },
              ],
            }),
            // update().eq() resolves to { error: null } via updateMock
            update: vi.fn(() => ({ eq: vi.fn().mockResolvedValue({ error: null }) })),
          };
        }
        // contacts table
        return { select: vi.fn(() => chain) };
      }),
    },
  };
});

// ── Fixtures ───────────────────────────────────────────────────────────────────

const CONTACT = {
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
  // clearAllMocks clears call history but preserves factory implementations
  vi.clearAllMocks();
  limitMock.mockResolvedValue({ data: [CONTACT] });
  singleMock.mockResolvedValue({ data: CONTACT });
  updateMock.mockResolvedValue({ error: null });

  global.fetch = vi.fn().mockResolvedValue({
    json: () =>
      Promise.resolve({ kind: "writer", body: "Dear Alice, let's connect." }),
  });
});

// ── Tests ──────────────────────────────────────────────────────────────────────

describe("LabRoot", () => {
  it("renders contact picker and mode toggle", async () => {
    render(<LabRoot />);
    await waitFor(() => {
      expect(screen.getByTestId("contact-picker-toggle")).toBeInTheDocument();
    });
    expect(screen.getByTestId("mode-writer")).toBeInTheDocument();
    expect(screen.getByTestId("mode-critic")).toBeInTheDocument();
  });

  it("switches to critic mode when Critic button is clicked", async () => {
    render(<LabRoot />);
    await waitFor(() => screen.getByTestId("mode-critic"));
    fireEvent.click(screen.getByTestId("mode-critic"));
    await waitFor(() => {
      expect(screen.getByPlaceholderText(/subject of draft/i)).toBeInTheDocument();
    });
  });

  it("switches back to writer mode", async () => {
    render(<LabRoot />);
    await waitFor(() => screen.getByTestId("mode-critic"));
    fireEvent.click(screen.getByTestId("mode-critic"));
    fireEvent.click(screen.getByTestId("mode-writer"));
    await waitFor(() => {
      expect(screen.queryByPlaceholderText(/subject of draft/i)).not.toBeInTheDocument();
    });
  });

  it("opens save dialog when Save button is clicked with unsaved changes", async () => {
    render(<LabRoot />);
    await waitFor(() => screen.getByTestId("contact-picker-toggle"));

    // Edit the textarea to create a diff
    await waitFor(() => screen.getByRole("textbox", { name: /prompt editor/i }));
    fireEvent.change(screen.getByRole("textbox", { name: /prompt editor/i }), {
      target: { value: "Modified prompt text." },
    });

    const saveBtn = screen.getByRole("button", { name: /^save$/i });
    await waitFor(() => expect(saveBtn).toBeEnabled());

    fireEvent.click(saveBtn);
    await waitFor(() => {
      expect(screen.getByTestId("dialog-content")).toBeInTheDocument();
    });
  });

  it("shows 'no contact selected' initially", async () => {
    render(<LabRoot />);
    await waitFor(() => {
      expect(screen.getByText(/no contact selected/i)).toBeInTheDocument();
    });
  });

  it("shows preview after Preview button click with a contact selected", async () => {
    render(<LabRoot />);

    // Open picker and select contact
    await waitFor(() => screen.getByTestId("contact-picker-toggle"));
    fireEvent.click(screen.getByTestId("contact-picker-toggle"));
    await waitFor(() => screen.getByText("Alice Chen"));
    fireEvent.click(screen.getByText("Alice Chen"));
    // Wait for dropdown to close
    await waitFor(() =>
      expect(screen.queryByPlaceholderText(/search contacts/i)).not.toBeInTheDocument()
    );

    // Click preview
    const previewBtn = screen.getByRole("button", { name: /preview/i });
    await waitFor(() => expect(previewBtn).toBeEnabled());
    fireEvent.click(previewBtn);

    await waitFor(() => {
      expect(screen.getByText(/Dear Alice/i)).toBeInTheDocument();
    });
  });
});
