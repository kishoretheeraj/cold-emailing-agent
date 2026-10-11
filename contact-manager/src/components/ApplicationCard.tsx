"use client";

import { useId, useState } from "react";
import type { AutomationStatus, JobApplication } from "@/lib/types";
import { AUTOMATION_STATUS_LABELS } from "@/lib/types";

// ── Shared helpers (also used by ApplicationList) ────────────────────────────

const TONE_NEUTRAL = "border-border bg-surface text-fg-muted";
const TONE_INDIGO = "border-indigo-600/40 bg-indigo-600/15 text-indigo-300";
const TONE_EMERALD = "border-emerald-600/40 bg-emerald-600/15 text-emerald-300";
const TONE_AMBER = "border-amber-700/50 bg-amber-700/15 text-amber-300";
const TONE_RED = "border-red-600/40 bg-red-600/15 text-red-300";

const STATUS_TONES: Record<AutomationStatus, string> = {
  idle: TONE_NEUTRAL,
  preparing: TONE_NEUTRAL,
  needs_input: TONE_AMBER,
  ready_for_review: TONE_EMERALD,
  approved: TONE_INDIGO,
  submitting: TONE_INDIGO,
  submitted: TONE_INDIGO,
  needs_confirmation: TONE_AMBER,
  failed_retryable: TONE_RED,
  failed_terminal: TONE_RED,
  unsupported: TONE_NEUTRAL,
};

export function matchScore(app: JobApplication): number | null {
  if (app.pick_score != null) {
    return app.pick_score > 1
      ? Math.round(Math.min(app.pick_score, 100))
      : Math.round(Math.max(app.pick_score, 0) * 100);
  }
  return null;
}

export function statusLabel(status: AutomationStatus): string {
  return AUTOMATION_STATUS_LABELS[status] ?? status;
}

export function statusTone(status: AutomationStatus): string {
  return STATUS_TONES[status] ?? TONE_NEUTRAL;
}

export function relativeAge(iso: string | null | undefined): string | null {
  if (!iso) return null;
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return null;
  const diffMs = Math.max(0, Date.now() - then);
  const days = Math.floor(diffMs / 86_400_000);
  if (days >= 1) return `${days}d ago`;
  const hours = Math.floor(diffMs / 3_600_000);
  if (hours >= 1) return `${hours}h ago`;
  return "just now";
}

// ── Tier 2 data ────────────────────────────────────────────────────────────────

function snapshotOf(app: JobApplication): Record<string, unknown> | null {
  const snapshot = (app as { posting_snapshot?: unknown }).posting_snapshot;
  return snapshot && typeof snapshot === "object" ? (snapshot as Record<string, unknown>) : null;
}

function formatMoney(value: number): string {
  return value >= 1000 ? `$${Math.round(value / 1000)}K` : `$${value}`;
}

function salaryRange(app: JobApplication): string | null {
  const snapshot = snapshotOf(app);
  if (!snapshot) return null;
  for (const key of ["salary", "salary_range", "compensation"]) {
    const value = snapshot[key];
    if (typeof value === "string" && value.trim()) return value.trim();
  }
  const min = snapshot.salary_min;
  const max = snapshot.salary_max;
  if (typeof min === "number" && typeof max === "number") return `${formatMoney(min)} - ${formatMoney(max)}`;
  if (typeof min === "number") return `${formatMoney(min)}+`;
  if (typeof max === "number") return `Up to ${formatMoney(max)}`;
  return null;
}

function sourceLabel(app: JobApplication): string | null {
  const raw = app.apply_preview?.platform || app.source;
  if (!raw) return null;
  const spaced = raw.replace(/_/g, " ").trim();
  return spaced ? `via ${spaced.charAt(0).toUpperCase()}${spaced.slice(1)}` : null;
}

function truncate(text: string, max: number): string {
  const trimmed = text.trim();
  return trimmed.length > max ? `${trimmed.slice(0, max - 1).trimEnd()}...` : trimmed;
}

// ── Action button ──────────────────────────────────────────────────────────────

type CardAction = { label: string; kind: "review" | "detail" } | null;

function actionFor(status: AutomationStatus): CardAction {
  switch (status) {
    case "idle":
    case "preparing":
      return null;
    case "ready_for_review":
      return { label: "Review", kind: "review" };
    case "approved":
    case "submitting":
      return { label: "View progress", kind: "detail" };
    case "submitted":
      return { label: "View confirmation", kind: "detail" };
    case "needs_confirmation":
      return { label: "Confirm status", kind: "detail" };
    default:
      return { label: "View issue", kind: "detail" };
  }
}

// ── Component ──────────────────────────────────────────────────────────────────

export type ApplicationCardProps = {
  app: JobApplication;
  onReview?: (app: JobApplication) => void;
  onViewDetail?: (app: JobApplication) => void;
};

export default function ApplicationCard({ app, onReview, onViewDetail }: ApplicationCardProps) {
  const [expanded, setExpanded] = useState(false);
  const baseId = useId();
  const titleId = `${baseId}-title`;
  const detailsId = `${baseId}-details`;

  const score = matchScore(app);
  const action = actionFor(app.automation_status);
  const salary = salaryRange(app);
  const source = sourceLabel(app);
  const age = relativeAge(app.posted_at ?? app.created_at);
  const sourceAndAge = [source, age].filter(Boolean).join(" · ");

  const handleAction = () => {
    if (!action) return;
    if (action.kind === "review") onReview?.(app);
    else onViewDetail?.(app);
  };

  return (
    <article aria-labelledby={titleId} className="w-full min-w-0 rounded-xl border border-border bg-surface">
      <button
        type="button"
        onClick={() => setExpanded((open) => !open)}
        aria-expanded={expanded}
        aria-controls={detailsId}
        className="flex min-h-[44px] w-full min-w-0 items-start justify-between gap-3 rounded-t-xl p-3 text-left"
      >
        <span className="min-w-0 flex-1">
          <span id={titleId} className="block truncate text-sm font-semibold text-fg">
            {app.role}
          </span>
          <span className="block truncate text-sm text-fg-muted">{app.company}</span>
          <span
            className={`mt-2 inline-block max-w-full truncate rounded-md border px-2 py-0.5 text-xs font-medium ${statusTone(app.automation_status)}`}
          >
            {statusLabel(app.automation_status)}
          </span>
        </span>
        <span
          aria-label={score != null ? `Match score ${score} out of 100` : "Match score unavailable"}
          className="shrink-0 rounded-md border border-border-strong px-2 py-1 text-xs font-semibold text-indigo-300"
        >
          {score != null ? score : "--"}
        </span>
      </button>

      {expanded && (
        <div id={detailsId} className="min-w-0 space-y-1 border-t border-border px-3 py-2 text-xs text-fg-muted">
          {app.location && <p className="truncate">{app.location}</p>}
          {salary && <p className="truncate">{salary}</p>}
          {app.pick_reasoning && <p className="truncate text-fg-dim">{truncate(app.pick_reasoning, 140)}</p>}
          {sourceAndAge && <p className="truncate text-fg-dim">{sourceAndAge}</p>}
        </div>
      )}

      <div className="border-t border-border p-3">
        {action ? (
          <button
            type="button"
            onClick={handleAction}
            className={`min-h-[44px] w-full rounded-md px-4 text-sm font-medium ${
              action.kind === "review"
                ? "bg-emerald-600 text-white"
                : "border border-border-strong text-fg"
            }`}
          >
            {action.label}
          </button>
        ) : (
          <p role="status" className="flex min-h-[44px] items-center justify-center text-sm text-fg-dim">
            Preparing…
          </p>
        )}
      </div>
    </article>
  );
}
