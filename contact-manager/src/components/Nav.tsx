"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { toast } from "sonner";
import { ConfirmModal } from "@/components/ui/ConfirmModal";
import { Tooltip } from "@/components/ui/Tooltip";
import { supabase } from "@/lib/supabase";

type TriggerState = "idle" | "loading" | "ok" | "err";
type PauseScope = "none" | "agent" | "all";
type SelectedScope = "agent" | "all";

const NAV_LINKS = [
  { href: "/overview", label: "Overview" },
  { href: "/", label: "Contacts" },
  { href: "/applications", label: "Applications" },
  { href: "/queue", label: "Queue" },
  { href: "/replies", label: "Replies" },
  { href: "/import", label: "Import" },
  { href: "/prompts", label: "Prompts" },
  { href: "/runs", label: "Activity" },
  { href: "/lab", label: "Lab" },
  { href: "/visa-review", label: "Visa" },
] as const;

export function Nav() {
  const pathname = usePathname();
  const [triggerState, setTriggerState] = useState<TriggerState>("idle");

  const [pauseScope, setPauseScope] = useState<PauseScope>("none");
  const [configLoading, setConfigLoading] = useState(true);
  const [pauseLoading, setPauseLoading] = useState(false);
  const [visaReviewCount, setVisaReviewCount] = useState(0);

  // Pending visa-match-review count — best-effort, never blocks the nav.
  useEffect(() => {
    supabase
      .from("company_intel")
      .select("id")
      .eq("match_status", "needs_review")
      .then(
        ({ data }) => setVisaReviewCount((data ?? []).length),
        () => {}
      );
  }, []);

  const [showRunModal, setShowRunModal] = useState(false);
  const [showPauseModal, setShowPauseModal] = useState(false);
  const [showResumeModal, setShowResumeModal] = useState(false);
  const [selectedScope, setSelectedScope] = useState<SelectedScope>("agent");

  // Load current pause state on mount.
  useEffect(() => {
    fetch("/api/agent-config")
      .then((r) => r.json())
      .then((d: { scope?: PauseScope }) => {
        setPauseScope(d.scope ?? "none");
      })
      .catch(() => {
        // Fail open — don't block the UI if config fetch fails.
      })
      .finally(() => setConfigLoading(false));
  }, []);

  const updateScope = useCallback(async (scope: PauseScope) => {
    setPauseLoading(true);
    try {
      const r = await fetch("/api/agent-config", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ scope }),
      });
      if (!r.ok) throw new Error("request failed");
      setPauseScope(scope);
      if (scope === "none") {
        toast.success("Agent resumed — takes effect on next run");
      } else if (scope === "agent") {
        toast.success("Outbound drafts paused");
      } else {
        toast.success("Agent and monitor fully paused");
      }
    } catch {
      toast.error("Failed to update pause state — try again");
    } finally {
      setPauseLoading(false);
    }
  }, []);

  const confirmPause = useCallback(async () => {
    setShowPauseModal(false);
    await updateScope(selectedScope);
  }, [selectedScope, updateScope]);

  const confirmResume = useCallback(async () => {
    setShowResumeModal(false);
    await updateScope("none");
  }, [updateScope]);

  const confirmRun = useCallback(async () => {
    setShowRunModal(false);
    setTriggerState("loading");
    try {
      const r = await fetch("/api/trigger-agent", { method: "POST" });
      setTriggerState(r.ok ? "ok" : "err");
      if (r.ok) toast.success("Agent triggered — check Activity in ~30s");
      else toast.error("Failed to trigger agent");
    } catch {
      setTriggerState("err");
      toast.error("Failed to trigger agent");
    }
    setTimeout(() => setTriggerState("idle"), 3000);
  }, []);

  const isPaused = pauseScope !== "none";

  return (
    <>
      <nav className="h-16 border-b border-border flex items-center px-4 sm:px-6 gap-3">
        <Link
          href="/"
          className="text-sm font-semibold text-fg mr-2 shrink-0"
        >
          Cold Email Ops
        </Link>

        <div className="flex items-center gap-1.5 flex-1">
          {NAV_LINKS.map(({ href, label }) => {
            const active = pathname === href;
            return (
              <Link
                key={href}
                href={href}
                className={`inline-flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-xs transition ${
                  active
                    ? "border-indigo-500/40 bg-indigo-500/10 text-indigo-300"
                    : "border-border text-fg-muted hover:text-fg hover:border-border-strong"
                }`}
              >
                {label}
                {href === "/visa-review" && visaReviewCount > 0 && (
                  <span className="inline-flex items-center justify-center min-w-[1.1rem] h-[1.1rem] rounded-full bg-amber-500/20 text-amber-300 text-[10px] px-1">
                    {visaReviewCount}
                  </span>
                )}
              </Link>
            );
          })}
        </div>

        <div className="flex items-center gap-2 shrink-0">
          {/* Pause / Resume button */}
          {!configLoading && (
            isPaused ? (
              <button
                type="button"
                onClick={() => setShowResumeModal(true)}
                disabled={pauseLoading}
                className="rounded-lg border px-3 py-1.5 text-xs transition disabled:opacity-50 text-emerald-400 border-emerald-500/40 hover:bg-emerald-500/10"
              >
                {pauseLoading ? "Updating…" : "Resume Agent"}
              </button>
            ) : (
              <button
                type="button"
                onClick={() => setShowPauseModal(true)}
                disabled={pauseLoading}
                className="rounded-lg border px-3 py-1.5 text-xs transition disabled:opacity-50 text-amber-400 border-amber-500/40 hover:bg-amber-500/10"
              >
                {pauseLoading ? "Updating…" : "Pause Agent"}
              </button>
            )
          )}

          {/* Run Agent button — disabled when paused */}
          {isPaused ? (
            <Tooltip content="Resume agent before triggering a run">
              <button
                type="button"
                disabled
                aria-label="Run Agent (paused)"
                className="rounded-lg border px-3 py-1.5 text-xs transition opacity-40 text-fg-muted border-border cursor-not-allowed"
              >
                Run Agent
              </button>
            </Tooltip>
          ) : (
            <button
              type="button"
              onClick={() => setShowRunModal(true)}
              disabled={triggerState === "loading"}
              className={`rounded-lg border px-3 py-1.5 text-xs transition disabled:opacity-50 ${
                triggerState === "ok"
                  ? "text-emerald-400 border-emerald-500/40"
                  : triggerState === "err"
                  ? "text-red-400 border-red-500/40"
                  : "text-fg-muted border-border hover:text-fg hover:border-border-strong"
              }`}
            >
              {triggerState === "loading"
                ? "Triggering…"
                : triggerState === "ok"
                ? "Triggered ✓"
                : triggerState === "err"
                ? "Failed ✗"
                : "Run Agent"}
            </button>
          )}
        </div>
      </nav>

      {/* Paused banner */}
      {isPaused && (
        <div
          className={`flex items-center justify-between px-4 sm:px-6 py-2 text-xs border-b ${
            pauseScope === "all"
              ? "bg-red-500/10 border-red-500/30 text-red-300"
              : "bg-amber-500/10 border-amber-500/30 text-amber-300"
          }`}
        >
          <span>
            {pauseScope === "all"
              ? "Agent and monitor fully paused. No drafts or reply detection until resumed."
              : "Outbound drafts paused. Monitor is still running — replies are detected normally."}
          </span>
          <button
            type="button"
            onClick={() => setShowResumeModal(true)}
            disabled={pauseLoading}
            className="ml-4 underline underline-offset-2 hover:no-underline disabled:opacity-50"
          >
            Resume
          </button>
        </div>
      )}

      {/* Run Agent confirmation modal */}
      <ConfirmModal
        open={showRunModal}
        title="Trigger agent now?"
        body={
          <div className="space-y-2">
            <p>This triggers the daily agent immediately. It will scan your contacts and create Gmail drafts for pending outreach.</p>
            <p className="text-fg-dim text-xs">Same-day duplicate drafts are prevented per-contact — anyone already drafted today is skipped. If this manual run succeeds, the scheduled run today will be skipped by the dedup check.</p>
          </div>
        }
        confirmLabel="Run Agent"
        confirmVariant="primary"
        onConfirm={confirmRun}
        onCancel={() => setShowRunModal(false)}
        loading={triggerState === "loading"}
      />

      {/* Pause scope confirmation modal */}
      <ConfirmModal
        open={showPauseModal}
        title="Pause the agent?"
        body={
          <div className="space-y-4">
            <p className="text-fg-dim text-xs">Currently running jobs will finish. Pause takes effect on the next scheduled run.</p>
            <div className="space-y-2">
              <button
                type="button"
                onClick={() => setSelectedScope("agent")}
                className={`w-full text-left rounded-lg border px-4 py-3 transition ${
                  selectedScope === "agent"
                    ? "border-amber-500/60 bg-amber-500/10"
                    : "border-border hover:border-border-strong"
                }`}
              >
                <div className="font-medium text-fg text-sm mb-0.5">Pause outbound only</div>
                <div className="text-fg-dim text-xs">New drafts stop. The monitor keeps running — replies are detected and sent-draft auto-flip continues.</div>
              </button>
              <button
                type="button"
                onClick={() => setSelectedScope("all")}
                className={`w-full text-left rounded-lg border px-4 py-3 transition ${
                  selectedScope === "all"
                    ? "border-red-500/60 bg-red-500/10"
                    : "border-border hover:border-border-strong"
                }`}
              >
                <div className="font-medium text-fg text-sm mb-0.5">Pause everything</div>
                <div className="text-fg-dim text-xs">Both drafts and reply detection stop. No emails drafted, no incoming replies classified.</div>
              </button>
            </div>
          </div>
        }
        confirmLabel="Pause Agent"
        confirmVariant="danger"
        onConfirm={confirmPause}
        onCancel={() => setShowPauseModal(false)}
        loading={pauseLoading}
      />

      {/* Resume confirmation modal */}
      <ConfirmModal
        open={showResumeModal}
        title="Resume the agent?"
        body={
          <p>The agent and monitor will resume processing contacts on the next scheduled run.</p>
        }
        confirmLabel="Resume"
        confirmVariant="primary"
        onConfirm={confirmResume}
        onCancel={() => setShowResumeModal(false)}
        loading={pauseLoading}
      />
    </>
  );
}
