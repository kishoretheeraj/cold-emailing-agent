"use client";

import { useEffect, useState } from "react";
import { supabase } from "@/lib/supabase";
import { TextArea } from "./Field";
import { extractVariables } from "@/lib/promptVariables";
import type { Prompt } from "@/lib/types";

export function PromptSection({
  prompt,
  onSaved,
  onError,
}: {
  prompt: Prompt;
  onSaved: (updated: Prompt) => void;
  onError: (message: string) => void;
}) {
  const [draft, setDraft] = useState(prompt.value);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    setDraft(prompt.value);
  }, [prompt.value]);

  const variables = extractVariables(draft);
  const isDirty = draft !== prompt.value;
  const canReset =
    prompt.default_value !== null && draft !== prompt.default_value;

  async function handleSave() {
    setSaving(true);
    const now = new Date().toISOString();
    const { data, error } = await supabase
      .from("prompts")
      .update({ value: draft, updated_at: now })
      .eq("key", prompt.key)
      .select()
      .single();
    if (error || !data) {
      onError("Save failed — check Supabase connection");
      setSaving(false);
      return;
    }
    onSaved(data as Prompt);
    setSaving(false);
  }

  function handleReset() {
    if (!prompt.default_value) return;
    if (
      !window.confirm(
        "Reset this prompt to its default? Your current changes will be lost."
      )
    )
      return;
    setDraft(prompt.default_value);
  }

  return (
    <div className="rounded-xl border border-border bg-surface p-6 mb-6">
      <div className="flex flex-wrap items-start justify-between gap-3 mb-3">
        <div>
          <h3 className="text-xl font-semibold text-fg">
            {prompt.display_title}
          </h3>
          {prompt.description && (
            <p className="text-sm text-fg-muted mt-1">{prompt.description}</p>
          )}
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <button
            onClick={handleReset}
            disabled={!canReset}
            className="rounded-md border border-border px-3 py-1.5 text-xs text-fg-muted hover:text-fg hover:border-border-strong transition disabled:opacity-40 disabled:cursor-not-allowed"
          >
            Reset to default
          </button>
          <button
            onClick={handleSave}
            disabled={!isDirty || saving}
            className="rounded-md bg-indigo-500 px-3 py-1.5 text-xs font-medium text-white transition hover:bg-indigo-400 disabled:bg-indigo-500/30 disabled:cursor-not-allowed"
          >
            {saving ? "Saving..." : "Save changes"}
          </button>
        </div>
      </div>

      {variables.length > 0 && (
        <div className="flex flex-wrap gap-1.5 mb-3">
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

      <TextArea
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        spellCheck={false}
        className="font-mono text-xs leading-relaxed min-h-[24rem] resize-y"
      />

      <p className="text-xs text-fg-dim mt-3">
        Last saved: {formatTimestamp(prompt.updated_at)}
        {isDirty && (
          <span className="ml-2 text-yellow-400">unsaved changes</span>
        )}
      </p>
    </div>
  );
}

function formatTimestamp(iso: string) {
  const d = new Date(iso);
  if (!iso || Number.isNaN(d.getTime())) return "never";
  return new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
    hour12: true,
  }).format(d);
}
