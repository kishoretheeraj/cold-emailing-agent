"use client";

import type { JobApplication } from "@/lib/types";

export type TimelineStageState = "completed" | "in-progress" | "pending" | "failed";
export type TimelineStageKey = "approved" | "queued" | "applying" | "confirmed";

export type TimelineStage = {
  key: TimelineStageKey;
  label: string;
  state: TimelineStageState;
  at: string | null;
  detail?: string;
};

const DOT_CLASS: Record<TimelineStageState, string> = {
  completed: "bg-emerald-500",
  "in-progress": "bg-indigo-500 animate-pulse",
  pending: "bg-fg-dim",
  failed: "bg-red-500",
};

const LABEL_CLASS: Record<TimelineStageState, string> = {
  completed: "text-emerald-300",
  "in-progress": "text-indigo-300",
  pending: "text-fg-dim",
  failed: "text-red-300",
};

const STATE_TEXT: Record<TimelineStageState, string> = {
  completed: "completed",
  "in-progress": "in progress",
  pending: "pending",
  failed: "failed",
};

export function getTimelineStages(app: JobApplication): TimelineStage[] {
  const evidence = app.submission_evidence;
  const status = app.automation_status;
  const submitted = status === "submitted";
  const submitting = status === "submitting";
  const confirming = status === "needs_confirmation";
  const failed = status === "failed_retryable" || status === "failed_terminal";

  const approvedStage: TimelineStage = {
    key: "approved",
    label: "Approved",
    state: app.approved_at ? "completed" : "pending",
    at: app.approved_at ?? null,
    detail: app.approved_at ? "Approved by you" : undefined,
  };

  const queuedDone = submitting || submitted || confirming || (failed && Boolean(app.submit_attempted_at));
  const queuedWaiting = !queuedDone && Boolean(app.approved_at);
  const queuedStage: TimelineStage = {
    key: "queued",
    label: "Queued",
    state: queuedDone ? "completed" : queuedWaiting ? "in-progress" : "pending",
    at: queuedDone || queuedWaiting ? (app.approved_at ?? null) : null,
    detail: queuedDone ? "Picked up" : queuedWaiting ? "Waiting for worker" : undefined,
  };

  const applyingDone = submitted || confirming || (failed && Boolean(app.submit_attempted_at));
  const applyingStage: TimelineStage = {
    key: "applying",
    label: "Applying",
    state: submitting ? "in-progress" : applyingDone ? "completed" : "pending",
    at: app.submit_attempted_at ?? null,
    detail: submitting ? "Applying…" : applyingDone ? "Submitted to ATS" : undefined,
  };

  let confirmedStage: TimelineStage;
  if (submitted) {
    confirmedStage = {
      key: "confirmed",
      label: "Confirmed",
      state: "completed",
      at: evidence?.at ?? evidence?.date ?? app.updated_at ?? null,
      detail: evidence?.url ? "View confirmation" : (evidence?.text ?? "Confirmation received"),
    };
  } else if (confirming) {
    confirmedStage = {
      key: "confirmed",
      label: "Confirming",
      state: "in-progress",
      at: null,
      detail: evidence?.text ?? "Awaiting confirmation",
    };
  } else if (failed) {
    confirmedStage = {
      key: "confirmed",
      label: "Confirmed",
      state: "failed",
      at: app.updated_at ?? null,
      detail: "Needs attention",
    };
  } else {
    confirmedStage = { key: "confirmed", label: "Confirmed", state: "pending", at: null };
  }

  return [approvedStage, queuedStage, applyingStage, confirmedStage];
}

function formatTime(iso: string | null): string | null {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  return d.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" });
}

function safeHref(url: string | undefined): string | null {
  if (!url) return null;
  return /^https?:\/\//i.test(url) ? url : null;
}

export default function SubmissionTimeline({ app }: { app: JobApplication }) {
  const stages = getTimelineStages(app);
  const hasFailure = stages.some((s) => s.state === "failed");
  const confirmationHref = safeHref(app.submission_evidence?.url);

  return (
    <div className="space-y-3">
      <ol
        aria-label="Submission timeline"
        className="flex flex-col gap-3 md:flex-row md:gap-4"
      >
        {stages.map((stage) => {
          const time = formatTime(stage.at);
          return (
            <li
              key={stage.key}
              data-state={stage.state}
              className="flex flex-1 items-start gap-3 rounded-md border border-border bg-surface p-3"
            >
              <span
                aria-hidden="true"
                className={`mt-1.5 h-2.5 w-2.5 shrink-0 rounded-full ${DOT_CLASS[stage.state]}`}
              />
              <div className="min-w-0 space-y-0.5">
                <div className="flex items-baseline gap-2">
                  <span className={`text-sm font-medium ${LABEL_CLASS[stage.state]}`}>
                    {stage.label}
                  </span>
                  <span className="sr-only">{STATE_TEXT[stage.state]}</span>
                  {time && <span className="text-xs text-fg-muted">{time}</span>}
                </div>
                {stage.key === "confirmed" && stage.state === "completed" && confirmationHref ? (
                  <a
                    href={confirmationHref}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-xs text-indigo-300 underline"
                  >
                    View confirmation
                  </a>
                ) : (
                  stage.detail && (
                    <p
                      className={`line-clamp-2 text-xs ${
                        stage.state === "failed" ? "text-red-300" : "text-fg-muted"
                      }`}
                    >
                      {stage.detail}
                    </p>
                  )
                )}
              </div>
            </li>
          );
        })}
      </ol>
      {hasFailure && (
        <div role="alert" className="rounded-md border border-border bg-surface p-3">
          <p className="text-xs font-medium text-amber-300">Needs attention</p>
          {app.apply_blocked_reason && (
            <p className="mt-1 text-sm text-red-300">{app.apply_blocked_reason}</p>
          )}
        </div>
      )}
    </div>
  );
}
