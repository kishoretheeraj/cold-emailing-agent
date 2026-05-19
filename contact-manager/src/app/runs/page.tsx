"use client";

import { useEffect, useState, useCallback } from "react";
import Link from "next/link";
import { supabase } from "@/lib/supabase";
import type { AgentEvent } from "@/lib/types";

const STATUS_CHIPS = ["all", "success", "failed", "blocked_preflight"] as const;
type StatusFilter = (typeof STATUS_CHIPS)[number];

const EVENT_TYPE_CHIPS = [
  "all", "preflight", "critic", "sent_detected",
  "classify_reply", "draft_reply", "research",
] as const;
type EventTypeFilter = (typeof EVENT_TYPE_CHIPS)[number];

const STATUS_LABELS: Record<string, string> = {
  success: "Success",
  failed: "Failed",
  blocked_preflight: "Blocked",
  running: "Running",
};

function formatMetadata(
  type: string,
  meta: Record<string, unknown> | null
): string {
  if (!meta) return "—";
  if (type === "preflight") {
    const checks = meta.blocked_checks as string[] | undefined;
    return checks?.[0] ?? "—";
  }
  if (type === "classify_reply")
    return (meta.classifier_status as string | undefined) ?? "—";
  if (type === "critic") {
    const retried = meta.retried ? " retried" : "";
    return `score=${meta.score} verdict=${meta.verdict}${retried}`;
  }
  if (type === "sent_detected")
    return `via=${meta.method} → ${meta.new_stage}`;
  if (type === "research") {
    if (meta.cache_hit) return `cache_hit age=${meta.cache_age_days}d`;
    return `queries=${meta.queries_generated} reliable=${meta.brief_reliable}`;
  }
  return JSON.stringify(meta).slice(0, 60);
}

function statusColor(status: string): string {
  if (status === "success") return "text-emerald-400";
  if (status === "failed") return "text-red-400";
  if (status === "blocked_preflight") return "text-amber-400";
  return "text-fg-muted";
}

function formatTs(ts: string): string {
  return new Date(ts).toLocaleString("en-US", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export default function RunsPage() {
  const [events, setEvents] = useState<AgentEvent[]>([]);
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("all");
  const [eventTypeFilter, setEventTypeFilter] = useState<EventTypeFilter>("all");
  const [failureBadge, setFailureBadge] = useState<number>(0);
  const [loading, setLoading] = useState(true);

  const fetchEvents = useCallback(async () => {
    const { data } = await supabase
      .from("agent_events")
      .select("*")
      .order("started_at", { ascending: false })
      .limit(100);
    setEvents((data as AgentEvent[]) ?? []);
    setLoading(false);
  }, []);

  const fetchBadge = useCallback(async () => {
    const since = new Date(Date.now() - 7 * 24 * 60 * 60 * 1000).toISOString();
    const { count } = await supabase
      .from("agent_events")
      .select("id", { count: "exact", head: true })
      .in("status", ["failed", "blocked_preflight"])
      .gte("started_at", since);
    setFailureBadge(count ?? 0);
  }, []);

  useEffect(() => {
    fetchEvents();
    fetchBadge();
    const id = setInterval(() => {
      fetchEvents();
      fetchBadge();
    }, 10_000);
    return () => clearInterval(id);
  }, [fetchEvents, fetchBadge]);

  const filtered = events
    .filter((e) => statusFilter === "all" || e.status === statusFilter)
    .filter((e) => eventTypeFilter === "all" || e.event_type === eventTypeFilter);

  return (
    <div className="mx-auto max-w-5xl px-4 sm:px-6 py-8 space-y-6">
      <header className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold text-fg">Activity</h1>
          <p className="text-sm text-fg-muted mt-0.5">
            Recent agent events — preflight checks, reply classification, draft creation.
          </p>
        </div>
        <div className="flex items-center gap-3">
          {failureBadge > 0 && (
            <span className="rounded-full bg-red-500/20 border border-red-500/40 px-2 py-0.5 text-xs text-red-300">
              {failureBadge} failure{failureBadge !== 1 ? "s" : ""} (7d)
            </span>
          )}
          <Link
            href="/"
            className="rounded-lg border border-border px-3 py-1.5 text-xs text-fg-muted hover:text-fg hover:border-border-strong transition"
          >
            Back
          </Link>
        </div>
      </header>

      {/* Status filter chips */}
      <div className="flex flex-wrap items-center gap-2">
        {STATUS_CHIPS.map((s) => (
          <button
            key={s}
            type="button"
            onClick={() => setStatusFilter(s)}
            className={`rounded-full px-3 py-1 text-xs border transition-colors capitalize ${
              statusFilter === s
                ? "bg-indigo-500/20 text-indigo-300 border-indigo-500/40"
                : "bg-surface text-fg-muted border-border hover:border-border-strong"
            }`}
          >
            {s === "all" ? "All" : s === "blocked_preflight" ? "Blocked" : s}
          </button>
        ))}
        <span className="w-px h-4 bg-border" />
        {EVENT_TYPE_CHIPS.map((t) => (
          <button
            key={t}
            type="button"
            onClick={() => setEventTypeFilter(t)}
            className={`rounded-full px-3 py-1 text-xs border transition-colors ${
              eventTypeFilter === t
                ? "bg-indigo-500/20 text-indigo-300 border-indigo-500/40"
                : "bg-surface text-fg-muted border-border hover:border-border-strong"
            }`}
          >
            {t === "all" ? "All types" : t}
          </button>
        ))}
      </div>

      {/* Table */}
      <div className="bg-surface border border-border rounded-lg overflow-hidden">
        {loading ? (
          <div className="px-4 py-8 text-sm text-fg-muted text-center">
            Loading...
          </div>
        ) : filtered.length === 0 ? (
          <div className="px-4 py-12 text-sm text-fg-muted text-center">
            No events yet.
          </div>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border text-xs text-fg-dim uppercase tracking-wider">
                <th className="text-left px-4 py-3 font-medium">Type</th>
                <th className="text-left px-4 py-3 font-medium">Status</th>
                <th className="text-left px-4 py-3 font-medium hidden sm:table-cell">
                  Contact
                </th>
                <th className="text-left px-4 py-3 font-medium hidden md:table-cell">
                  Detail
                </th>
                <th className="text-right px-4 py-3 font-medium">Started</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {filtered.map((e) => (
                <tr key={e.id} className="hover:bg-surface-2 transition-colors">
                  <td className="px-4 py-3 font-mono text-xs text-fg">
                    {e.event_type}
                  </td>
                  <td className={`px-4 py-3 text-xs font-medium ${statusColor(e.status)}`}>
                    {STATUS_LABELS[e.status] ?? e.status}
                  </td>
                  <td className="px-4 py-3 text-xs text-fg-muted hidden sm:table-cell">
                    {e.contact_name ?? (e.contact_id != null ? `#${e.contact_id}` : "—")}
                  </td>
                  <td className="px-4 py-3 text-xs text-fg-muted hidden md:table-cell max-w-xs truncate">
                    {e.error_message ?? formatMetadata(e.event_type, e.metadata)}
                  </td>
                  <td className="px-4 py-3 text-xs text-fg-dim text-right whitespace-nowrap">
                    {formatTs(e.started_at)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
