"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { ChevronDown, Search, X } from "lucide-react";
import { supabase } from "@/lib/supabase";
import type { Contact } from "@/lib/types";
import { Skeleton } from "@/components/ui/Skeleton";

// ── Constants ──────────────────────────────────────────────────────────────────

const LIST_COLUMNS =
  "id,name,company,email,stage,tier,mode,last_emailed,reply_status,classifier_status,dartmouth,notes,message_id,followup_date,created_at,state";

const PICKER_LIMIT = 20;

// ── Props ──────────────────────────────────────────────────────────────────────

type Props = {
  selectedContact: Contact | null;
  onSelect: (contact: Contact) => void;
};

// ── Component ──────────────────────────────────────────────────────────────────

export function LabContactPicker({ selectedContact, onSelect }: Props) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const [results, setResults] = useState<Contact[]>([]);
  const [loading, setLoading] = useState(false);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);

  const fetchContacts = useCallback(async (q: string) => {
    setLoading(true);
    const escaped = q.trim().replace(/[%_]/g, "\\$&");
    let query = supabase
      .from("contacts")
      .select(LIST_COLUMNS)
      .is("deleted_at", null)
      .order("created_at", { ascending: false });
    if (escaped) {
      query = query.or(`name.ilike.%${escaped}%,company.ilike.%${escaped}%`);
    }
    const { data } = await query.limit(PICKER_LIMIT);
    setResults((data ?? []) as Contact[]);
    setLoading(false);
  }, []);

  // Load on open
  useEffect(() => {
    if (open) {
      void fetchContacts(search);
      setTimeout(() => inputRef.current?.focus(), 50);
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  // Debounce search
  useEffect(() => {
    if (!open) return;
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => {
      void fetchContacts(search);
    }, 300);
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
  }, [search, open, fetchContacts]);

  async function handleSelect(c: Contact) {
    setOpen(false);
    setSearch("");
    // Fetch full record (detail, job_description, job_title needed for assembly)
    const { data } = await supabase
      .from("contacts")
      .select("*")
      .eq("id", c.id)
      .single();
    onSelect((data ?? c) as Contact);
  }

  const summary = selectedContact
    ? `${selectedContact.name ?? "—"} at ${selectedContact.company ?? "—"}`
    : null;

  return (
    <div className="relative">
      <button
        type="button"
        data-testid="contact-picker-toggle"
        onClick={() => setOpen((v) => !v)}
        className="w-full flex items-center justify-between gap-3 px-4 py-3 rounded-xl border border-border bg-surface hover:border-border-strong transition text-left"
      >
        <span className="text-sm text-fg truncate">
          {summary ? (
            <>
              <span className="text-fg-muted text-xs mr-1">Contact:</span>
              <span className="font-medium">{summary}</span>
            </>
          ) : (
            <span className="text-fg-muted">Select a contact to preview against…</span>
          )}
        </span>
        <ChevronDown
          size={14}
          className={`text-fg-muted shrink-0 transition-transform duration-200 ${open ? "rotate-180" : ""}`}
          aria-hidden="true"
        />
      </button>

      {open && (
        <div className="absolute left-0 right-0 top-full mt-1 z-20 rounded-xl border border-border bg-surface shadow-lg">
          {/* Search input */}
          <div className="relative px-3 pt-3 pb-2">
            <Search
              size={13}
              className="absolute left-5.5 top-5.5 text-fg-dim pointer-events-none"
              aria-hidden="true"
            />
            <input
              ref={inputRef}
              type="text"
              placeholder="Search contacts…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="w-full rounded-lg border border-border bg-surface-2 pl-7 pr-7 py-2 text-sm text-fg placeholder:text-fg-dim focus:outline-none focus:border-border-strong"
            />
            {search && (
              <button
                type="button"
                onClick={() => setSearch("")}
                className="absolute right-5 top-4 text-fg-dim hover:text-fg"
                aria-label="Clear search"
              >
                <X size={13} />
              </button>
            )}
          </div>

          {/* Results */}
          <div className="max-h-64 overflow-y-auto pb-2">
            {loading && (
              <div className="space-y-1 px-3 py-1">
                {Array.from({ length: 4 }).map((_, i) => (
                  <Skeleton key={i} className="h-10 w-full rounded-lg" />
                ))}
              </div>
            )}

            {!loading && results.length === 0 && (
              <p className="text-sm text-fg-muted px-4 py-3">No contacts found.</p>
            )}

            {!loading &&
              results.map((c) => (
                <button
                  key={c.id}
                  type="button"
                  onClick={() => void handleSelect(c)}
                  className="w-full text-left px-4 py-2.5 hover:bg-surface-2 transition-colors"
                >
                  <div className="text-sm font-medium text-fg truncate">
                    {c.name ?? "—"}
                  </div>
                  <div className="text-xs text-fg-muted truncate">
                    {c.company ?? "—"} · {c.stage ?? "new"}
                  </div>
                </button>
              ))}
          </div>
        </div>
      )}
    </div>
  );
}
