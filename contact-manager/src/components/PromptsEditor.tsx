"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { supabase } from "@/lib/supabase";
import { Toast, type ToastTone } from "./Toast";
import { DEFAULT_PROMPTS, PROMPT_META } from "@/lib/defaultPrompts";

type PromptRow = {
  value: string;
  updated_at: string | null;
};

export function PromptsEditor() {
  const [rows, setRows] = useState<Record<string, PromptRow> | null>(null);
  const [edits, setEdits] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState<string | null>(null);
  const [toast, setToast] = useState<{ msg: string; tone: ToastTone } | null>(null);

  useEffect(() => {
    async function load() {
      const { data, error } = await supabase
        .from("prompts")
        .select("key, value, updated_at");
      if (error || !data) return;
      const rowMap: Record<string, PromptRow> = {};
      const editMap: Record<string, string> = {};
      for (const r of data) {
        rowMap[r.key] = { value: r.value, updated_at: r.updated_at };
        editMap[r.key] = r.value;
      }
      setRows(rowMap);
      setEdits(editMap);
    }
    load();
  }, []);

  const save = useCallback(
    async (key: string) => {
      if (saving) return;
      setSaving(key);
      try {
        const now = new Date().toISOString();
        const { error } = await supabase
          .from("prompts")
          .update({ value: edits[key], updated_at: now })
          .eq("key", key);
        if (error) throw error;
        setRows((prev) =>
          prev
            ? { ...prev, [key]: { value: edits[key], updated_at: now } }
            : prev
        );
        setToast({ msg: "Saved", tone: "success" });
      } catch {
        setToast({ msg: "Save failed — check Supabase connection", tone: "error" });
      } finally {
        setSaving(null);
      }
    },
    [saving, edits]
  );

  const reset = useCallback((key: string) => {
    const def = DEFAULT_PROMPTS[key];
    if (!def) return;
    setEdits((prev) => ({ ...prev, [key]: def }));
  }, []);

  if (rows === null) {
    return (
      <div className="mx-auto max-w-4xl px-4 sm:px-6 py-12 text-sm text-fg-muted">
        Loading prompts…
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-4xl px-4 sm:px-6 py-8 sm:py-12 space-y-12">
      <header className="flex items-start justify-between gap-4">
        <div className="space-y-1">
          <h1 className="text-2xl sm:text-3xl font-semibold tracking-tight text-fg">
            Prompts & Profile
          </h1>
          <p className="text-sm text-fg-muted">
            Changes take effect on the next agent run (8am EST).
          </p>
        </div>
        <Link
          href="/"
          className="shrink-0 rounded-lg border border-border px-3 py-1.5 text-xs text-fg-muted hover:text-fg hover:border-border-strong transition"
        >
          ← Contacts
        </Link>
      </header>

      {PROMPT_META.map(({ key, label, description, variables, rows: textareaRows }) => {
        const row = rows[key];
        const isDirty = edits[key] !== row?.value;
        return (
          <section key={key} className="space-y-3">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <h2 className="text-base font-semibold text-fg">{label}</h2>
                <p className="text-xs text-fg-muted mt-0.5">{description}</p>
              </div>
              <div className="flex items-center gap-2 shrink-0">
                <button
                  onClick={() => reset(key)}
                  className="rounded-md border border-border px-3 py-1.5 text-xs text-fg-muted hover:text-fg hover:border-border-strong transition"
                >
                  Reset to default
                </button>
                <button
                  onClick={() => save(key)}
                  disabled={saving === key || !isDirty}
                  className="rounded-md bg-indigo-500 px-3 py-1.5 text-xs font-medium text-white transition hover:bg-indigo-400 disabled:bg-indigo-500/30 disabled:cursor-not-allowed"
                >
                  {saving === key ? "Saving…" : "Save changes"}
                </button>
              </div>
            </div>

            {variables && (
              <div className="flex flex-wrap gap-1.5">
                {variables.map((v) => (
                  <code
                    key={v}
                    className="rounded border border-border bg-surface-2 px-1.5 py-0.5 text-xs text-fg-muted font-mono"
                  >
                    {v}
                  </code>
                ))}
              </div>
            )}

            <textarea
              value={edits[key] ?? ""}
              onChange={(e) =>
                setEdits((prev) => ({ ...prev, [key]: e.target.value }))
              }
              rows={textareaRows}
              spellCheck={false}
              className="w-full rounded-lg border border-border bg-surface px-3 py-2.5 font-mono text-xs text-fg leading-relaxed resize-y focus:outline-none focus:border-border-strong"
            />

            <p className="text-xs text-fg-dim">
              Last saved:{" "}
              {row?.updated_at ? formatTimestamp(row.updated_at) : "never"}
              {isDirty && (
                <span className="ml-2 text-yellow-400">unsaved changes</span>
              )}
            </p>
          </section>
        );
      })}

      {toast && (
        <Toast
          message={toast.msg}
          tone={toast.tone}
          onClose={() => setToast(null)}
        />
      )}
    </div>
  );
}

function formatTimestamp(iso: string) {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}
