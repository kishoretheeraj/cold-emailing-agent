import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { Prompt } from "@/lib/types";

// Build the mock supabase chain for:
//   supabase.from("prompts").update({...}).eq("key", key).select().single()
const singleMock = vi.fn();
const selectAfterEqMock = vi.fn(() => ({ single: singleMock }));
const eqMock = vi.fn(() => ({ select: selectAfterEqMock }));
const updateMock = vi.fn(() => ({ eq: eqMock }));

vi.mock("@/lib/supabase", () => ({
  supabase: {
    from: vi.fn(() => ({ update: updateMock })),
  },
}));

import { PromptSection } from "./PromptSection";

const basePrompt: Prompt = {
  key: "outreach_prompt",
  value: "Hello {name} at {company}",
  display_title: "Outreach Email",
  description: "Used for cold intro emails.",
  default_value: "Default outreach value",
  sort_order: 20,
  updated_at: "2026-05-01T10:00:00.000Z",
};

beforeEach(() => {
  updateMock.mockReset();
  singleMock.mockReset();
  updateMock.mockReturnValue({ eq: eqMock });
  eqMock.mockReturnValue({ select: selectAfterEqMock });
  selectAfterEqMock.mockReturnValue({ single: singleMock });
});

describe("PromptSection", () => {
  it("renders display_title, description, value in textarea, formatted updated_at", () => {
    render(
      <PromptSection prompt={basePrompt} onSaved={vi.fn()} onError={vi.fn()} />
    );
    expect(screen.getByText("Outreach Email")).toBeInTheDocument();
    expect(screen.getByText("Used for cold intro emails.")).toBeInTheDocument();
    expect(screen.getByRole("textbox")).toHaveValue("Hello {name} at {company}");
    expect(screen.getByText(/Last saved:/)).toBeInTheDocument();
  });

  it("hides variables row when value has no placeholders", () => {
    render(
      <PromptSection
        prompt={{ ...basePrompt, value: "No placeholders here" }}
        onSaved={vi.fn()}
        onError={vi.fn()}
      />
    );
    expect(document.querySelector("code")).toBeNull();
  });

  it("shows variables row with deduplicated placeholders from draft", () => {
    render(
      <PromptSection
        prompt={{ ...basePrompt, value: "{x} {y} {x}" }}
        onSaved={vi.fn()}
        onError={vi.fn()}
      />
    );
    expect(screen.getByText("{x}")).toBeInTheDocument();
    expect(screen.getByText("{y}")).toBeInTheDocument();
    expect(screen.getAllByText("{x}")).toHaveLength(1);
  });

  it("Save button disabled when draft equals prompt.value", () => {
    render(
      <PromptSection prompt={basePrompt} onSaved={vi.fn()} onError={vi.fn()} />
    );
    expect(screen.getByRole("button", { name: "Save changes" })).toBeDisabled();
  });

  it("Save button enabled after editing textarea", async () => {
    const user = userEvent.setup();
    render(
      <PromptSection prompt={basePrompt} onSaved={vi.fn()} onError={vi.fn()} />
    );
    await user.type(screen.getByRole("textbox"), " extra");
    expect(screen.getByRole("button", { name: "Save changes" })).not.toBeDisabled();
  });

  it("Save calls supabase update with new value and correct key, calls onSaved with returned row", async () => {
    const user = userEvent.setup();
    const onSaved = vi.fn();
    const updatedRow: Prompt = {
      ...basePrompt,
      value: "Hello {name} at {company} extra",
      updated_at: "2026-05-15T12:00:00.000Z",
    };
    singleMock.mockResolvedValueOnce({ data: updatedRow, error: null });

    render(
      <PromptSection prompt={basePrompt} onSaved={onSaved} onError={vi.fn()} />
    );
    await user.type(screen.getByRole("textbox"), " extra");
    await user.click(screen.getByRole("button", { name: "Save changes" }));

    await waitFor(() => {
      expect(updateMock).toHaveBeenCalledWith(
        expect.objectContaining({ value: "Hello {name} at {company} extra" })
      );
      expect(eqMock).toHaveBeenCalledWith("key", "outreach_prompt");
      expect(onSaved).toHaveBeenCalledWith(updatedRow);
    });
  });

  it("Save calls onError on supabase failure", async () => {
    const user = userEvent.setup();
    const onError = vi.fn();
    singleMock.mockResolvedValueOnce({ data: null, error: { message: "DB error" } });

    render(
      <PromptSection prompt={basePrompt} onSaved={vi.fn()} onError={onError} />
    );
    await user.type(screen.getByRole("textbox"), " extra");
    await user.click(screen.getByRole("button", { name: "Save changes" }));

    await waitFor(() => {
      expect(onError).toHaveBeenCalledWith(
        "Save failed — check Supabase connection"
      );
    });
  });

  it("Reset button disabled when default_value is null", () => {
    render(
      <PromptSection
        prompt={{ ...basePrompt, default_value: null }}
        onSaved={vi.fn()}
        onError={vi.fn()}
      />
    );
    expect(screen.getByRole("button", { name: "Reset to default" })).toBeDisabled();
  });

  it("Reset button disabled when draft already equals default_value", () => {
    render(
      <PromptSection
        prompt={{ ...basePrompt, value: "Default outreach value" }}
        onSaved={vi.fn()}
        onError={vi.fn()}
      />
    );
    expect(screen.getByRole("button", { name: "Reset to default" })).toBeDisabled();
  });

  it("Reset shows confirm dialog then sets draft to default_value, does NOT auto-save", async () => {
    const user = userEvent.setup();
    vi.spyOn(window, "confirm").mockReturnValueOnce(true);

    render(
      <PromptSection prompt={basePrompt} onSaved={vi.fn()} onError={vi.fn()} />
    );

    // basePrompt.value !== basePrompt.default_value so reset button is already enabled
    await user.click(screen.getByRole("button", { name: "Reset to default" }));

    expect(window.confirm).toHaveBeenCalledWith(
      "Reset this prompt to its default? Your current changes will be lost."
    );
    expect(screen.getByRole("textbox")).toHaveValue("Default outreach value");
    expect(updateMock).not.toHaveBeenCalled();
  });

  it("resets draft to new prompt.value when prop changes", () => {
    const { rerender } = render(
      <PromptSection prompt={basePrompt} onSaved={vi.fn()} onError={vi.fn()} />
    );
    expect(screen.getByRole("textbox")).toHaveValue("Hello {name} at {company}");

    rerender(
      <PromptSection
        prompt={{ ...basePrompt, value: "New value from parent" }}
        onSaved={vi.fn()}
        onError={vi.fn()}
      />
    );
    expect(screen.getByRole("textbox")).toHaveValue("New value from parent");
  });
});
