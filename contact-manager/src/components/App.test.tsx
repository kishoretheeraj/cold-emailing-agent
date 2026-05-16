import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
  Toaster: () => null,
}));

// Mock all Radix / Vaul primitives used transitively by ContactsList
vi.mock("vaul", () => ({
  Drawer: {
    Root: ({ children, open }: { children: React.ReactNode; open?: boolean }) =>
      open ? <>{children}</> : null,
    Portal: ({ children }: { children: React.ReactNode }) => <>{children}</>,
    Overlay: () => <div />,
    Content: ({ children }: { children: React.ReactNode }) => (
      <div role="dialog">{children}</div>
    ),
    Trigger: ({ children }: { children: React.ReactNode }) => <>{children}</>,
    Close: ({ children, onClick, "aria-label": ariaLabel }: { children?: React.ReactNode; onClick?: () => void; "aria-label"?: string }) => (
      <button type="button" onClick={onClick} aria-label={ariaLabel}>
        {children}
      </button>
    ),
    Title: ({ children }: { children: React.ReactNode }) => <h2>{children}</h2>,
    Description: ({ children }: { children: React.ReactNode }) => (
      <p>{children}</p>
    ),
  },
}));

vi.mock("@radix-ui/react-dialog", () => ({
  Root: ({ children, open }: { children: React.ReactNode; open?: boolean }) =>
    open ? <>{children}</> : null,
  Portal: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  Overlay: () => <div />,
  Content: ({ children }: { children: React.ReactNode }) => (
    <div role="dialog">{children}</div>
  ),
  Title: ({ children }: { children: React.ReactNode }) => <h2>{children}</h2>,
  Description: ({ children, asChild }: { children: React.ReactNode; asChild?: boolean }) =>
    asChild ? <>{children}</> : <p>{children}</p>,
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

vi.mock("@radix-ui/react-select", () => ({
  Root: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  Trigger: ({ children }: { children: React.ReactNode }) => (
    <button type="button">{children}</button>
  ),
  Value: () => null,
  Icon: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  Portal: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  Content: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  Viewport: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  Group: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  Label: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  Item: ({ children }: { children: React.ReactNode }) => (
    <div role="option">{children}</div>
  ),
  ItemText: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  ItemIndicator: () => null,
  Separator: () => <hr />,
}));

// Supabase mock — matches new query chain:
// .from("contacts").select("*").is(...).order(...).limit(30)
const limitMock = vi.fn();
vi.mock("@/lib/supabase", () => ({
  supabase: {
    from: vi.fn(() => ({
      select: vi.fn(() => ({
        is: vi.fn(() => ({
          order: vi.fn(() => ({
            limit: limitMock,
            or: vi.fn(() => ({ order: vi.fn(() => ({ limit: limitMock })) })),
          })),
        })),
      })),
      insert: vi.fn().mockResolvedValue({ error: null }),
      update: vi.fn(() => ({
        eq: vi.fn().mockResolvedValue({ error: null }),
      })),
    })),
  },
}));

import { App } from "./App";

beforeEach(() => {
  limitMock.mockReset();
  limitMock.mockResolvedValue({ data: [], error: null });
});

describe("App shell", () => {
  it("renders header and both mode buttons", async () => {
    render(<App />);
    expect(screen.getByText("Cold Email Ops")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Smart Input" })
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Structured Form" })
    ).toBeInTheDocument();
  });

  it("starts in Smart Input mode", () => {
    render(<App />);
    expect(screen.getByPlaceholderText(/Examples:/)).toBeInTheDocument();
  });

  it("toggles to Structured Form mode", async () => {
    const user = userEvent.setup();
    render(<App />);

    await user.click(screen.getByRole("button", { name: "Structured Form" }));
    expect(
      screen.getByRole("button", { name: /Outreach Contact/i })
    ).toBeInTheDocument();
    expect(screen.queryByPlaceholderText(/Examples:/)).toBeNull();
  });

  it("shows the empty state when no contacts exist", async () => {
    render(<App />);
    await waitFor(() =>
      expect(screen.getByText(/No contacts yet/i)).toBeInTheDocument()
    );
  });

  it("renders Prompts & Profile nav link pointing to /prompts", () => {
    render(<App />);
    const link = screen.getByRole("link", { name: /prompts/i });
    expect(link).toBeInTheDocument();
    expect(link).toHaveAttribute("href", "/prompts");
  });
});
