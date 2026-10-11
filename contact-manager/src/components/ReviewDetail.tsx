"use client";

import type { JobApplication } from "@/lib/types";

type ReviewDetailProps = {
  app: JobApplication;
  onApprove: (app: JobApplication) => void;
  onReject: (app: JobApplication) => void;
  onEdit?: (app: JobApplication) => void;
  onBack: () => void;
  approving?: boolean;
};

const NEEDS_REVIEW_PREFIX = "NEEDS HUMAN REVIEW";
const RESUME_KEY_PATTERN = /headline|summary|bullet|skill/i;

// ── Helpers ────────────────────────────────────────────────────────────────────

function isFlagged(answer: string | null | undefined) {
  return typeof answer === "string" && answer.startsWith(NEEDS_REVIEW_PREFIX);
}

export function getReviewIssues(app: JobApplication): string[] {
  const issues: string[] = [];
  const preview = app.apply_preview;

  if (preview) {
    for (const [question, answer] of Object.entries(preview.eligibility_answers ?? {})) {
      if (isFlagged(answer)) issues.push(`Answer needed: ${question}`);
    }
    for (const [question, answer] of Object.entries(preview.screening_answers ?? {})) {
      if (isFlagged(answer)) issues.push(`Answer needed: ${question}`);
    }
    for (const [label, filled] of Object.entries(preview.fill_report?.eligibility ?? {})) {
      if (filled === false) issues.push(`Not filled: ${label}`);
    }
  }

  if (app.apply_blocked_reason) issues.push(app.apply_blocked_reason);
  return issues;
}

function matchScore(score: number | null) {
  if (score === null || score === undefined || Number.isNaN(score)) return null;
  const scaled = score >= 0 && score <= 1 ? score * 100 : score;
  return Math.round(Math.min(100, Math.max(0, scaled)));
}

function formatDate(value: string | null) {
  if (!value) return null;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}

// ── Subcomponents ──────────────────────────────────────────────────────────────

function QaRows({ entries }: { entries: [string, string][] }) {
  return (
    <dl className="divide-y divide-border rounded-lg border border-border bg-surface">
      {entries.map(([question, answer]) => {
        const flagged = isFlagged(answer);
        return (
          <div
            key={question}
            className="flex flex-col gap-1 p-3"
            {...(flagged ? { "data-testid": "qa-flagged" } : {})}
          >
            <dt className="text-sm text-fg-muted">{question}</dt>
            <dd className={flagged ? "text-sm text-amber-300" : "text-sm text-fg"}>
              {answer || "(blank)"}
            </dd>
          </div>
        );
      })}
    </dl>
  );
}

// ── Component ──────────────────────────────────────────────────────────────────

export default function ReviewDetail({
  app,
  onApprove,
  onReject,
  onEdit,
  onBack,
  approving = false,
}: ReviewDetailProps) {
  const issues = getReviewIssues(app);
  const hasUnresolvedChecks = issues.length > 0;

  const preview = app.apply_preview;
  const eligibility = Object.entries(preview?.eligibility_answers ?? {});
  const screening = Object.entries(preview?.screening_answers ?? {});
  const resumeRows = Object.entries(preview?.field_values ?? {}).filter(([key]) =>
    RESUME_KEY_PATTERN.test(key),
  );
  const score = matchScore(app.pick_score);
  const posted = formatDate(app.posted_at ?? null);

  return (
    <div className="flex min-h-screen flex-col bg-bg text-fg">
      <div className="flex-1 overflow-y-auto">
        <div className="mx-auto flex w-full max-w-2xl flex-col gap-6 p-4">
          <button
            type="button"
            onClick={onBack}
            aria-label="Back to queue"
            className="min-h-[44px] self-start text-sm text-indigo-300"
          >
            ← Back to queue
          </button>

          {hasUnresolvedChecks && (
            <div
              data-testid="issues-banner"
              role="alert"
              className="rounded-lg border border-border-strong bg-surface p-3 text-amber-300"
            >
              <p className="mb-1 text-sm font-semibold">Resolve these before approving</p>
              <ul className="list-disc space-y-1 pl-5 text-sm">
                {issues.map((issue, index) => (
                  <li key={`${index}-${issue}`}>{issue}</li>
                ))}
              </ul>
            </div>
          )}

          <section aria-labelledby="review-header" className="flex flex-col gap-2">
            <h1 id="review-header" className="text-xl font-semibold text-fg">
              {app.company}
            </h1>
            <p className="text-fg-muted">{app.role}</p>
            {app.location && <p className="text-sm text-fg-dim">{app.location}</p>}
            {score !== null && (
              <span className="self-start rounded-full border border-border-strong px-3 py-1 text-sm text-emerald-300">
                {score}/100 match
              </span>
            )}
            {app.pick_reasoning && (
              <div className="mt-1">
                <h2 className="text-sm font-semibold text-fg">Why it matched</h2>
                <p className="text-sm text-fg-muted">{app.pick_reasoning}</p>
              </div>
            )}
          </section>

          <section aria-labelledby="review-eligibility" className="flex flex-col gap-2">
            <h2 id="review-eligibility" className="text-base font-semibold text-fg">
              Eligibility
            </h2>
            {eligibility.length > 0 ? (
              <QaRows entries={eligibility} />
            ) : (
              <p className="text-sm text-fg-dim">No eligibility answers</p>
            )}
          </section>

          <section aria-labelledby="review-resume" className="flex flex-col gap-2">
            <h2 id="review-resume" className="text-base font-semibold text-fg">
              Resume preview
            </h2>
            {resumeRows.length > 0 ? (
              <dl className="divide-y divide-border rounded-lg border border-border bg-surface">
                {resumeRows.map(([key, value]) => (
                  <div key={key} className="flex flex-col gap-1 p-3">
                    <dt className="text-sm text-fg-muted">{key}</dt>
                    <dd className="whitespace-pre-wrap text-sm text-fg">{value}</dd>
                  </div>
                ))}
              </dl>
            ) : (
              <div className="flex flex-col gap-1">
                <p className="text-sm text-fg-dim">Resume preview not available</p>
                {app.job_url && (
                  <a
                    href={app.job_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-sm text-indigo-300 underline"
                  >
                    View posting
                  </a>
                )}
              </div>
            )}
          </section>

          <section aria-labelledby="review-screening" className="flex flex-col gap-2">
            <h2 id="review-screening" className="text-base font-semibold text-fg">
              Screening answers
            </h2>
            {screening.length > 0 ? (
              <QaRows entries={screening} />
            ) : (
              <p className="text-sm text-fg-dim">No screening questions</p>
            )}
          </section>

          <section aria-labelledby="review-details">
            <h2 id="review-details" className="sr-only">
              Application details
            </h2>
            <details className="rounded-lg border border-border bg-surface p-3">
              <summary className="cursor-pointer text-base font-semibold text-fg">Details</summary>
              <dl className="mt-2 space-y-1 text-sm">
                <div className="flex gap-2">
                  <dt className="text-fg-muted">Platform:</dt>
                  <dd className="text-fg">{preview?.platform ?? "Unknown"}</dd>
                </div>
                <div className="flex gap-2">
                  <dt className="text-fg-muted">Posted:</dt>
                  <dd className="text-fg">{posted ?? "Unknown"}</dd>
                </div>
                <div className="flex gap-2">
                  <dt className="text-fg-muted">Source:</dt>
                  <dd className="text-fg">{app.source ?? "Unknown"}</dd>
                </div>
              </dl>
            </details>
          </section>
        </div>
      </div>

      <div className="sticky bottom-0 border-t border-border bg-surface p-4">
        <div className="mx-auto flex w-full max-w-2xl gap-2">
          <button
            type="button"
            onClick={() => onApprove(app)}
            disabled={hasUnresolvedChecks || approving}
            title={hasUnresolvedChecks ? "Resolve checks to approve" : undefined}
            className="min-h-[48px] flex-1 rounded-lg bg-emerald-600 font-medium text-white disabled:opacity-50"
          >
            {approving ? "Approving…" : "Approve"}
          </button>
          <button
            type="button"
            onClick={() => onReject(app)}
            disabled={approving}
            className="min-h-[48px] flex-1 rounded-lg border border-border-strong font-medium text-red-300 disabled:opacity-50"
          >
            Reject
          </button>
          {onEdit && (
            <button
              type="button"
              onClick={() => onEdit(app)}
              disabled={approving}
              className="min-h-[48px] flex-1 rounded-lg border border-border-strong font-medium text-fg disabled:opacity-50"
            >
              Edit
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
