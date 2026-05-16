"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { toast } from "sonner";
import { supabase } from "@/lib/supabase";
import { PromptSection } from "./PromptSection";
import type { Prompt } from "@/lib/types";

export function PromptsPage() {
  const [prompts, setPrompts] = useState<Prompt[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
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
    toast.success("Saved");
  }

  function handleError(message: string) {
    toast.error(message);
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

    </div>
  );
}
