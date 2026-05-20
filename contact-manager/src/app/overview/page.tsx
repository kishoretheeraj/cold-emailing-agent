"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { supabase } from "@/lib/supabase";

// ── Types ──────────────────────────────────────────────────────────────────────

type ActionContact = { id: string; name: string | null; company: string | null };
type TriggerState = "idle" | "loading" | "ok" | "err";

type AgentRun = {
  id: number;
  ran_at: string;
  status: "success" | "failure";
  drafted: number;
  skipped: number;
  errors: number;
  elapsed_seconds: number | null;
  failure_reason: string | null;
  source: "agent" | "monitor" | null;
};

type PipelineBuckets = {
  New: number;
  Drafted: number;
  Sent: number;
  Replied: number;
  Done: number;
};

// ── Helpers ────────────────────────────────────────────────────────────────────

const TERMINAL = new Set([
  "breakup_sent", "applied_followup_sent", "reply_sent",
  "closed", "bounced", "unsubscribed",
]);

function bucketStage(stage: string | null, replyStatus: string | null): keyof PipelineBuckets {
  if (replyStatus && replyStatus !== "no_reply") return "Replied";
  if (!stage || stage === "new") return "New";
  if (stage.endsWith("_drafted")) return "Drafted";
  if (TERMINAL.has(stage)) return "Done";
  return "Sent";
}

function formatRelativeTs(ts: string): string {
  const diff = Date.now() - new Date(ts).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 2) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.floor(hrs / 24)}d ago`;
}

function formatTime(ts: string): string {
  return new Date(ts).toLocaleString("en-US", {
    weekday: "short", month: "short", day: "numeric",
    hour: "2-digit", minute: "2-digit",
  });
}

// ── Component ──────────────────────────────────────────────────────────────────

export default function OverviewPage() {
  const [needsReply, setNeedsReply] = useState<ActionContact[]>([]);
  const [draftsWaiting, setDraftsWaiting] = useState(0);
  const [pipeline, setPipeline] = useState<PipelineBuckets>({ New: 0, Drafted: 0, Sent: 0, Replied: 0, Done: 0 });
  const [lastRun, setLastRun] = useState<AgentRun | null>(null);
  const [loading, setLoading] = useState(true);
  const [triggerState, setTriggerState] = useState<TriggerState>("idle");
  const resetTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const load = useCallback(async () => {
    const [replyRes, draftsRes, pipelineRes, runRes] = await Promise.all([
      // Contacts needing a human reply
      supabase
        .from("contacts")
        .select("id,name,company")
        .in("classifier_status", ["positive_reply", "soft_yes"])
        .not("reply_status", "in", "(interested,call_scheduled,dead)")
        .is("deleted_at", null)
        .order("created_at", { ascending: false }),

      // Drafts sitting in Gmail waiting for the user to review + send
      supabase
        .from("contacts")
        .select("id", { count: "exact", head: true })
        .like("stage", "%_drafted")
        .is("deleted_at", null),

      // All active contacts for pipeline bucketing
      supabase
        .from("contacts")
        .select("stage,reply_status")
        .is("deleted_at", null),

      // Most recent agent run
      supabase
        .from("agent_runs")
        .select("*")
        .order("ran_at", { ascending: false })
        .limit(1)
        .single(),
    ]);

    setNeedsReply((replyRes.data ?? []) as ActionContact[]);
    setDraftsWaiting(draftsRes.count ?? 0);

    if (pipelineRes.data) {
      const buckets: PipelineBuckets = { New: 0, Drafted: 0, Sent: 0, Replied: 0, Done: 0 };
      for (const row of pipelineRes.data) {
        buckets[bucketStage(row.stage, row.reply_status)]++;
      }
      setPipeline(buckets);
    }

    if (runRes.data) setLastRun(runRes.data as AgentRun);
    setLoading(false);
  }, []);

  useEffect(() => {
    load();
    const id = setInterval(load, 30_000);
    return () => clearInterval(id);
  }, [load]);

  const runAgent = useCallback(async () => {
    if (resetTimer.current) clearTimeout(resetTimer.current);
    setTriggerState("loading");
    try {
      const r = await fetch("/api/trigger-agent", { method: "POST" });
      setTriggerState(r.ok ? "ok" : "err");
    } catch {
      setTriggerState("err");
    }
    resetTimer.current = setTimeout(() => setTriggerState("idle"), 3000);
  }, []);

  const totalActive = pipeline.New + pipeline.Drafted + pipeline.Sent + pipeline.Replied;
  const allClear = needsReply.length === 0 && draftsWaiting === 0;

  return (
    <div className="mx-auto max-w-5xl px-4 sm:px-6 py-8 space-y-6">
      {/* ── Header ────────────────────────────────────────────────────────────── */}
      <header className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold text-fg">Overview</h1>
          <p className="text-sm text-fg-muted mt-0.5">
            Your job search pipeline at a glance.
          </p>
        </div>
        <nav className="shrink-0 flex items-center gap-2">
          <Link
            href="/"
            className="rounded-lg border border-border px-3 py-1.5 text-xs text-fg-muted hover:text-fg hover:border-border-strong transition"
          >
            Contacts
          </Link>
          <Link
            href="/prompts"
            className="rounded-lg border border-border px-3 py-1.5 text-xs text-fg-muted hover:text-fg hover:border-border-strong transition"
          >
            Prompts
          </Link>
          <Link
            href="/runs"
            className="rounded-lg border border-border px-3 py-1.5 text-xs text-fg-muted hover:text-fg hover:border-border-strong transition"
          >
            Activity
          </Link>
          <button
            type="button"
            onClick={runAgent}
            disabled={triggerState === "loading"}
            className={`rounded-lg border px-3 py-1.5 text-xs transition disabled:opacity-50 ${
              triggerState === "ok"
                ? "text-emerald-400 border-emerald-500/40"
                : triggerState === "err"
                ? "text-red-400 border-red-500/40"
                : "text-fg-muted border-border hover:text-fg hover:border-border-strong"
            }`}
          >
            {triggerState === "loading" ? "Triggering…" : triggerState === "ok" ? "Triggered ✓" : triggerState === "err" ? "Failed ✗" : "Run Agent"}
          </button>
        </nav>
      </header>

      {loading ? (
        <div className="text-sm text-fg-muted py-12 text-center">Loading…</div>
      ) : (
        <>
          {/* ── Action Required ─────────────────────────────────────────────── */}
          <section className="bg-surface border border-border rounded-lg overflow-hidden">
            <div className="px-4 py-3 border-b border-border">
              <h2 className="text-xs font-semibold uppercase tracking-wider text-fg-dim">
                Action Required
              </h2>
            </div>

            {allClear ? (
              <div className="px-4 py-8 text-center">
                <p className="text-sm font-medium text-emerald-400">You&apos;re all caught up</p>
                <p className="text-xs text-fg-muted mt-1">No replies waiting, no drafts pending review.</p>
              </div>
            ) : (
              <div className="divide-y divide-border">
                {/* Needs reply */}
                {needsReply.length > 0 && (
                  <div className="px-4 py-4 space-y-3">
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-2">
                        <span className="size-2 rounded-full bg-emerald-400 shrink-0" />
                        <span className="text-sm font-medium text-fg">
                          {needsReply.length} {needsReply.length === 1 ? "contact" : "contacts"} replied — needs your response
                        </span>
                      </div>
                      <Link
                        href="/?needsResponse=1"
                        className="text-xs text-indigo-300 hover:text-indigo-200 transition shrink-0"
                      >
                        View in list →
                      </Link>
                    </div>
                    <ul className="space-y-1.5 pl-4">
                      {needsReply.slice(0, 5).map((c) => (
                        <li key={c.id} className="text-xs text-fg-muted">
                          <span className="text-fg font-medium">{c.name ?? "—"}</span>
                          {c.company && <span className="text-fg-dim"> · {c.company}</span>}
                        </li>
                      ))}
                      {needsReply.length > 5 && (
                        <li className="text-xs text-fg-dim">+{needsReply.length - 5} more</li>
                      )}
                    </ul>
                  </div>
                )}

                {/* Drafts waiting */}
                {draftsWaiting > 0 && (
                  <div className="px-4 py-4 flex items-center gap-2">
                    <span className="size-2 rounded-full bg-amber-400 shrink-0" />
                    <span className="text-sm font-medium text-fg">
                      {draftsWaiting} {draftsWaiting === 1 ? "draft" : "drafts"} waiting in Gmail — review and send
                    </span>
                  </div>
                )}
              </div>
            )}
          </section>

          {/* ── Pipeline Funnel ──────────────────────────────────────────────── */}
          <section className="bg-surface border border-border rounded-lg overflow-hidden">
            <div className="px-4 py-3 border-b border-border flex items-center justify-between">
              <h2 className="text-xs font-semibold uppercase tracking-wider text-fg-dim">
                Pipeline
              </h2>
              <span className="text-xs text-fg-dim">{totalActive + pipeline.Done} total</span>
            </div>

            <div className="px-4 py-5 space-y-4">
              {/* Bucket rows */}
              {(
                [
                  { key: "New",     color: "bg-fg-dim",       label: "New",       desc: "Not yet contacted" },
                  { key: "Drafted", color: "bg-amber-400",    label: "Drafted",   desc: "Draft in Gmail, not sent" },
                  { key: "Sent",    color: "bg-indigo-400",   label: "In flight", desc: "Sent, awaiting reply" },
                  { key: "Replied", color: "bg-emerald-400",  label: "Replied",   desc: "Received a reply" },
                  { key: "Done",    color: "bg-border",       label: "Done",      desc: "Breakup / closed / terminal" },
                ] as const
              ).map(({ key, color, label, desc }) => {
                const count = pipeline[key];
                const total = totalActive + pipeline.Done || 1;
                const pct = Math.max((count / total) * 100, count > 0 ? 2 : 0);
                return (
                  <div key={key} className="space-y-1">
                    <div className="flex items-center justify-between text-xs">
                      <div className="flex items-center gap-2">
                        <span className={`size-2 rounded-full shrink-0 ${color}`} />
                        <span className="text-fg-muted">{label}</span>
                        <span className="text-fg-dim hidden sm:inline">{desc}</span>
                      </div>
                      <span className="text-fg font-medium tabular-nums">{count}</span>
                    </div>
                    <div className="h-1.5 bg-surface-2 rounded-full overflow-hidden">
                      <div
                        className={`h-full rounded-full ${color} transition-all duration-500`}
                        style={{ width: `${pct}%` }}
                      />
                    </div>
                  </div>
                );
              })}
            </div>
          </section>

          {/* ── Agent Status ─────────────────────────────────────────────────── */}
          <section className="bg-surface border border-border rounded-lg px-4 py-4">
            <div className="flex items-center justify-between gap-4 flex-wrap">
              <div className="space-y-1">
                <h2 className="text-xs font-semibold uppercase tracking-wider text-fg-dim">
                  Agent
                </h2>
                {lastRun ? (
                  <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                    <span
                      className={`text-xs font-medium ${lastRun.status === "success" ? "text-emerald-400" : "text-red-400"}`}
                    >
                      {lastRun.status === "success" ? "Last run OK" : "Last run failed"}
                    </span>
                    <span className="text-xs text-fg-muted">{formatTime(lastRun.ran_at)}</span>
                    <span className="text-xs text-fg-dim">({formatRelativeTs(lastRun.ran_at)})</span>
                    {lastRun.status === "success" && (
                      <>
                        <span className="text-xs text-fg-dim">·</span>
                        <span className="text-xs text-fg-muted">
                          {lastRun.drafted} drafted · {lastRun.skipped} skipped
                          {lastRun.errors > 0 && (
                            <span className="text-red-400"> · {lastRun.errors} errors</span>
                          )}
                        </span>
                      </>
                    )}
                    {lastRun.status === "failure" && lastRun.failure_reason && (
                      <span className="text-xs text-red-300">{lastRun.failure_reason}</span>
                    )}
                    <span className="text-xs text-fg-dim">
                      · {lastRun.source === "monitor" ? "monitor" : "scheduled"}
                    </span>
                  </div>
                ) : (
                  <p className="text-xs text-fg-muted">No runs recorded yet.</p>
                )}
                <p className="text-xs text-fg-dim">Next scheduled: weekdays at 4:37am EST</p>
              </div>

              <button
                type="button"
                onClick={runAgent}
                disabled={triggerState === "loading"}
                className={`shrink-0 rounded-lg border px-4 py-2 text-xs font-medium transition disabled:opacity-50 ${
                  triggerState === "ok"
                    ? "text-emerald-400 border-emerald-500/40"
                    : triggerState === "err"
                    ? "text-red-400 border-red-500/40"
                    : "text-fg-muted border-border hover:text-fg hover:border-border-strong"
                }`}
              >
                {triggerState === "loading" ? "Triggering…" : triggerState === "ok" ? "Triggered ✓" : triggerState === "err" ? "Failed ✗" : "Run Agent Now"}
              </button>
            </div>
          </section>
        </>
      )}
    </div>
  );
}
