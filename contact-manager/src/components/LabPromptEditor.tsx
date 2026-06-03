"use client";

import { Fragment } from "react";
import { extractVariables } from "@/lib/promptVariables";
import type { Contact } from "@/lib/types";
import type { AgentAction } from "@/lib/assembleUserMessage";

// ── Types ──────────────────────────────────────────────────────────────────────

export type LabMode = "writer" | "critic";

export type PromptTab =
  | "sender_profile"
  | "outreach_prompt"
  | "outreach_first_touch_instruction"
  | "outreach_followup1_instruction"
  | "outreach_followup2_instruction"
  | "outreach_breakup_instruction"
  | "applied_intro_prompt"
  | "applied_followup_prompt"
  | "subject_prompt"
  | "critic_prompt";

type TabGroup = "main" | "outreach-master" | "outreach-sub";
type TabDef = { key: PromptTab; label: string; modes: LabMode[]; group: TabGroup };

// ── Tab definitions ────────────────────────────────────────────────────────────

const ALL_TABS: TabDef[] = [
  { key: "sender_profile",                   label: "Sender Profile",  modes: ["writer", "critic"], group: "main" },
  { key: "outreach_prompt",                  label: "Outreach",        modes: ["writer"],           group: "outreach-master" },
  { key: "outreach_first_touch_instruction", label: "First Touch",     modes: ["writer"],           group: "outreach-sub" },
  { key: "outreach_followup1_instruction",   label: "Followup 1",      modes: ["writer"],           group: "outreach-sub" },
  { key: "outreach_followup2_instruction",   label: "Followup 2",      modes: ["writer"],           group: "outreach-sub" },
  { key: "outreach_breakup_instruction",     label: "Breakup",         modes: ["writer"],           group: "outreach-sub" },
  { key: "applied_intro_prompt",             label: "Applied Intro",   modes: ["writer"],           group: "main" },
  { key: "applied_followup_prompt",          label: "Applied Followup",modes: ["writer"],           group: "main" },
  { key: "subject_prompt",                   label: "Subject",         modes: ["writer"],           group: "main" },
  { key: "critic_prompt",                    label: "Critic",          modes: ["critic"],           group: "main" },
];

const OUTREACH_SUB_KEYS = new Set<PromptTab>([
  "outreach_first_touch_instruction",
  "outreach_followup1_instruction",
  "outreach_followup2_instruction",
  "outreach_breakup_instruction",
]);

// ── Active-tab detection ───────────────────────────────────────────────────────

// Returns the specific sub-instruction tab that the agent would use for a contact,
// so users land directly on the relevant prompt (not just the master outreach template).
export function getActiveTabForContact(
  contact: Contact | null,
  action: AgentAction | null
): PromptTab {
  if (!contact) return "sender_profile";
  if (!action) return "sender_profile";

  if (action === "send_first_touch") return "outreach_first_touch_instruction";
  if (action === "send_followup1")   return "outreach_followup1_instruction";
  if (action === "send_followup2")   return "outreach_followup2_instruction";
  if (action === "send_breakup")     return "outreach_breakup_instruction";
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
  const activeIsOutreachSub = OUTREACH_SUB_KEYS.has(activeTab);

  const variables = extractVariables(sandboxValue);

  return (
    <div className="flex flex-col h-full">
      {/* Tab strip */}
      <div
        className="flex items-center gap-1 overflow-x-auto pb-2 shrink-0"
        role="tablist"
        aria-label="Prompt tabs"
      >
        {visibleTabs.map((tab, i) => {
          const isActive = activeTab === tab.key;
          const isSubTab = tab.group === "outreach-sub";
          // Dim the master "Outreach" tab when a sub-instruction tab is active
          const isParentOfActive = tab.key === "outreach_prompt" && activeIsOutreachSub;
          const isAutoSelected = tab.key === autoSelectedTab || tab.key === "sender_profile";

          // Vertical divider before "Applied Intro" (first main tab after the outreach group)
          const prevTab = visibleTabs[i - 1];
          const showDivider =
            tab.group === "main" &&
            (prevTab?.group === "outreach-sub" || prevTab?.group === "outreach-master");

          return (
            <Fragment key={tab.key}>
              {showDivider && (
                <span
                  className="shrink-0 w-px h-4 bg-border self-center mx-0.5"
                  aria-hidden="true"
                />
              )}
              <button
                role="tab"
                aria-selected={isActive}
                type="button"
                onClick={() => onTabChange(tab.key)}
                className={[
                  "relative shrink-0 rounded-lg border transition whitespace-nowrap",
                  isSubTab ? "px-2 py-1 text-[11px]" : "px-3 py-1.5 text-xs",
                  isActive
                    ? "border-indigo-500/40 bg-indigo-500/10 text-indigo-300"
                    : isParentOfActive
                    ? "border-indigo-500/20 bg-indigo-500/5 text-indigo-400/60"
                    : "border-border text-fg-muted hover:text-fg hover:border-border-strong",
                ].join(" ")}
              >
                {isSubTab && (
                  <span className="mr-1 opacity-40 font-mono" aria-hidden="true">↳</span>
                )}
                {tab.label}
                {isAutoSelected && contact && (
                  <span
                    title="Active for this contact"
                    className="absolute -top-1 -right-1 size-2 rounded-full bg-indigo-400"
                    aria-hidden="true"
                  />
                )}
              </button>
            </Fragment>
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
