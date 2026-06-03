import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { LabPromptEditor, getActiveTabForContact } from "./LabPromptEditor";
import type { Contact } from "@/lib/types";

// ── Fixture contact ────────────────────────────────────────────────────────────

const CONTACT: Contact = {
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

const DEFAULT_PROPS = {
  mode: "writer" as const,
  activeTab: "outreach_prompt" as const,
  onTabChange: vi.fn(),
  contact: CONTACT,
  action: "send_first_touch" as const,
  sandboxValue: "Write for {name} at {company}.",
  savedValue: "Write for {name} at {company}.",
  onSandboxChange: vi.fn(),
  onPreview: vi.fn(),
  previewLoading: false,
  canSave: false,
  onSave: vi.fn(),
};

// ── getActiveTabForContact ─────────────────────────────────────────────────────

describe("getActiveTabForContact", () => {
  it("returns sender_profile when no contact", () => {
    expect(getActiveTabForContact(null, null)).toBe("sender_profile");
  });

  it("returns specific outreach sub-instruction for each outreach action", () => {
    expect(getActiveTabForContact(CONTACT, "send_first_touch")).toBe("outreach_first_touch_instruction");
    expect(getActiveTabForContact(CONTACT, "send_followup1")).toBe("outreach_followup1_instruction");
    expect(getActiveTabForContact(CONTACT, "send_followup2")).toBe("outreach_followup2_instruction");
    expect(getActiveTabForContact(CONTACT, "send_breakup")).toBe("outreach_breakup_instruction");
  });

  it("returns applied_intro_prompt for send_applied_intro", () => {
    expect(getActiveTabForContact(CONTACT, "send_applied_intro")).toBe("applied_intro_prompt");
  });

  it("returns applied_followup_prompt for send_applied_followup", () => {
    expect(getActiveTabForContact(CONTACT, "send_applied_followup")).toBe("applied_followup_prompt");
  });
});

// ── Tab visibility ─────────────────────────────────────────────────────────────

describe("LabPromptEditor — tab visibility", () => {
  it("shows 9 tabs in writer mode including outreach sub-instructions", () => {
    render(<LabPromptEditor {...DEFAULT_PROPS} />);
    expect(screen.getByRole("tab", { name: /sender profile/i })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /^outreach$/i })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /first touch/i })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /followup 1/i })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /followup 2/i })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /breakup/i })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /applied intro/i })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /applied followup/i })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /subject/i })).toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: /critic/i })).not.toBeInTheDocument();
  });

  it("shows Sender Profile and Critic tabs in critic mode", () => {
    render(<LabPromptEditor {...DEFAULT_PROPS} mode="critic" activeTab="critic_prompt" />);
    expect(screen.getByRole("tab", { name: /sender profile/i })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /critic/i })).toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: /^outreach$/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: /first touch/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: /applied intro/i })).not.toBeInTheDocument();
  });
});

// ── Textarea and sandbox ───────────────────────────────────────────────────────

describe("LabPromptEditor — textarea", () => {
  it("renders the sandbox value in the textarea", () => {
    render(<LabPromptEditor {...DEFAULT_PROPS} sandboxValue="My custom prompt." />);
    const textarea = screen.getByRole("textbox") as HTMLTextAreaElement;
    expect(textarea.value).toBe("My custom prompt.");
  });

  it("calls onSandboxChange when textarea is edited", () => {
    const onSandboxChange = vi.fn();
    render(<LabPromptEditor {...DEFAULT_PROPS} onSandboxChange={onSandboxChange} />);
    const textarea = screen.getByRole("textbox");
    fireEvent.change(textarea, { target: { value: "Updated text." } });
    expect(onSandboxChange).toHaveBeenCalledWith("Updated text.");
  });

  it("shows 'unsaved changes' indicator when sandbox differs from saved", () => {
    render(
      <LabPromptEditor
        {...DEFAULT_PROPS}
        sandboxValue="Modified prompt."
        savedValue="Original prompt."
      />
    );
    expect(screen.getByText(/unsaved changes/i)).toBeInTheDocument();
  });

  it("does not show 'unsaved changes' when sandbox equals saved", () => {
    render(<LabPromptEditor {...DEFAULT_PROPS} />);
    expect(screen.queryByText(/unsaved changes/i)).not.toBeInTheDocument();
  });
});

// ── Variable badge row ─────────────────────────────────────────────────────────

describe("LabPromptEditor — variable badges", () => {
  it("shows variable badges extracted from sandbox value", () => {
    render(<LabPromptEditor {...DEFAULT_PROPS} sandboxValue="Hello {name}, at {company}." />);
    expect(screen.getByText("{name}")).toBeInTheDocument();
    expect(screen.getByText("{company}")).toBeInTheDocument();
  });

  it("shows no badges when there are no variables", () => {
    render(<LabPromptEditor {...DEFAULT_PROPS} sandboxValue="No placeholders here." />);
    expect(screen.queryByText(/\{[a-z_]+\}/)).not.toBeInTheDocument();
  });
});

// ── Preview and Save buttons ───────────────────────────────────────────────────

describe("LabPromptEditor — action buttons", () => {
  it("Preview button is present and enabled when contact is set", () => {
    render(<LabPromptEditor {...DEFAULT_PROPS} />);
    const btn = screen.getByRole("button", { name: /preview/i });
    expect(btn).toBeEnabled();
  });

  it("Preview button is disabled when no contact", () => {
    render(<LabPromptEditor {...DEFAULT_PROPS} contact={null} />);
    const btn = screen.getByRole("button", { name: /preview/i });
    expect(btn).toBeDisabled();
  });

  it("Preview button shows loading text when previewLoading", () => {
    render(<LabPromptEditor {...DEFAULT_PROPS} previewLoading />);
    expect(screen.getByText(/previewing/i)).toBeInTheDocument();
  });

  it("Save button is disabled when canSave is false", () => {
    render(<LabPromptEditor {...DEFAULT_PROPS} canSave={false} />);
    expect(screen.getByRole("button", { name: /^save$/i })).toBeDisabled();
  });

  it("Save button is enabled when canSave is true", () => {
    render(<LabPromptEditor {...DEFAULT_PROPS} canSave />);
    expect(screen.getByRole("button", { name: /^save$/i })).toBeEnabled();
  });

  it("calls onPreview when Preview is clicked", () => {
    const onPreview = vi.fn();
    render(<LabPromptEditor {...DEFAULT_PROPS} onPreview={onPreview} />);
    fireEvent.click(screen.getByRole("button", { name: /preview/i }));
    expect(onPreview).toHaveBeenCalledOnce();
  });

  it("calls onSave when Save is clicked and enabled", () => {
    const onSave = vi.fn();
    render(<LabPromptEditor {...DEFAULT_PROPS} canSave onSave={onSave} />);
    fireEvent.click(screen.getByRole("button", { name: /^save$/i }));
    expect(onSave).toHaveBeenCalledOnce();
  });

  it("calls onTabChange when a tab is clicked", () => {
    const onTabChange = vi.fn();
    render(<LabPromptEditor {...DEFAULT_PROPS} onTabChange={onTabChange} />);
    fireEvent.click(screen.getByRole("tab", { name: /subject/i }));
    expect(onTabChange).toHaveBeenCalledWith("subject_prompt");
  });
});
