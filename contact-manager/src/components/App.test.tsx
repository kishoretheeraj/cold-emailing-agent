import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

// Mock the supabase chain — the contacts list calls it on mount.
const limitMock = vi.fn();
vi.mock("@/lib/supabase", () => ({
  supabase: {
    from: vi.fn(() => ({
      select: vi.fn(() => ({
        order: vi.fn(() => ({
          limit: limitMock,
        })),
      })),
      insert: vi.fn().mockResolvedValue({ error: null }),
      update: vi.fn(() => ({
        eq: vi.fn(() => ({
          select: vi.fn(() => ({
            single: vi.fn().mockResolvedValue({ data: {}, error: null }),
          })),
        })),
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
    expect(screen.getByRole("button", { name: "Smart Input" })).toBeInTheDocument();
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
    // The Outreach Contact section button shows up only in Structured Form
    expect(
      screen.getByRole("button", { name: /Outreach Contact/i })
    ).toBeInTheDocument();
    // Smart Input textarea is no longer present
    expect(screen.queryByPlaceholderText(/Examples:/)).toBeNull();
  });

  it("shows the empty state when no contacts exist", async () => {
    render(<App />);
    await waitFor(() =>
      expect(screen.getByText(/No contacts yet/i)).toBeInTheDocument()
    );
  });
});
