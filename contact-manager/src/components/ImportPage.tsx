"use client";

import { useState } from "react";
import { toast } from "sonner";
import type { ReviewContact, BulkImportWindow } from "@/lib/types";
import { ReviewFlow } from "./ReviewFlow";

// ── Types ──────────────────────────────────────────────────────────────────────

type Phase = "paste" | "review";

// ── Helpers ────────────────────────────────────────────────────────────────────

function parseContacts(raw: string): ReviewContact[] {
  const parsed: unknown = JSON.parse(raw.trim());
  if (!Array.isArray(parsed)) throw new Error("Expected a JSON array");

  return parsed.map((c: unknown) => {
    const obj = (c ?? {}) as Record<string, unknown>;
    const email =
      typeof obj.email === "string" && obj.email.trim() ? obj.email.trim() : null;
    const name =
      typeof obj.name === "string" && obj.name.trim() ? obj.name.trim() : null;
    const tier = typeof obj.tier === "number" ? obj.tier : 2;
    const mode =
      obj.mode === "outreach" || obj.mode === "applied" ? obj.mode : "outreach";

    return {
      name,
      email,
      company: typeof obj.company === "string" ? obj.company.trim() || null : null,
      role: typeof obj.role === "string" ? obj.role.trim() || null : null,
      detail: null,
      tier,
      mode,
      dartmouth: obj.dartmouth === true,
      job_title: null,
      job_description: null,
      applied_date: null,
      notes: typeof obj.notes === "string" ? obj.notes.trim() || null : null,
      resume_url: null,
      state: null,
      missing_email: !email,
      missing_required: !name,
      required_missing_fields: !name ? ["name"] : [],
      status: "pending" as const,
    };
  });
}

// ── Component ──────────────────────────────────────────────────────────────────

export function ImportPage() {
  const [phase, setPhase] = useState<Phase>("paste");
  const [raw, setRaw] = useState("");
  const [contacts, setContacts] = useState<ReviewContact[]>([]);

  function handleParse() {
    if (!raw.trim()) return;
    try {
      const parsed = parseContacts(raw);
      if (parsed.length === 0) {
        toast.error("No contacts found in the pasted JSON");
        return;
      }
      // Deduplicate within the batch by email (keep first occurrence)
      const seen = new Set<string>();
      const deduped = parsed.filter((c) => {
        if (!c.email) return true;
        const key = c.email.toLowerCase();
        if (seen.has(key)) return false;
        seen.add(key);
        return true;
      });
      const removed = parsed.length - deduped.length;
      if (removed > 0)
        toast.info(`${removed} duplicate email${removed > 1 ? "s" : ""} removed from this batch`);
      setContacts(deduped);
      setPhase("review");
    } catch {
      toast.error("Invalid JSON — paste the output from the TuckConnect bookmarklet");
    }
  }

  function reset() {
    setPhase("paste");
    setRaw("");
    setContacts([]);
  }

  // ── Review phase ─────────────────────────────────────────────────────────────

  if (phase === "review") {
    return (
      <ReviewFlow
        contacts={contacts}
        onUpdate={(index, updated) =>
          setContacts((cs) => cs.map((c, i) => (i === index ? updated : c)))
        }
        onBack={reset}
        onAddMore={reset}
        onAdded={(_window?: BulkImportWindow) => {
          reset();
          toast.success("Contacts added to your pipeline");
        }}
        onError={(msg) => toast.error(msg)}
      />
    );
  }

  // ── Paste phase ───────────────────────────────────────────────────────────────

  const canParse = raw.trim().startsWith("[");

  return (
    <div className="max-w-2xl mx-auto space-y-4">
      <div>
        <h1 className="text-2xl font-semibold text-fg">Import Contacts</h1>
        <p className="mt-1 text-sm text-fg-muted">
          Paste the JSON output from the TuckConnect bookmarklet. Each contact is
          reviewed before import.
        </p>
      </div>

      <div className="rounded-xl border border-border bg-surface p-4 space-y-3">
        <div className="space-y-1.5">
          <label
            htmlFor="tuck-json"
            className="text-xs font-medium text-fg-muted uppercase tracking-wide"
          >
            Bookmarklet output
          </label>
          <textarea
            id="tuck-json"
            value={raw}
            onChange={(e) => setRaw(e.target.value)}
            placeholder={'Paste JSON array here, e.g. [{"name": "...", "email": "...", ...}]'}
            className="w-full h-48 rounded-lg border border-border bg-bg px-3 py-2.5 text-sm text-fg font-mono placeholder:text-fg-dim resize-y focus:outline-none focus:border-indigo-500/50"
          />
        </div>

        <button
          type="button"
          onClick={handleParse}
          disabled={!canParse}
          className="w-full rounded-lg bg-indigo-500 px-4 py-2.5 text-sm font-medium text-white transition hover:bg-indigo-400 disabled:opacity-40 disabled:cursor-not-allowed"
        >
          Review contacts
        </button>
      </div>

      <div className="rounded-xl border border-border bg-surface p-4 space-y-2">
        <p className="text-xs font-medium text-fg-muted uppercase tracking-wide">
          How to use the bookmarklet
        </p>
        <ol className="text-sm text-fg-muted space-y-1 list-decimal list-inside">
          <li>Log into TuckConnect and run your alumni search</li>
          <li>Click the "TuckConnect Export" bookmark in your browser</li>
          <li>An alert will confirm how many contacts were copied to your clipboard</li>
          <li>Paste the clipboard here and click "Review contacts"</li>
        </ol>
      </div>
    </div>
  );
}
