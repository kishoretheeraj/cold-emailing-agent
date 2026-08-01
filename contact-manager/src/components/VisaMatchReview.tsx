"use client";

import { useCallback, useEffect, useState } from "react";
import { CheckCircle2, XCircle, SkipForward, RotateCcw } from "lucide-react";
import { supabase } from "@/lib/supabase";
import { Badge } from "@/components/ui/Badge";
import { Skeleton } from "@/components/ui/Skeleton";
import { EmptyState } from "@/components/ui/EmptyState";
import type { CompanyIntel } from "@/lib/types";

type Employer = {
  id: number;
  display_name: string;
  lca_recent_2fy: number;
  latest_filing_fy: number | null;
  approval_rate: number | null;
};

type Phase = "loading" | "reviewing" | "summary";
type Outcome = "confirmed" | "rejected" | "skipped";

type Result = {
  companyId: number;
  normalizedName: string;
  outcome: Outcome;
};

// Mirrors ReviewFlow.tsx's card-by-card + keyboard-nav + summary shell, but
// is a standalone component rather than a refactor of that shared one — the
// two review flows (bulk contact import vs. company-match review) have
// different enough data shapes that sharing code isn't worth the risk to
// ReviewFlow's existing test coverage.
export function VisaMatchReview() {
  const [phase, setPhase] = useState<Phase>("loading");
  const [items, setItems] = useState<CompanyIntel[]>([]);
  const [employers, setEmployers] = useState<Record<number, Employer>>({});
  const [index, setIndex] = useState(0);
  const [candidateIndex, setCandidateIndex] = useState(0);
  const [results, setResults] = useState<Result[]>([]);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      setPhase("loading");
      setError(null);
      const { data, error: fetchError } = await supabase
        .from("company_intel")
        .select("*")
        .eq("match_status", "needs_review")
        .order("created_at", { ascending: true });

      if (cancelled) return;
      if (fetchError) {
        setError(`Failed to load review queue: ${fetchError.message}`);
        setPhase("reviewing");
        return;
      }

      const rows = (data ?? []) as CompanyIntel[];
      const employerIds = new Set<number>();
      for (const row of rows) {
        for (const c of row.top_candidates ?? []) {
          if (c.employer_id != null) employerIds.add(c.employer_id);
        }
      }

      let employerMap: Record<number, Employer> = {};
      if (employerIds.size > 0) {
        const { data: employerRows } = await supabase
          .from("employer_h1b_stats")
          .select("id,display_name,lca_recent_2fy,latest_filing_fy,approval_rate")
          .in("id", Array.from(employerIds));
        for (const e of (employerRows ?? []) as Employer[]) {
          employerMap[e.id] = e;
        }
      }

      if (cancelled) return;
      setItems(rows);
      setEmployers(employerMap);
      setIndex(0);
      setCandidateIndex(0);
      setResults([]);
      setPhase(rows.length > 0 ? "reviewing" : "summary");
    }
    load();
    return () => {
      cancelled = true;
    };
  }, []);

  const current = items[index];
  const candidates = current?.top_candidates ?? [];
  const candidate = candidates[candidateIndex];
  const candidateEmployer = candidate?.employer_id != null ? employers[candidate.employer_id] : undefined;

  const advance = useCallback(
    (outcome: Outcome) => {
      if (!current) return;
      setResults((prev) => [...prev, { companyId: current.id, normalizedName: current.normalized_name, outcome }]);
      setCandidateIndex(0);
      setIndex((i) => {
        const next = i + 1;
        if (next >= items.length) setPhase("summary");
        return next;
      });
    },
    [current, items.length]
  );

  const handleConfirm = useCallback(async () => {
    if (!current || !candidate || !candidateEmployer || saving) return;
    setSaving(true);
    const { error: updateError } = await supabase
      .from("company_intel")
      .update({
        match_status: "confirmed",
        matched_employer_id: candidateEmployer.id,
        match_confidence: candidate.score,
        sponsors_h1b: true,
        h1b_recent_count: candidateEmployer.lca_recent_2fy,
        latest_filing_fy: candidateEmployer.latest_filing_fy,
        approval_rate: candidateEmployer.approval_rate,
        reviewed_by_user_at: new Date().toISOString(),
      })
      .eq("id", current.id);
    setSaving(false);
    if (updateError) {
      setError(`Failed to save: ${updateError.message}`);
      return;
    }
    advance("confirmed");
  }, [current, candidate, candidateEmployer, saving, advance]);

  const handleReject = useCallback(async () => {
    if (!current || saving) return;
    setSaving(true);
    const { error: updateError } = await supabase
      .from("company_intel")
      .update({
        match_status: "rejected",
        matched_employer_id: null,
        sponsors_h1b: null,
        reviewed_by_user_at: new Date().toISOString(),
      })
      .eq("id", current.id);
    setSaving(false);
    if (updateError) {
      setError(`Failed to save: ${updateError.message}`);
      return;
    }
    advance("rejected");
  }, [current, saving, advance]);

  const handleSkip = useCallback(() => {
    if (!current || saving) return;
    advance("skipped");
  }, [current, saving, advance]);

  const cycleCandidate = useCallback(() => {
    if (candidates.length < 2) return;
    setCandidateIndex((i) => (i + 1) % candidates.length);
  }, [candidates.length]);

  // Keyboard shortcuts
  useEffect(() => {
    if (phase !== "reviewing") return;
    function onKeyDown(e: KeyboardEvent) {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return;
      if (e.key === "Enter" || e.key === "ArrowRight") {
        e.preventDefault();
        handleConfirm();
      } else if (e.key.toLowerCase() === "s") {
        e.preventDefault();
        handleSkip();
      } else if (e.key.toLowerCase() === "n") {
        e.preventDefault();
        handleReject();
      } else if (e.key.toLowerCase() === "c") {
        e.preventDefault();
        cycleCandidate();
      }
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [phase, handleConfirm, handleSkip, handleReject, cycleCandidate]);

  if (phase === "loading") {
    return (
      <div className="max-w-2xl mx-auto px-4 py-8 space-y-3">
        <Skeleton className="h-6 w-48" />
        <Skeleton className="h-32 w-full" />
      </div>
    );
  }

  if (phase === "summary") {
    const confirmed = results.filter((r) => r.outcome === "confirmed").length;
    const rejected = results.filter((r) => r.outcome === "rejected").length;
    const skipped = results.filter((r) => r.outcome === "skipped").length;

    return (
      <div className="max-w-2xl mx-auto px-4 py-8">
        {items.length === 0 && results.length === 0 ? (
          <EmptyState
            icon={<CheckCircle2 className="size-5 text-fg-muted" />}
            title="Nothing to review"
            description="No companies are currently awaiting a visa-intel match review."
          />
        ) : (
          <div className="space-y-4">
            <h1 className="text-lg font-semibold text-fg">Review complete</h1>
            <div className="grid grid-cols-3 gap-3">
              <div className="rounded-lg border border-border bg-surface p-3">
                <div className="text-xs text-fg-dim uppercase tracking-wider">Confirmed</div>
                <div className="text-2xl text-emerald-400 mt-1">{confirmed}</div>
              </div>
              <div className="rounded-lg border border-border bg-surface p-3">
                <div className="text-xs text-fg-dim uppercase tracking-wider">Rejected</div>
                <div className="text-2xl text-red-400 mt-1">{rejected}</div>
              </div>
              <div className="rounded-lg border border-border bg-surface p-3">
                <div className="text-xs text-fg-dim uppercase tracking-wider">Skipped</div>
                <div className="text-2xl text-fg-muted mt-1">{skipped}</div>
              </div>
            </div>
          </div>
        )}
      </div>
    );
  }

  return (
    <div className="max-w-2xl mx-auto px-4 py-8">
      <div className="flex items-center justify-between mb-4">
        <h1 className="text-lg font-semibold text-fg">Visa match review</h1>
        <span className="text-xs text-fg-dim">
          {index + 1} of {items.length}
        </span>
      </div>

      {error && (
        <div className="bg-red-500/10 border border-red-500/30 rounded-md p-3 text-red-300 text-xs mb-4">
          {error}
        </div>
      )}

      {current && (
        <div className="rounded-lg border border-border bg-surface p-5 space-y-4">
          <div>
            <div className="text-xs uppercase tracking-wider text-fg-dim mb-1">Target company</div>
            <div className="text-fg text-base font-medium">
              {current.raw_company_names[0] ?? current.normalized_name}
            </div>
          </div>

          <div className="h-px bg-border" />

          {candidate ? (
            <div>
              <div className="flex items-center justify-between mb-2">
                <div className="text-xs uppercase tracking-wider text-fg-dim">
                  Proposed match {candidates.length > 1 ? `(${candidateIndex + 1}/${candidates.length})` : ""}
                </div>
                <Badge variant="amber">{candidate.score.toFixed(0)}% match</Badge>
              </div>
              <div className="text-fg text-base font-medium">
                {candidateEmployer?.display_name ?? candidate.normalized_name}
              </div>
              {candidateEmployer && (
                <div className="text-fg-muted text-xs mt-1 space-x-3">
                  <span>{candidateEmployer.lca_recent_2fy} recent LCAs</span>
                  {candidateEmployer.latest_filing_fy && (
                    <span>latest FY{candidateEmployer.latest_filing_fy}</span>
                  )}
                  {candidateEmployer.approval_rate != null && (
                    <span>{Math.round(candidateEmployer.approval_rate * 100)}% approval rate</span>
                  )}
                </div>
              )}
            </div>
          ) : (
            <div className="text-fg-muted text-sm">No candidate details available.</div>
          )}

          <div className="flex flex-wrap gap-2 pt-2">
            <button
              type="button"
              onClick={handleConfirm}
              disabled={saving || !candidate}
              className="inline-flex items-center gap-1.5 rounded-lg border border-emerald-500/40 bg-emerald-500/10 text-emerald-300 px-3 py-2 text-sm hover:bg-emerald-500/20 transition-colors disabled:opacity-50"
            >
              <CheckCircle2 className="size-4" /> Confirm
            </button>
            {candidates.length > 1 && (
              <button
                type="button"
                onClick={cycleCandidate}
                disabled={saving}
                className="inline-flex items-center gap-1.5 rounded-lg border border-border text-fg-muted px-3 py-2 text-sm hover:border-border-strong transition-colors disabled:opacity-50"
              >
                <RotateCcw className="size-4" /> Try next candidate
              </button>
            )}
            <button
              type="button"
              onClick={handleReject}
              disabled={saving}
              className="inline-flex items-center gap-1.5 rounded-lg border border-red-500/40 text-red-300 px-3 py-2 text-sm hover:bg-red-500/10 transition-colors disabled:opacity-50"
            >
              <XCircle className="size-4" /> No match
            </button>
            <button
              type="button"
              onClick={handleSkip}
              disabled={saving}
              className="inline-flex items-center gap-1.5 rounded-lg border border-border text-fg-muted px-3 py-2 text-sm hover:border-border-strong transition-colors disabled:opacity-50"
            >
              <SkipForward className="size-4" /> Skip
            </button>
          </div>

          <div className="text-fg-dim text-xs pt-1">
            Enter confirm · c cycle candidate · n no match · s skip
          </div>
        </div>
      )}
    </div>
  );
}
