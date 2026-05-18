import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import type { Prompt } from "@/lib/types";

// Mock PromptSection to avoid the supabase update chain
vi.mock("./PromptSection", () => ({
  PromptSection: ({ prompt }: { prompt: Prompt }) => (
    <div data-testid="prompt-section">{prompt.display_title}</div>
  ),
}));

import { PromptCategory } from "./PromptCategory";

function makePrompt(key: string, title: string): Prompt {
  return {
    key,
    value: "value",
    display_title: title,
    description: null,
    default_value: null,
    sort_order: 1,
    updated_at: "2026-05-01T10:00:00.000Z",
  };
}

const prompts = [makePrompt("key_a", "Prompt A"), makePrompt("key_b", "Prompt B")];

const noop = vi.fn();

describe("PromptCategory", () => {
  it("renders prompt sections when isOpen=true", () => {
    render(
      <PromptCategory
        category="Sender & Core"
        prompts={prompts}
        isOpen={true}
        onToggle={noop}
        searchActive={false}
        onSaved={noop}
        onError={noop}
      />
    );
    expect(screen.getAllByTestId("prompt-section")).toHaveLength(2);
  });

  it("hides prompt sections when isOpen=false and searchActive=false", () => {
    render(
      <PromptCategory
        category="Sender & Core"
        prompts={prompts}
        isOpen={false}
        onToggle={noop}
        searchActive={false}
        onSaved={noop}
        onError={noop}
      />
    );
    expect(screen.queryAllByTestId("prompt-section")).toHaveLength(0);
  });

  it("forces sections open when searchActive=true, even if isOpen=false", () => {
    render(
      <PromptCategory
        category="Sender & Core"
        prompts={prompts}
        isOpen={false}
        onToggle={noop}
        searchActive={true}
        onSaved={noop}
        onError={noop}
      />
    );
    expect(screen.getAllByTestId("prompt-section")).toHaveLength(2);
  });

  it("calls onToggle when header button is clicked and searchActive=false", () => {
    const onToggle = vi.fn();
    render(
      <PromptCategory
        category="Sender & Core"
        prompts={prompts}
        isOpen={false}
        onToggle={onToggle}
        searchActive={false}
        onSaved={noop}
        onError={noop}
      />
    );
    fireEvent.click(screen.getByRole("button"));
    expect(onToggle).toHaveBeenCalledOnce();
  });

  it("does not call onToggle when searchActive=true (toggle disabled during search)", () => {
    const onToggle = vi.fn();
    render(
      <PromptCategory
        category="Sender & Core"
        prompts={prompts}
        isOpen={false}
        onToggle={onToggle}
        searchActive={true}
        onSaved={noop}
        onError={noop}
      />
    );
    fireEvent.click(screen.getByRole("button"));
    expect(onToggle).not.toHaveBeenCalled();
  });

  it("aria-expanded reflects isOpen when not searching", () => {
    const { rerender } = render(
      <PromptCategory
        category="Sender & Core"
        prompts={prompts}
        isOpen={false}
        onToggle={noop}
        searchActive={false}
        onSaved={noop}
        onError={noop}
      />
    );
    expect(screen.getByRole("button")).toHaveAttribute("aria-expanded", "false");

    rerender(
      <PromptCategory
        category="Sender & Core"
        prompts={prompts}
        isOpen={true}
        onToggle={noop}
        searchActive={false}
        onSaved={noop}
        onError={noop}
      />
    );
    expect(screen.getByRole("button")).toHaveAttribute("aria-expanded", "true");
  });

  it("aria-expanded is true when searchActive=true even if isOpen=false", () => {
    render(
      <PromptCategory
        category="Sender & Core"
        prompts={prompts}
        isOpen={false}
        onToggle={noop}
        searchActive={true}
        onSaved={noop}
        onError={noop}
      />
    );
    expect(screen.getByRole("button")).toHaveAttribute("aria-expanded", "true");
  });

  it("displays the category name and prompt count", () => {
    render(
      <PromptCategory
        category="Reply Pipeline"
        prompts={prompts}
        isOpen={false}
        onToggle={noop}
        searchActive={false}
        onSaved={noop}
        onError={noop}
      />
    );
    expect(screen.getByText("Reply Pipeline")).toBeInTheDocument();
    expect(screen.getByText("2")).toBeInTheDocument();
  });
});
