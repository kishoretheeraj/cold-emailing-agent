"use client";

import { ChevronDown } from "lucide-react";
import { PromptSection } from "./PromptSection";
import type { Prompt } from "@/lib/types";

export function PromptCategory({
  category,
  prompts,
  isOpen,
  onToggle,
  searchActive,
  onSaved,
  onError,
}: {
  category: string;
  prompts: Prompt[];
  isOpen: boolean;
  onToggle: () => void;
  searchActive: boolean;
  onSaved: (updated: Prompt) => void;
  onError: (message: string) => void;
}) {
  const expanded = isOpen || searchActive;

  return (
    <section className="mb-4">
      <button
        data-testid="category-header"
        onClick={searchActive ? undefined : onToggle}
        aria-expanded={expanded}
        className={[
          "w-full flex items-center justify-between gap-3",
          "px-4 py-3 rounded-xl border border-border bg-surface",
          "text-left transition",
          searchActive
            ? "cursor-default"
            : "hover:border-border-strong hover:bg-surface-2",
        ].join(" ")}
      >
        <span className="flex items-center gap-2">
          <span className="text-sm font-semibold text-fg">{category}</span>
          <span className="rounded-full bg-surface-2 border border-border px-2 py-0.5 text-xs text-fg-muted">
            {prompts.length}
          </span>
        </span>
        <ChevronDown
          size={14}
          className={[
            "text-fg-muted shrink-0 transition-transform duration-200",
            expanded ? "rotate-180" : "",
          ].join(" ")}
          aria-hidden="true"
        />
      </button>

      {expanded && (
        <div className="mt-3 space-y-0">
          {prompts.map((prompt) => (
            <PromptSection
              key={prompt.key}
              prompt={prompt}
              onSaved={onSaved}
              onError={onError}
            />
          ))}
        </div>
      )}
    </section>
  );
}
