"use client";

import { useEffect, useState, useCallback } from "react";
import { toast } from "sonner";
import { supabase } from "@/lib/supabase";
import { LabContactPicker } from "./LabContactPicker";
import {
  LabPromptEditor,
  getActiveTabForContact,
  type LabMode,
  type PromptTab,
} from "./LabPromptEditor";
import { LabPreviewPanel, type PreviewResult } from "./LabPreviewPanel";
import { ConfirmModal } from "@/components/ui/ConfirmModal";
import {
  deriveAction,
  assembleUserMessage as _assembleUserMessage,
} from "@/lib/assembleUserMessage";
import type { Contact, Prompt } from "@/lib/types";

// ── Diff helper ────────────────────────────────────────────────────────────────

function computeDiff(saved: string, sandbox: string): string {
  const savedLines = saved.split("\n");
  const sandboxLines = sandbox.split("\n");
  const maxLen = Math.max(savedLines.length, sandboxLines.length);
  const lines: string[] = [];
  for (let i = 0; i < maxLen; i++) {
    const s = savedLines[i];
    const n = sandboxLines[i];
    if (s === n) {
      if (s !== undefined) lines.push(`  ${s}`);
    } else {
      if (s !== undefined) lines.push(`- ${s}`);
      if (n !== undefined) lines.push(`+ ${n}`);
    }
  }
  return lines.join("\n");
}

// ── Component ──────────────────────────────────────────────────────────────────

export function LabRoot() {
  // ── State ──────────────────────────────────────────────────────────────────

  const [selectedContact, setSelectedContact] = useState<Contact | null>(null);
  const [mode, setMode] = useState<LabMode>("writer");
  const [savedPrompts, setSavedPrompts] = useState<Record<string, string>>({});
  const [sandboxValues, setSandboxValues] = useState<Record<string, string>>({});
  const [activeTab, setActiveTab] = useState<PromptTab>("sender_profile");

  // Critic inputs
  const [criticDraftBody, setCriticDraftBody] = useState("");
  const [criticDraftSubject, setCriticDraftSubject] = useState("");

  // Preview
  const [sandboxPreview, setSandboxPreview] = useState<PreviewResult | null>(null);
  const [savedPreview, setSavedPreview] = useState<PreviewResult | null>(null);
  const [sandboxLoading, setSandboxLoading] = useState(false);
  const [savedLoading, setSavedLoading] = useState(false);

  // Save dialog
  const [saveDialogOpen, setSaveDialogOpen] = useState(false);
  const [saving, setSaving] = useState(false);

  // ── Load prompts on mount ─────────────────────────────────────────────────

  useEffect(() => {
    async function load() {
      const { data } = await supabase.from("prompts").select("key,value");
      const map: Record<string, string> = {};
      for (const row of (data ?? []) as Prompt[]) {
        map[row.key] = row.value;
      }
      setSavedPrompts(map);
      setSandboxValues(map);
    }
    void load();
  }, []);

  // ── Auto-select tab when contact or mode changes ──────────────────────────

  useEffect(() => {
    if (!selectedContact) return;
    if (mode === "critic") {
      setActiveTab("critic_prompt");
      return;
    }
    const action = deriveAction(selectedContact);
    const recommended = getActiveTabForContact(selectedContact, action);
    setActiveTab(recommended);
  }, [selectedContact?.id, mode]); // eslint-disable-line react-hooks/exhaustive-deps

  // ── Contact selection ─────────────────────────────────────────────────────

  const handleContactSelect = useCallback((c: Contact) => {
    setSelectedContact(c);
    setSandboxPreview(null);
    setSavedPreview(null);
  }, []);

  // ── Tab change ────────────────────────────────────────────────────────────

  function handleTabChange(tab: PromptTab) {
    setActiveTab(tab);
    setSandboxPreview(null);
    setSavedPreview(null);
  }

  // ── Sandbox edit ──────────────────────────────────────────────────────────

  function handleSandboxChange(value: string) {
    setSandboxValues((prev) => ({ ...prev, [activeTab]: value }));
  }

  // ── Preview ───────────────────────────────────────────────────────────────

  const handlePreview = useCallback(async () => {
    if (!selectedContact) return;

    const sandboxVal = sandboxValues[activeTab] ?? savedPrompts[activeTab] ?? "";
    const savedVal = savedPrompts[activeTab] ?? "";
    const isDifferent = sandboxVal !== savedVal;

    setSandboxLoading(true);
    setSavedLoading(isDifferent);
    setSandboxPreview(null);
    setSavedPreview(null);

    const body = {
      contact_id: String(selectedContact.id),
      active_prompt_key: activeTab,
      sandbox_value: sandboxVal,
      mode,
      ...(mode === "critic"
        ? { critic_draft_body: criticDraftBody, critic_draft_subject: criticDraftSubject }
        : {}),
    };

    // Fire sandbox call (always)
    const sandboxFetch = fetch("/api/preview-draft", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    })
      .then((r) => r.json() as Promise<PreviewResult>)
      .then((r) => { setSandboxPreview(r); setSandboxLoading(false); })
      .catch(() => { toast.error("Preview failed"); setSandboxLoading(false); });

    // Fire saved call in parallel only when sandbox differs from saved
    const savedFetch = isDifferent
      ? fetch("/api/preview-draft", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ ...body, sandbox_value: savedVal }),
        })
          .then((r) => r.json() as Promise<PreviewResult>)
          .then((r) => { setSavedPreview(r); setSavedLoading(false); })
          .catch(() => { setSavedLoading(false); })
      : Promise.resolve();

    await Promise.all([sandboxFetch, savedFetch]);
  }, [
    selectedContact,
    sandboxValues,
    savedPrompts,
    activeTab,
    mode,
    criticDraftBody,
    criticDraftSubject,
  ]);

  // ── Save ──────────────────────────────────────────────────────────────────

  async function handleSaveConfirm() {
    setSaving(true);
    const value = sandboxValues[activeTab] ?? "";
    const now = new Date().toISOString();
    const { error } = await supabase
      .from("prompts")
      .update({ value, updated_at: now })
      .eq("key", activeTab);

    setSaving(false);
    if (error) {
      toast.error("Save failed — check Supabase connection");
      return;
    }
    setSavedPrompts((prev) => ({ ...prev, [activeTab]: value }));
    setSavedPreview(null);
    setSaveDialogOpen(false);
    toast.success("Saved");
  }

  // ── Derived values ────────────────────────────────────────────────────────

  const action = selectedContact ? deriveAction(selectedContact) : null;
  const sandboxVal = sandboxValues[activeTab] ?? savedPrompts[activeTab] ?? "";
  const savedVal = savedPrompts[activeTab] ?? "";
  const canSave = sandboxVal !== savedVal;
  const diff = canSave ? computeDiff(savedVal, sandboxVal) : "";

  // ── Render ────────────────────────────────────────────────────────────────

  return (
    <div className="flex flex-col h-full min-h-screen bg-bg">
      {/* Contact picker */}
      <div className="px-4 sm:px-6 pt-4 pb-2 shrink-0">
        <LabContactPicker
          selectedContact={selectedContact}
          onSelect={handleContactSelect}
        />
      </div>

      {/* Mode toggle */}
      <div className="px-4 sm:px-6 pb-3 shrink-0">
        <div className="flex items-center gap-1.5">
          {(["writer", "critic"] as const).map((m) => (
            <button
              key={m}
              type="button"
              data-testid={`mode-${m}`}
              onClick={() => setMode(m)}
              className={[
                "rounded-lg border px-3 py-1.5 text-xs capitalize transition",
                mode === m
                  ? "border-indigo-500/40 bg-indigo-500/10 text-indigo-300"
                  : "border-border text-fg-muted hover:text-fg hover:border-border-strong",
              ].join(" ")}
            >
              {m}
            </button>
          ))}
        </div>
      </div>

      {/* Critic inputs (only in critic mode) */}
      {mode === "critic" && (
        <div className="px-4 sm:px-6 pb-3 shrink-0 space-y-2">
          <input
            type="text"
            placeholder="Subject of draft to critique…"
            value={criticDraftSubject}
            onChange={(e) => setCriticDraftSubject(e.target.value)}
            className="w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm text-fg placeholder:text-fg-dim focus:outline-none focus:border-border-strong"
          />
          <textarea
            placeholder="Paste draft body to critique…"
            value={criticDraftBody}
            onChange={(e) => setCriticDraftBody(e.target.value)}
            rows={5}
            className="w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm text-fg placeholder:text-fg-dim focus:outline-none focus:border-border-strong resize-y"
          />
        </div>
      )}

      {/* Two-column layout: editor left, preview right */}
      <div className="flex flex-1 gap-0 overflow-hidden px-4 sm:px-6 pb-6">
        {/* Editor — ~40% */}
        <div className="w-2/5 shrink-0 pr-4 flex flex-col overflow-y-auto">
          <LabPromptEditor
            mode={mode}
            activeTab={activeTab}
            onTabChange={handleTabChange}
            contact={selectedContact}
            action={action}
            sandboxValue={sandboxVal}
            savedValue={savedVal}
            onSandboxChange={handleSandboxChange}
            onPreview={() => void handlePreview()}
            previewLoading={sandboxLoading}
            canSave={canSave}
            onSave={() => setSaveDialogOpen(true)}
          />
        </div>

        {/* Preview — ~60% */}
        <div className="flex-1 overflow-y-auto pl-4 border-l border-border">
          <LabPreviewPanel
            sandboxPreview={sandboxPreview}
            savedPreview={savedPreview}
            sandboxLoading={sandboxLoading}
            savedLoading={savedLoading}
            contactSelected={selectedContact !== null}
          />
        </div>
      </div>

      {/* Save dialog */}
      <ConfirmModal
        open={saveDialogOpen}
        title={`Save "${activeTab}"?`}
        body={
          <div className="space-y-4">
            <p>
              This will overwrite the saved prompt. The change takes effect on the
              next agent run (4:37am EST).
            </p>

            {/* Diff */}
            {diff && (
              <div>
                <div className="text-xs uppercase tracking-wider text-fg-dim mb-1">
                  Changes
                </div>
                <pre className="text-xs font-mono rounded-lg border border-border bg-surface-2 p-3 overflow-x-auto max-h-48 whitespace-pre-wrap leading-relaxed">
                  {diff}
                </pre>
              </div>
            )}

            {/* Final preview */}
            {sandboxPreview?.kind === "writer" && (
              <div>
                <div className="text-xs uppercase tracking-wider text-fg-dim mb-1">
                  Preview for {selectedContact?.name ?? "contact"}
                </div>
                <pre className="text-xs font-mono rounded-lg border border-border bg-surface-2 p-3 overflow-x-auto max-h-48 whitespace-pre-wrap leading-relaxed text-fg-muted">
                  {sandboxPreview.body}
                </pre>
              </div>
            )}
          </div>
        }
        confirmLabel="Save"
        loading={saving}
        onConfirm={() => void handleSaveConfirm()}
        onCancel={() => setSaveDialogOpen(false)}
      />
    </div>
  );
}
