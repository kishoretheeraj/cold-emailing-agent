import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
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

// Mock PromptCategory — renders a div per category so we can inspect props
vi.mock("./PromptCategory", () => ({
  PromptCategory: ({
    category,
    prompts,
    isOpen,
    searchActive,
    onToggle,
  }: {
    category: string;
    prompts: Prompt[];
    isOpen: boolean;
    searchActive: boolean;
    onToggle: () => void;
  }) => (
    <div
      data-testid="prompt-category"
      data-category={category}
      data-open={String(isOpen)}
      data-search-active={String(searchActive)}
      onClick={onToggle}
    >
      {prompts.map((p) => (
        <span key={p.key} data-testid="prompt-title">
          {p.display_title}
        </span>
      ))}
    </div>
  ),
}));

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
  Toaster: () => null,
}));

import { PromptsPage } from "./PromptsPage";

function makePrompt(key: string, title: string, sortOrder: number): Prompt {
  return {
    key,
    value: "value",
    display_title: title,
    description: "desc",
    default_value: null,
    sort_order: sortOrder,
    updated_at: "2026-05-01T10:00:00.000Z",
  };
}

const senderProfile = makePrompt("sender_profile", "Sender Profile", 10);
const outreachPrompt = makePrompt("outreach_prompt", "Outreach Email", 20);
const tier1 = makePrompt("tier_1_instruction", "Tier 1 Instruction", 15);
const tier2 = makePrompt("tier_2_instruction", "Tier 2 Instruction", 16);
const appliedIntro = makePrompt("applied_intro_prompt", "Applied Intro", 30);
const dartmouth = makePrompt("dartmouth_instruction", "Dartmouth Instruction", 18);

beforeEach(() => {
  orderMock.mockReset();
  localStorage.clear();
});

describe("PromptsPage", () => {
  it("shows loading state initially", () => {
    orderMock.mockReturnValue(new Promise(() => {}));
    render(<PromptsPage />);
    expect(screen.getByText("Loading...")).toBeInTheDocument();
  });

  it("fetches prompts ordered by sort_order ascending", async () => {
    orderMock.mockResolvedValueOnce({ data: [senderProfile, outreachPrompt], error: null });
    render(<PromptsPage />);

    await waitFor(() => expect(screen.queryByText("Loading...")).toBeNull());

    expect(orderMock).toHaveBeenCalledWith("sort_order", { ascending: true });
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

  it("groups prompts by category and renders one PromptCategory per non-empty category", async () => {
    // senderProfile + outreachPrompt → "Sender & Core"
    // tier1 + tier2 → "Outreach Modifiers"
    // appliedIntro → "Applied"
    orderMock.mockResolvedValueOnce({
      data: [senderProfile, tier1, tier2, outreachPrompt, appliedIntro],
      error: null,
    });
    render(<PromptsPage />);

    await waitFor(() => expect(screen.queryByText("Loading...")).toBeNull());

    const categories = screen.getAllByTestId("prompt-category");
    const names = categories.map((el) => el.getAttribute("data-category"));
    expect(names).toContain("Sender & Core");
    expect(names).toContain("Outreach Modifiers");
    expect(names).toContain("Applied");
    // Categories with no prompts are not rendered
    expect(names).not.toContain("Research Pipeline");
    expect(names).not.toContain("Reply Pipeline");
    expect(names).not.toContain("Retrospective");
  });

  it("only 'Sender & Core' is open by default (localStorage empty)", async () => {
    orderMock.mockResolvedValueOnce({
      data: [senderProfile, tier1, appliedIntro],
      error: null,
    });
    render(<PromptsPage />);

    await waitFor(() => expect(screen.queryByText("Loading...")).toBeNull());

    const categories = screen.getAllByTestId("prompt-category");
    const openMap = Object.fromEntries(
      categories.map((el) => [
        el.getAttribute("data-category"),
        el.getAttribute("data-open"),
      ])
    );
    expect(openMap["Sender & Core"]).toBe("true");
    expect(openMap["Outreach Modifiers"]).toBe("false");
    expect(openMap["Applied"]).toBe("false");
  });

  it("restores open state from localStorage on mount", async () => {
    localStorage.setItem(
      "prompts-open-categories",
      JSON.stringify(["Applied", "Outreach Modifiers"])
    );
    orderMock.mockResolvedValueOnce({
      data: [senderProfile, tier1, appliedIntro],
      error: null,
    });
    render(<PromptsPage />);

    await waitFor(() => expect(screen.queryByText("Loading...")).toBeNull());

    const categories = screen.getAllByTestId("prompt-category");
    const openMap = Object.fromEntries(
      categories.map((el) => [
        el.getAttribute("data-category"),
        el.getAttribute("data-open"),
      ])
    );
    expect(openMap["Sender & Core"]).toBe("false");
    expect(openMap["Outreach Modifiers"]).toBe("true");
    expect(openMap["Applied"]).toBe("true");
  });

  it("persists open state to localStorage when category is toggled", async () => {
    orderMock.mockResolvedValueOnce({ data: [senderProfile, tier1], error: null });
    render(<PromptsPage />);

    await waitFor(() => expect(screen.queryByText("Loading...")).toBeNull());

    // Click "Outreach Modifiers" (currently closed) to open it
    const outreachCat = screen
      .getAllByTestId("prompt-category")
      .find((el) => el.getAttribute("data-category") === "Outreach Modifiers");
    expect(outreachCat).toBeTruthy();
    fireEvent.click(outreachCat!);

    const stored = localStorage.getItem("prompts-open-categories");
    expect(stored).not.toBeNull();
    const parsed = JSON.parse(stored!);
    expect(parsed).toContain("Outreach Modifiers");
  });

  it("search filters prompts by title and passes searchActive=true to categories", async () => {
    orderMock.mockResolvedValueOnce({
      data: [senderProfile, tier1, appliedIntro],
      error: null,
    });
    render(<PromptsPage />);

    await waitFor(() => expect(screen.queryByText("Loading...")).toBeNull());

    const searchInput = screen.getByPlaceholderText("Search prompts...");
    fireEvent.change(searchInput, { target: { value: "tier" } });

    // Only "Outreach Modifiers" category should be visible (tier1 matches "tier")
    const categories = screen.getAllByTestId("prompt-category");
    const names = categories.map((el) => el.getAttribute("data-category"));
    expect(names).toContain("Outreach Modifiers");
    expect(names).not.toContain("Sender & Core");
    expect(names).not.toContain("Applied");

    // searchActive prop is true on all rendered categories
    categories.forEach((el) => {
      expect(el.getAttribute("data-search-active")).toBe("true");
    });
  });

  it("search matches on description as well as title", async () => {
    const withDesc: Prompt = {
      ...senderProfile,
      display_title: "Sender Profile",
      description: "unique_description_token",
    };
    orderMock.mockResolvedValueOnce({ data: [withDesc, appliedIntro], error: null });
    render(<PromptsPage />);

    await waitFor(() => expect(screen.queryByText("Loading...")).toBeNull());

    fireEvent.change(screen.getByPlaceholderText("Search prompts..."), {
      target: { value: "unique_description_token" },
    });

    const names = screen
      .getAllByTestId("prompt-category")
      .map((el) => el.getAttribute("data-category"));
    expect(names).toContain("Sender & Core");
    expect(names).not.toContain("Applied");
  });

  it("unknown prompt key is placed in 'Shared' category", async () => {
    const unknown = makePrompt("future_unknown_key", "Future Prompt", 99);
    orderMock.mockResolvedValueOnce({ data: [unknown], error: null });
    render(<PromptsPage />);

    await waitFor(() => expect(screen.queryByText("Loading...")).toBeNull());

    const categories = screen.getAllByTestId("prompt-category");
    const names = categories.map((el) => el.getAttribute("data-category"));
    expect(names).toContain("Shared");
    expect(names).not.toContain("Sender & Core");
  });

  it("empty categories are not rendered", async () => {
    // Only sender_profile (Sender & Core) — no other categories have prompts
    orderMock.mockResolvedValueOnce({ data: [senderProfile], error: null });
    render(<PromptsPage />);

    await waitFor(() => expect(screen.queryByText("Loading...")).toBeNull());

    const categories = screen.getAllByTestId("prompt-category");
    expect(categories).toHaveLength(1);
    expect(categories[0].getAttribute("data-category")).toBe("Sender & Core");
  });

  it("dartmouth_instruction appears in Shared category", async () => {
    orderMock.mockResolvedValueOnce({ data: [dartmouth], error: null });
    render(<PromptsPage />);

    await waitFor(() => expect(screen.queryByText("Loading...")).toBeNull());

    const categories = screen.getAllByTestId("prompt-category");
    const names = categories.map((el) => el.getAttribute("data-category"));
    expect(names).toContain("Shared");
  });
});
