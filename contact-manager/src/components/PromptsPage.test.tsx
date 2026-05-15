import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import type { Prompt } from "@/lib/types";

// Mock supabase chain: supabase.from("prompts").select("*").order(...)
const orderMock = vi.fn();

vi.mock("@/lib/supabase", () => ({
  supabase: {
    from: vi.fn(() => ({
      select: vi.fn(() => ({
        order: orderMock,
      })),
    })),
  },
}));

// Mock PromptSection to avoid deep supabase update chain in page-level tests
vi.mock("./PromptSection", () => ({
  PromptSection: ({ prompt }: { prompt: Prompt }) => (
    <div data-testid="prompt-section">{prompt.display_title}</div>
  ),
}));

import { PromptsPage } from "./PromptsPage";

const mockPrompts: Prompt[] = [
  {
    key: "sender_profile",
    value: "My profile",
    display_title: "Sender Profile",
    description: "Injected as {profile}",
    default_value: "My profile",
    sort_order: 10,
    updated_at: "2026-05-01T10:00:00.000Z",
  },
  {
    key: "outreach_prompt",
    value: "Outreach body",
    display_title: "Outreach Email",
    description: "Used for cold intro",
    default_value: "Outreach body",
    sort_order: 20,
    updated_at: "2026-05-02T10:00:00.000Z",
  },
];

beforeEach(() => {
  orderMock.mockReset();
});

describe("PromptsPage", () => {
  it("shows loading state initially", () => {
    orderMock.mockReturnValue(new Promise(() => {}));
    render(<PromptsPage />);
    expect(screen.getByText("Loading...")).toBeInTheDocument();
  });

  it("fetches prompts ordered by sort_order and renders one PromptSection per row", async () => {
    orderMock.mockResolvedValueOnce({ data: mockPrompts, error: null });
    render(<PromptsPage />);

    await waitFor(() => {
      expect(screen.queryByText("Loading...")).toBeNull();
    });

    expect(orderMock).toHaveBeenCalledWith("sort_order", { ascending: true });
    const sections = screen.getAllByTestId("prompt-section");
    expect(sections).toHaveLength(2);
    expect(sections[0]).toHaveTextContent("Sender Profile");
    expect(sections[1]).toHaveTextContent("Outreach Email");
  });

  it("shows empty state when fetch returns []", async () => {
    orderMock.mockResolvedValueOnce({ data: [], error: null });
    render(<PromptsPage />);

    await waitFor(() => {
      expect(screen.getByText(/No prompts configured/)).toBeInTheDocument();
    });
  });

  it("shows error state when fetch errors", async () => {
    orderMock.mockResolvedValueOnce({
      data: null,
      error: { message: "connection refused" },
    });
    render(<PromptsPage />);

    await waitFor(() => {
      expect(screen.getByText("connection refused")).toBeInTheDocument();
    });
  });
});
