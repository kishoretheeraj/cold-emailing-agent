"use client";

import { useEffect, useState } from "react";
import { Badge } from "@/components/ui/Badge";
import type { SystemHealthRow } from "@/lib/types";

// I9c: sources that should always render a chip, even with zero reported rows -- otherwise a
// source that has simply stopped reporting entirely just vanishes, which is indistinguishable
// from "nothing to show" and defeats the whole point of replacing the GHA red-X signal.
// M4: db.record_run(source=...) is called with nine distinct values across the Python
// codebase (grep `record_run(` at the repo root to confirm) -- agent, monitor, cu_linkedin,
// jobright, job_discovery, form_d_ingest, visa_ingest_lca, visa_ingest_uscis, visa_match. All
// nine get a chip here, not just the six that shipped with U13 -- an allowlist gap is exactly
// the "silently reads as healthy" failure mode this strip exists to prevent.
const KNOWN_SOURCES = [
  "agent",
  "monitor",
  "cu_linkedin",
  "jobright",
  "job_discovery",
  "form_d_ingest",
  "visa_ingest_lca",
  "visa_ingest_uscis",
  "visa_match",
] as const;

// I9b: a source dead for weeks must not render in the same dim "success" style as one that ran
// minutes ago. Thresholds are deliberately per-source -- cu_linkedin runs a few times a day,
// monitor every 20-60 min, the daily agent once a day, jobright once a day (M4: a bit over a
// day's grace), job_discovery has no fixed schedule but should still surface a dead source
// within about a month (M4), and the quarterly visa/form-d/visa-match ingests get a generous
// catch-all default rather than their own entries.
const SOURCE_MAX_AGE_MINUTES: Record<string, number> = {
  cu_linkedin: 60 * 8, // within the last ~3 timer fires
  agent: 60 * 30,
  monitor: 60 * 2,
  jobright: 60 * 26, // runs daily -- a bit over a day
  job_discovery: 60 * 24 * 35, // manual/no fixed schedule -- should still run at least monthly
};
const DEFAULT_MAX_AGE_MINUTES = 60 * 24 * 100; // quarterly/manual sources -- generous default

function relativeTime(iso: string): string {
  const diffMs = Date.now() - new Date(iso).getTime();
  const minutes = Math.round(diffMs / 60000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} hr ago`;
  const days = Math.round(hours / 24);
  return `${days} day${days === 1 ? "" : "s"} ago`;
}

function isStale(source: string, ranAtIso: string): boolean {
  const ageMinutes = (Date.now() - new Date(ranAtIso).getTime()) / 60000;
  const maxAge = SOURCE_MAX_AGE_MINUTES[source] ?? DEFAULT_MAX_AGE_MINUTES;
  return ageMinutes > maxAge;
}

// M9: blocked (a CAPTCHA/human-needed pause) is "waiting on a person", not a hard failure --
// amber, not red. failure is the more severe state -- red.
function statusVariant(status: string): "emerald" | "amber" | "red" | "muted" {
  if (status === "success") return "emerald";
  if (status === "blocked") return "amber";
  if (status === "failure") return "red";
  return "muted";
}

export function SystemHealthStrip() {
  const [rows, setRows] = useState<SystemHealthRow[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(false);

  useEffect(() => {
    let cancelled = false;
    fetch("/api/system-health")
      .then((res) => {
        if (!res.ok) throw new Error(`request failed: ${res.status}`);
        return res.json();
      })
      .then((data) => {
        if (!cancelled) setRows(data.health ?? []);
      })
      .catch(() => {
        // I9a: a failed fetch must render an explicit error state, distinct from "loaded, zero
        // rows" -- a 500 here must not look identical to "nothing to show" on the exact strip
        // that's supposed to replace the GHA red-X failure signal.
        if (!cancelled) setLoadError(true);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (loading) return null;

  if (loadError) {
    return (
      <div className="px-3 py-2 bg-amber-500/10 border border-amber-500/30 rounded-md text-sm text-amber-200">
        Couldn&apos;t load system health -- try refreshing.
      </div>
    );
  }

  const bySource = new Map((rows ?? []).map((r) => [r.source, r] as const));
  const knownRows: (SystemHealthRow | { source: string; status: "never_ran" })[] =
    KNOWN_SOURCES.map((s) => bySource.get(s) ?? { source: s, status: "never_ran" as const });
  // A source reporting rows that ISN'T in the known-sources list still gets a chip -- an
  // out-of-date allowlist must never hide a real, reporting source.
  const extraRows = (rows ?? []).filter(
    (r) => !(KNOWN_SOURCES as readonly string[]).includes(r.source)
  );
  const displayRows = [...knownRows, ...extraRows];

  const blocked = (rows ?? []).filter((r) => r.status === "blocked");
  const vncBase = process.env.NEXT_PUBLIC_BEELINK_VNC_URL;

  return (
    <div className="flex flex-col gap-2">
      {blocked.length > 0 && (
        <div className="px-3 py-2 bg-amber-500/10 border border-amber-500/30 rounded-md text-sm text-amber-200">
          {blocked.map((r) => (
            <div key={r.source}>
              <strong>{r.source}</strong> is blocked and needs a human at the VNC console.{" "}
              {vncBase ? (
                <a href={vncBase} target="_blank" rel="noreferrer" className="underline">
                  Open VNC
                </a>
              ) : (
                "Set NEXT_PUBLIC_BEELINK_VNC_URL to link directly to the console."
              )}
            </div>
          ))}
        </div>
      )}
      <div className="flex flex-wrap gap-2">
        {displayRows.map((r) => {
          if (r.status === "never_ran") {
            return (
              <div
                key={r.source}
                className="flex items-center gap-2 px-2 py-1 bg-surface-2 border border-border rounded-md text-xs"
              >
                <span className="text-fg-muted">{r.source}</span>
                <Badge variant="muted">never ran</Badge>
              </div>
            );
          }
          const row = r as SystemHealthRow;
          const stale = isStale(row.source, row.ran_at);
          return (
            <div
              key={row.source}
              title={row.status === "success" ? undefined : (row.failure_reason ?? undefined)}
              className="flex items-center gap-2 px-2 py-1 bg-surface-2 border border-border rounded-md text-xs"
            >
              <span className="text-fg-muted">{row.source}</span>
              {/* M6: a failure must stay red regardless of staleness -- a stale failure is not
                  less severe than a fresh one, so only a non-failure status downgrades to amber
                  when stale. */}
              <Badge variant={statusVariant(row.status) === "red" ? "red" : stale ? "amber" : statusVariant(row.status)}>
                {row.status}
              </Badge>
              {/* M7: root CLAUDE.md calls out `errors`, not just `status`, as the wedged-box
                  signal -- a run can report status='success' while still logging per-row
                  errors along the way. */}
              {typeof row.errors === "number" && row.errors > 0 && (
                <span className="text-amber-400">errors: {row.errors}</span>
              )}
              <span className={stale ? "text-amber-400" : "text-fg-dim"}>{relativeTime(row.ran_at)}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
