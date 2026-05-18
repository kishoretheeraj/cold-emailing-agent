"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { toast } from "sonner";
import { supabase } from "@/lib/supabase";
import { PromptCategory } from "./PromptCategory";
import {
  CATEGORY_ORDER,
  PROMPT_CATEGORY_MAP,
  type PromptCategory as PromptCategoryType,
} from "@/lib/promptCategories";
import type { Prompt } from "@/lib/types";

const STORAGE_KEY = "prompts-open-categories";

export function PromptsPage() {
  const [prompts, setPrompts] = useState<Prompt[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  // null = skeleton (pre-hydration); Set = hydrated from localStorage
  const [openCategories, setOpenCategories] = useState<Set<string> | null>(null);

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

  // Read localStorage after mount to avoid SSR hydration mismatch
  useEffect(() => {
    try {
      const stored = localStorage.getItem(STORAGE_KEY);
      const initial = stored
        ? new Set<string>(JSON.parse(stored) as string[])
        : new Set<string>(["Sender & Core"]);
      setOpenCategories(initial);
    } catch {
      setOpenCategories(new Set<string>(["Sender & Core"]));
    }
  }, []);

  function handleToggle(category: string) {
    setOpenCategories((prev) => {
      const next = new Set(prev);
      next.has(category) ? next.delete(category) : next.add(category);
      try {
        localStorage.setItem(STORAGE_KEY, JSON.stringify([...next]));
      } catch {
        // localStorage blocked (e.g. private browsing with strict settings)
      }
      return next;
    });
  }

  function handleSaved(updated: Prompt) {
    setPrompts((prev) => prev.map((p) => (p.key === updated.key ? updated : p)));
    toast.success("Saved");
  }

  function handleError(message: string) {
    toast.error(message);
  }

  const searchActive = search.trim().length > 0;
  const q = search.trim().toLowerCase();

  const filtered = searchActive
    ? prompts.filter(
        (p) =>
          p.display_title.toLowerCase().includes(q) ||
          (p.description ?? "").toLowerCase().includes(q)
      )
    : prompts;

  // Group prompts by category; unknown keys fall into "Shared"
  const grouped = new Map<PromptCategoryType, Prompt[]>();
  for (const cat of CATEGORY_ORDER) {
    grouped.set(cat, []);
  }
  for (const p of filtered) {
    const cat: PromptCategoryType = PROMPT_CATEGORY_MAP[p.key] ?? "Shared";
    grouped.get(cat)!.push(p);
  }

  return (
    <div className="mx-auto max-w-4xl px-4 sm:px-6 py-8 sm:py-12">
      <header className="flex items-start justify-between gap-4 mb-8">
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

      {/* Sticky search */}
      <div className="sticky top-0 z-10 -mx-4 sm:-mx-6 px-4 sm:px-6 pb-4 pt-1 bg-bg">
        <input
          type="search"
          placeholder="Search prompts..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm text-fg placeholder:text-fg-dim focus:outline-none focus:border-border-strong"
        />
      </div>

      {loading && <p className="text-sm text-fg-muted">Loading...</p>}

      {error && <p className="text-sm text-red-400">{error}</p>}

      {!loading && !error && prompts.length === 0 && (
        <p className="text-sm text-fg-muted">
          No prompts configured. Run the seed migration in the agent repo.
        </p>
      )}

      {!loading && !error && prompts.length > 0 && (
        <>
          {/* Skeleton: openCategories is null until localStorage is read after mount */}
          {openCategories === null ? (
            <div className="space-y-3">
              {[...Array(7)].map((_, i) => (
                <div
                  key={i}
                  className="h-12 rounded-xl border border-border bg-surface animate-pulse"
                />
              ))}
            </div>
          ) : (
            CATEGORY_ORDER.map((cat) => {
              const catPrompts = grouped.get(cat) ?? [];
              if (catPrompts.length === 0) return null;
              return (
                <PromptCategory
                  key={cat}
                  category={cat}
                  prompts={catPrompts}
                  isOpen={openCategories.has(cat)}
                  onToggle={() => handleToggle(cat)}
                  searchActive={searchActive}
                  onSaved={handleSaved}
                  onError={handleError}
                />
              );
            })
          )}
        </>
      )}
    </div>
  );
}
