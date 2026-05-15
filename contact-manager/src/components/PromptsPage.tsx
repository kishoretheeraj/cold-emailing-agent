"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { supabase } from "@/lib/supabase";
import { PromptSection } from "./PromptSection";
import { Toast, type ToastTone } from "./Toast";
import type { Prompt } from "@/lib/types";

export function PromptsPage() {
  const [prompts, setPrompts] = useState<Prompt[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [toast, setToast] = useState<{
    kind: ToastTone;
    message: string;
  } | null>(null);

  useEffect(() => {
    async function load() {
      const { data, error: err } = await supabase
        .from("prompts")
        .select("*")
        .order("sort_order", { ascending: true });
      if (err) {
        setError(err.message);
        setLoading(false);
        return;
      }
      setPrompts((data as Prompt[]) ?? []);
      setLoading(false);
    }
    load();
  }, []);

  function handleSaved(updated: Prompt) {
    setPrompts((prev) =>
      prev.map((p) => (p.key === updated.key ? updated : p))
    );
    setToast({ kind: "success", message: "Saved" });
  }

  function handleError(message: string) {
    setToast({ kind: "error", message });
  }

  return (
    <div className="mx-auto max-w-4xl px-4 sm:px-6 py-8 sm:py-12">
      <header className="flex items-start justify-between gap-4 mb-10">
        <div className="space-y-1">
          <h1 className="text-2xl font-semibold text-fg">Prompts & Profile</h1>
          <p className="text-sm text-fg-muted">
            Changes take effect on the next agent run (8am EST).
          </p>
        </div>
        <Link
          href="/"
          className="shrink-0 flex items-center gap-1 rounded-lg border border-border px-3 py-1.5 text-xs text-fg-muted hover:text-fg hover:border-border-strong transition"
        >
          <svg
            xmlns="http://www.w3.org/2000/svg"
            width="12"
            height="12"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
            aria-hidden="true"
          >
            <polyline points="15 18 9 12 15 6" />
          </svg>
          Contacts
        </Link>
      </header>

      {loading && <p className="text-sm text-fg-muted">Loading...</p>}

      {error && <p className="text-sm text-red-400">{error}</p>}

      {!loading && !error && prompts.length === 0 && (
        <p className="text-sm text-fg-muted">
          No prompts configured. Run the seed migration in the agent repo.
        </p>
      )}

      {!loading &&
        !error &&
        prompts.map((prompt) => (
          <PromptSection
            key={prompt.key}
            prompt={prompt}
            onSaved={handleSaved}
            onError={handleError}
          />
        ))}

      {toast && (
        <Toast
          message={toast.message}
          tone={toast.kind}
          onClose={() => setToast(null)}
        />
      )}
    </div>
  );
}
