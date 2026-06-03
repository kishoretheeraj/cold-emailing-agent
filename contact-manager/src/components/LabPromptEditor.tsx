"use client";

import { extractVariables } from "@/lib/promptVariables";
import { Badge } from "@/components/ui/Badge";
import type { Contact } from "@/lib/types";
import type { AgentAction } from "@/lib/assembleUserMessage";

// ── Types ──────────────────────────────────────────────────────────────────────

export type LabMode = "writer" | "critic";

export type PromptTab =
  | "sender_profile"
  | "outreach_prompt"
  | "applied_intro_prompt"
  | "applied_followup_prompt"
  | "subject_prompt"
  | "critic_prompt";

type TabDef = { key: PromptTab; label: string; modes: LabMode[] };

// ── Tab definitions ────────────────────────────────────────────────────────────

const ALL_TABS: TabDef[] = [
  { key: "sender_profile",         label: "Sender Profile",  modes: ["writer", "critic"] },
  { key: "outreach_prompt",        label: "Outreach",        modes: ["writer"] },
  { key: "applied_intro_prompt",   label: "Applied Intro",   modes: ["writer"] },
  { key: "applied_followup_prompt",label: "Applied Followup",modes: ["writer"] },
  { key: "subject_prompt",         label: "Subject",         modes: ["writer"] },
  { key: "critic_prompt",          label: "Critic",          modes: ["critic"] },
];

// ── Active-tab detection ───────────────────────────────────────────────────────

// Returns which prompt tab is "active" (what the agent would use) for a contact.
export function getActiveTabForContact(
  contact: Contact | null,
  action: AgentAction | null
): PromptTab {
  if (!contact) return "sender_profile";
  if (!action) return "sender_profile";

  if (
    action === "send_first_touch" ||
    action === "send_followup1" ||
    action === "send_followup2" ||
    action === "send_breakup"
  ) {
    return "outreach_prompt";
  }
  if (action === "send_applied_intro") return "applied_intro_prompt";
  if (action === "send_applied_followup") return "applied_followup_prompt";
  return "sender_profile";
}

// ── Props ──────────────────────────────────────────────────────────────────────

type Props = {
  mode: LabMode;
  activeTab: PromptTab;
  onTabChange: (tab: PromptTab) => void;
  contact: Contact | null;
  action: AgentAction | null;
  sandboxValue: string;
  savedValue: string;
  onSandboxChange: (value: string) => void;
  onPreview: () => void;
  previewLoading: boolean;
  canSave: boolean;
  onSave: () => void;
};

// ── Component ──────────────────────────────────────────────────────────────────

export function LabPromptEditor({
  mode,
  activeTab,
  onTabChange,
  contact,
  action,
  sandboxValue,
  savedValue,
  onSandboxChange,
  onPreview,
  previewLoading,
  canSave,
  onSave,
}: Props) {
  const visibleTabs = ALL_TABS.filter((t) => t.modes.includes(mode));
  const isDirty = sandboxValue !== savedValue;
  const autoSelectedTab = getActiveTabForContact(contact, action);

  const variables = extractVariables(sandboxValue);

  return (
    <div className="flex flex-col h-full">
      {/* Tab strip */}
      <div
        className="flex items-center gap-1 overflow-x-auto pb-2 shrink-0"
        role="tablist"
        aria-label="Prompt tabs"
      >
        {visibleTabs.map((tab) => {
          const isActive = activeTab === tab.key;
          const isAutoSelected = tab.key === autoSelectedTab || tab.key === "sender_profile";
          return (
            <button
              key={tab.key}
              role="tab"
              aria-selected={isActive}
              type="button"
              onClick={() => onTabChange(tab.key)}
              className={[
                "relative shrink-0 rounded-lg border px-3 py-1.5 text-xs transition whitespace-nowrap",
                isActive
                  ? "border-indigo-500/40 bg-indigo-500/10 text-indigo-300"
                  : "border-border text-fg-muted hover:text-fg hover:border-border-strong",
              ].join(" ")}
            >
              {tab.label}
              {isAutoSelected && contact && (
                <span
                  title="Active for this contact"
                  className="absolute -top-1 -right-1 size-2 rounded-full bg-indigo-400"
                  aria-hidden="true"
                />
              )}
            </button>
          );
        })}
      </div>

      {/* Variable chips */}
      {variables.length > 0 && (
        <div className="flex flex-wrap gap-1.5 mb-2 shrink-0">
          {variables.map((v) => (
            <code
              key={v}
              className="rounded border border-border bg-surface-2 px-1.5 py-0.5 text-xs text-fg-muted font-mono"
            >
              {`{${v}}`}
            </code>
          ))}
        </div>
      )}

      {/* Textarea */}
      <textarea
        aria-label={`Prompt editor for ${activeTab}`}
        value={sandboxValue}
        onChange={(e) => onSandboxChange(e.target.value)}
        spellCheck={false}
        className="flex-1 w-full rounded-lg border border-border bg-surface-2 px-3 py-2.5 text-xs font-mono leading-relaxed text-fg placeholder:text-fg-dim focus:outline-none focus:border-border-strong resize-none min-h-[24rem]"
      />

      {/* Footer bar */}
      <div className="flex items-center justify-between gap-2 pt-2 shrink-0">
        <div className="text-xs text-fg-dim">
          {isDirty && <span className="text-amber-400">unsaved changes</span>}
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            disabled={canSave === false}
            onClick={onSave}
            className="rounded-md border border-border px-3 py-1.5 text-xs text-fg-muted hover:text-fg hover:border-border-strong transition disabled:opacity-30 disabled:cursor-not-allowed"
          >
            Save
          </button>
          <button
            type="button"
            disabled={previewLoading || !contact}
            onClick={onPreview}
            className="rounded-md bg-indigo-500 px-3 py-1.5 text-xs font-medium text-white transition hover:bg-indigo-400 disabled:bg-indigo-500/30 disabled:cursor-not-allowed"
          >
            {previewLoading ? "Previewing…" : "Preview"}
          </button>
        </div>
      </div>
    </div>
  );
}
