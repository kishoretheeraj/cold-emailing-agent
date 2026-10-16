"use client";

import Link from "next/link";
import type { JobApplication } from "@/lib/types";
import { useApprovalQueue, type ReviewBucket, type ReviewItem } from "@/hooks/useApprovalQueue";

const SECTIONS: { bucket: ReviewBucket; title: string; tone: string }[] = [
  { bucket: "ready", title: "Ready to approve", tone: "text-emerald-300" },
  { bucket: "checks", title: "Has unresolved checks", tone: "text-amber-300" },
  { bucket: "blocked", title: "Blocked or failed", tone: "text-red-300" },
];

const CARD_BORDER: Record<ReviewBucket, string> = {
  ready: "border-emerald-500/40",
  checks: "border-amber-500/40",
  blocked: "border-red-500/40",
};

function meta(app: JobApplication): string {
  return [app.location, app.source, app.apply_preview?.platform].filter(Boolean).join(" · ");
}

function Stat({ value, label }: { value: number; label: string }) {
  return (
    <span className="whitespace-nowrap">
      <span className="font-semibold text-fg">{value}</span> <span className="text-fg-muted">{label}</span>
    </span>
  );
}

// Home screen: everything that needs a human decision, most urgent and best-matched first.
export function ReviewQueue() {
  const q = useApprovalQueue();
  const { counts, today } = q;

  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-5 px-4 py-6 sm:px-6">
      <header className="flex flex-col gap-2">
        <div className="flex items-baseline justify-between gap-3">
          <h1 className="text-xl font-semibold text-fg">Review</h1>
          <div className="flex gap-3 text-sm">
            <Link href="/applications" className="text-indigo-300 underline">All applications</Link>
            <Link href="/?view=contacts" className="text-indigo-300 underline">Contacts</Link>
          </div>
        </div>
        <p aria-label="Queue counts" className="flex flex-wrap gap-x-2 gap-y-1 text-sm">
          <Stat value={counts.toReview} label="To review" />
          <span className="text-fg-dim">|</span>
          <Stat value={counts.preparing} label="Preparing" />
          <span className="text-fg-dim">|</span>
          <Stat value={counts.submitted} label="Submitted" />
        </p>
        {today && (
          <p className="text-xs text-fg-dim">
            {today.cap > 0 ? `Today: ${today.submitted} of ${today.cap} submitted` : `Today: ${today.submitted} submitted`}
          </p>
        )}
      </header>

      {q.failed && (
        <p role="alert" className="rounded-md border border-red-500/40 px-3 py-2 text-sm text-red-300">
          Couldn&apos;t load the queue. Retrying automatically.
        </p>
      )}

      {q.loaded && !q.failed && q.items.length === 0 && (
        <p className="text-sm text-fg-dim">Nothing is waiting for you. New applications appear here once they are prepared.</p>
      )}

      {SECTIONS.map(({ bucket, title, tone }) => {
        const list = q.items.filter((i) => i.bucket === bucket);
        if (list.length === 0) return null;
        return (
          <section key={bucket} aria-label={title} className="flex flex-col gap-3">
            <h2 className={`text-sm font-medium ${tone}`}>{title} ({list.length})</h2>
            {list.map((item) => <ReviewCard key={item.app.id} item={item} q={q} />)}
          </section>
        );
      })}
    </div>
  );
}

function ReviewCard({ item, q }: { item: ReviewItem; q: ReturnType<typeof useApprovalQueue> }) {
  const { app, bucket, issues } = item;
  const counting = q.pending?.id === app.id;
  const links = q.docs[app.id];
  const confirm = app.automation_status === "needs_confirmation";
  const retryable = app.automation_status === "failed_retryable" || app.automation_status === "needs_input";

  return (
    <article aria-label={`${app.company} ${app.role}`}
      className={`flex flex-col gap-3 rounded-xl border bg-surface p-4 ${CARD_BORDER[bucket]}`}>
      <header className="flex flex-col gap-1">
        <div className="flex flex-wrap items-center gap-2">
          <p className="text-base font-medium text-fg">{app.company}</p>
          {app.pick_verdict && (
            <span className="rounded-full border border-emerald-500/40 px-2 py-0.5 text-xs text-emerald-300">{app.pick_verdict} fit</span>
          )}
          {app.visa && (
            <span className="rounded-full border border-border px-2 py-0.5 text-xs text-fg-muted">{app.visa.label}</span>
          )}
        </div>
        <p className="text-sm text-fg-muted">{app.role}</p>
        {meta(app) && <p className="text-xs text-fg-dim">{meta(app)}</p>}
      </header>

      {confirm ? (
        <p className="text-sm text-fg-muted">Submit was clicked but no confirmation appeared. Did it go through?</p>
      ) : issues.length > 0 ? (
        <ul className={`flex flex-col gap-1 text-sm ${bucket === "blocked" ? "text-red-300" : "text-amber-300"}`}>
          {issues.map((msg) => <li key={msg}>{msg}</li>)}
        </ul>
      ) : bucket === "blocked" ? (
        <p className="text-sm text-fg-muted">Something needs a look.</p>
      ) : (
        <p className="text-sm text-emerald-300">All checks passed.</p>
      )}

      <div className="flex flex-wrap items-center gap-3 text-sm">
        {app.job_url && (
          <a href={app.job_url} target="_blank" rel="noreferrer" className="text-indigo-300 underline">Posting</a>
        )}
        {links ? (
          <>
            {links.resume_url && <a href={links.resume_url} target="_blank" rel="noreferrer" className="text-indigo-300 underline">Resume</a>}
            {links.cover_letter_url && <a href={links.cover_letter_url} target="_blank" rel="noreferrer" className="text-indigo-300 underline">Cover letter</a>}
          </>
        ) : bucket !== "blocked" ? (
          <button type="button" onClick={() => q.loadDocs(app)} className="text-indigo-300 underline">Show documents</button>
        ) : null}
      </div>

      {counting ? (
        <div role="status" className="flex items-center justify-between gap-3 rounded-md bg-indigo-600/20 px-3 py-2 text-sm text-fg">
          <span>Submitting in {q.pending?.secondsLeft}s</span>
          <button type="button" onClick={q.undo} className="rounded-md border border-border-strong px-3 py-2 text-fg">Undo</button>
        </div>
      ) : confirm ? (
        <div className="flex flex-wrap gap-2">
          <button type="button" onClick={() => q.resolve(app, true)}
            className="flex-1 rounded-md bg-emerald-600 px-4 py-3 text-sm font-medium text-white sm:flex-none">It went through</button>
          <button type="button" onClick={() => q.resolve(app, false)}
            className="flex-1 rounded-md border border-border-strong px-4 py-3 text-sm text-fg sm:flex-none">It did not</button>
        </div>
      ) : bucket === "blocked" ? (
        <div className="flex flex-wrap gap-2">
          {retryable && (
            <button type="button" onClick={() => q.reprepare(app)}
              className="flex-1 rounded-md border border-border-strong px-4 py-3 text-sm text-fg sm:flex-none">Prepare again</button>
          )}
          <button type="button" onClick={() => q.skip(app)}
            className="rounded-md border border-border-strong px-4 py-3 text-sm text-fg">Skip</button>
        </div>
      ) : (
        <div className="flex flex-wrap gap-2">
          <button type="button" onClick={() => q.startSubmit(app)} disabled={!!q.pending || q.busy === app.id}
            className={`flex-1 rounded-md px-4 py-3 text-sm font-medium text-white disabled:opacity-50 sm:flex-none ${
              bucket === "ready" ? "bg-indigo-600" : "bg-amber-700"
            }`}>
            {bucket === "ready" ? "Submit" : "Submit anyway"}
          </button>
          {bucket === "checks" && (
            <button type="button" onClick={() => q.reprepare(app)} disabled={!!q.pending}
              className="rounded-md border border-border-strong px-4 py-3 text-sm text-fg disabled:opacity-50">Prepare again</button>
          )}
          <button type="button" onClick={() => q.skip(app)} disabled={!!q.pending}
            className="rounded-md border border-border-strong px-4 py-3 text-sm text-fg disabled:opacity-50">Skip</button>
        </div>
      )}
    </article>
  );
}
