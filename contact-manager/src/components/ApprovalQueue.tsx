"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import type { JobApplication } from "@/lib/types";
import { PeoplePanel } from "@/components/PeoplePanel";

export const UNDO_SECONDS = 5;
const SEGMENTS = 10;
export const QUEUE_POLL_MS = 10_000;

type Pending = { id: string; secondsLeft: number };
type Today = { submitted: number; cap: number };
type DocLinks = { resume_url: string | null; cover_letter_url: string | null };

const NEEDS_REVIEW = "NEEDS HUMAN REVIEW";

function answersOf(app: JobApplication): [string, string][] {
  const preview = app.apply_preview;
  if (!preview) return [];
  return [
    ...Object.entries(preview.screening_answers ?? {}),
    ...Object.entries(preview.eligibility_answers ?? {}),
  ].filter(([, v]) => typeof v === "string" && v.trim() !== "");
}

function postedAgo(iso: string | null | undefined): string | null {
  if (!iso) return null;
  const days = Math.floor((Date.now() - Date.parse(iso)) / 86_400_000);
  if (!Number.isFinite(days) || days < 0) return null;
  return days === 0 ? "posted today" : days === 1 ? "posted yesterday" : `posted ${days} days ago`;
}

function cardMeta(app: JobApplication): string {
  const snapshot = (app.posting_snapshot ?? {}) as Record<string, unknown>;
  const location = app.location ?? (typeof snapshot.location === "string" ? snapshot.location : null);
  const posted = postedAgo(app.posted_at ?? (typeof snapshot.posted_at === "string" ? snapshot.posted_at : null));
  return [location, app.source, posted].filter(Boolean).join(" · ");
}

function heldUntil(app: JobApplication, now = Date.now()): Date | null {
  const until = app.referral_hold_until ? Date.parse(app.referral_hold_until) : NaN;
  return Number.isFinite(until) && until > now ? new Date(until) : null;
}

// Cards waiting on a referral go last; the rest by pick score, then newest.
function byPickThenNewest(a: JobApplication, b: JobApplication) {
  return Number(Boolean(heldUntil(a))) - Number(Boolean(heldUntil(b)))
    || (b.pick_score ?? 0) - (a.pick_score ?? 0) || b.created_at.localeCompare(a.created_at);
}

const VISA_TONE = {
  good: "border-emerald-500/40 text-emerald-300",
  review: "border-amber-500/40 text-amber-300",
  none: "border-border text-fg-dim",
} as const;

function companyLine(app: JobApplication): { text: string; full: boolean } | null {
  const c = app.company_30d;
  if (!c || c.others === 0) return null;
  const text = `${c.others} other ${c.others === 1 ? "application" : "applications"} here in 30 days`;
  return { text: c.cap > 0 ? `${text} (cap ${c.cap})` : text, full: c.cap > 0 && c.others >= c.cap };
}

function peopleLine(app: JobApplication): string | null {
  if (!app.people) return null;
  const parts = [];
  if (app.people.known > 0) parts.push(`You know ${app.people.known} ${app.people.known === 1 ? "person" : "people"} here`);
  if (app.people.linked > 0) parts.push(`${app.people.linked} linked`);
  return parts.length ? parts.join(" · ") : null;
}

// The one-tap approval queue (spec 2026-10-08 §9). Submit starts a 5-second bar with Undo; only
// when it runs out is the approval sent, so a mis-tap or closing the page sends nothing. The
// approval is bound to the preview revision rendered on the card (preview_revision_hash).
export function ApprovalQueue() {
  const [rows, setRows] = useState<JobApplication[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [pending, setPending] = useState<Pending | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [docs, setDocs] = useState<Record<string, DocLinks>>({});
  const [today, setToday] = useState<Today | null>(null);
  const [peopleOpen, setPeopleOpen] = useState<string | null>(null);
  const [outcomes, setOutcomes] = useState<JobApplication[]>([]);
  const countdown = useRef<ReturnType<typeof setInterval> | null>(null);

  const load = useCallback(async () => {
    try {
      const res = await fetch("/api/applications?view=queue");
      if (!res.ok) return;
      const body = (await res.json()) as { applications?: JobApplication[] };
      setRows(body.applications ?? []);
      const todayRes = await fetch("/api/applications/today");
      if (todayRes.ok) setToday((await todayRes.json()) as Today);
      const outcomesRes = await fetch("/api/applications?view=outcomes");
      if (outcomesRes.ok) {
        const o = (await outcomesRes.json()) as { applications?: JobApplication[] };
        setOutcomes((o.applications ?? []).filter((a) => a.outcome_evidence));
      }
    } catch {
      // the next poll tries again
    } finally {
      setLoaded(true);
    }
  }, []);

  useEffect(() => {
    load();
    const timer = setInterval(load, QUEUE_POLL_MS);
    return () => clearInterval(timer);
  }, [load]);

  // Leaving the page during a countdown sends nothing.
  useEffect(() => () => {
    if (countdown.current) clearInterval(countdown.current);
  }, []);

  const send = useCallback(async (app: JobApplication) => {
    setBusy(app.id);
    try {
      const res = await fetch(`/api/applications/${app.id}/submit`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ revision_hash: app.preview_revision_hash }),
      });
      const body = (await res.json().catch(() => ({}))) as { error?: string };
      if (res.ok) {
        toast.success(`${app.company}: approved. It will be submitted within a minute.`);
      } else {
        toast.error(body.error ?? "Could not approve this application");
      }
    } catch {
      toast.error("Could not reach the server");
    }
    setBusy(null);
    load();
  }, [load]);

  const startSubmit = (app: JobApplication) => {
    if (pending || countdown.current) return;
    setPending({ id: app.id, secondsLeft: UNDO_SECONDS });
    let left = UNDO_SECONDS;
    countdown.current = setInterval(() => {
      left -= 1;
      if (left > 0) {
        setPending({ id: app.id, secondsLeft: left });
        return;
      }
      if (countdown.current) clearInterval(countdown.current);
      countdown.current = null;
      setPending(null);
      send(app);
    }, 1000);
  };

  const undo = () => {
    if (countdown.current) clearInterval(countdown.current);
    countdown.current = null;
    setPending(null);
    toast("Not sent");
  };

  const post = async (url: string, init: RequestInit, ok: string) => {
    try {
      const res = await fetch(url, init);
      const body = (await res.json().catch(() => ({}))) as { error?: string };
      if (res.ok) toast.success(ok);
      else toast.error(body.error ?? "That did not work");
    } catch {
      toast.error("Could not reach the server");
    }
    load();
  };

  const skip = (app: JobApplication) =>
    post(`/api/applications/${app.id}`, {
      method: "PATCH", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ stage: "withdrawn" }),
    }, `${app.company}: skipped`);

  const reprepare = (app: JobApplication) =>
    post(`/api/applications/${app.id}/requeue-preview`, { method: "POST" },
      `${app.company}: it will be prepared again`);

  const resolve = (app: JobApplication, submitted: boolean) =>
    post(`/api/applications/${app.id}/resolve-confirmation`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ submitted }),
    }, submitted ? `${app.company}: marked submitted` : `${app.company}: back in review`);

  const hold = async (app: JobApplication) => {
    await post(`/api/applications/${app.id}/hold`, { method: "POST" },
      `${app.company}: waiting on a referral. Add the people to ask below.`);
    setPeopleOpen(app.id);
  };

  const stopWaiting = (app: JobApplication) =>
    post(`/api/applications/${app.id}/hold`, { method: "DELETE" }, `${app.company}: no longer waiting`);

  const undoOutcome = (app: JobApplication) =>
    post(`/api/applications/${app.id}`, {
      method: "PATCH", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ stage: app.outcome_evidence?.previous_stage }),
    }, `${app.company}: back to ${app.outcome_evidence?.previous_stage?.replace("_", " ")}`);

  const loadDocs = async (app: JobApplication) => {
    try {
      const res = await fetch(`/api/applications/${app.id}/files`);
      const body = (await res.json()) as DocLinks;
      setDocs((cur) => ({ ...cur, [app.id]: body }));
    } catch {
      toast.error("Could not load the documents");
    }
  };

  const ready = rows.filter((r) => r.automation_status === "ready_for_review").sort(byPickThenNewest);
  const inFlight = rows.filter((r) => r.automation_status === "approved" || r.automation_status === "submitting");
  const needsYou = rows.filter((r) => r.automation_status === "needs_input" || r.automation_status === "needs_confirmation");
  const submitted = rows.filter((r) => r.automation_status === "submitted");
  const cap = today?.cap ?? 0;
  const sentToday = today?.submitted ?? 0;
  // Ten segments whatever the cap: each one is a tenth of the day's budget.
  const lit = cap > 0 ? Math.min(SEGMENTS, Math.floor((sentToday / cap) * SEGMENTS)) : 0;

  return (
    <section aria-label="Approval queue" className="flex flex-col gap-4">
      {today && (
        <div className="flex flex-col gap-2">
          <h2 className="text-base font-medium text-fg">
            {cap > 0 ? `Today: ${sentToday} of ${cap} submitted` : `Today: ${sentToday} submitted`}
          </h2>
          {cap > 0 && (
            <div role="progressbar" aria-valuemin={0} aria-valuemax={cap} aria-valuenow={Math.min(sentToday, cap)}
              className="grid w-full max-w-md grid-cols-10 gap-1">
              {Array.from({ length: SEGMENTS }, (_, i) => (
                <div key={i} className={`h-2 rounded-full ${i < lit ? "bg-emerald-500" : "bg-surface-2"}`} />
              ))}
            </div>
          )}
          {cap > 0 && sentToday >= cap && ready.length > 0 && (
            <p className="text-sm text-amber-300">Today&apos;s cap is reached. Anything you approve now goes out tomorrow.</p>
          )}
        </div>
      )}

      {loaded && ready.length === 0 && inFlight.length === 0 && needsYou.length === 0 && (
        <p className="text-sm text-fg-dim">Nothing is waiting for you. New applications appear here once they are prepared.</p>
      )}

      {outcomes.length > 0 && (
        <div className="flex flex-col gap-2">
          <h3 className="text-sm font-medium text-fg">Replies from companies</h3>
          {outcomes.map((app) => {
            const e = app.outcome_evidence!;
            const undone = app.stage === e.previous_stage;
            const interview = e.kind === "interview";
            return (
              <article key={app.id} aria-label={`${app.company} reply`}
                className={`flex flex-wrap items-center gap-3 rounded-lg border bg-surface px-4 py-3 text-sm ${interview ? "border-emerald-500/40" : "border-border"}`}>
                <div className="min-w-[12rem] flex-1">
                  <p className="font-medium text-fg">
                    {app.company} &middot; {app.role}:{" "}
                    <span className={interview ? "text-emerald-300" : "text-fg-muted"}>
                      {interview ? "interview invite" : "not moving forward"}
                    </span>
                  </p>
                  <p className="text-fg-dim">&ldquo;{e.subject}&rdquo; &middot; {new Date(e.date).toLocaleDateString(undefined, { month: "short", day: "numeric" })}</p>
                </div>
                {undone ? (
                  <span className="text-xs text-fg-dim">Undone</span>
                ) : (
                  <button type="button" onClick={() => undoOutcome(app)}
                    className="rounded-md border border-border-strong px-3 py-2 text-fg">Not right? Undo</button>
                )}
              </article>
            );
          })}
        </div>
      )}

      {needsYou.length > 0 && (
        <div className="flex flex-col gap-2">
          <h3 className="text-sm font-medium text-amber-300">Needs you</h3>
          {needsYou.map((app) => (
            <article key={app.id} aria-label={`${app.company} needs you`}
              className="flex flex-wrap items-center gap-3 rounded-lg border border-amber-500/40 bg-surface px-4 py-3 text-sm">
              <div className="min-w-[12rem] flex-1">
                <p className="font-medium text-fg">{app.company} &middot; {app.role}</p>
                <p className="text-fg-muted">
                  {app.automation_status === "needs_confirmation"
                    ? "Submit was clicked but no confirmation appeared. Did it go through?"
                    : app.apply_blocked_reason ?? "Something needs a look."}
                </p>
              </div>
              {app.automation_status === "needs_confirmation" ? (
                <div className="flex gap-2">
                  <button type="button" onClick={() => resolve(app, true)}
                    className="rounded-md bg-emerald-600 px-3 py-2 text-white">It went through</button>
                  <button type="button" onClick={() => resolve(app, false)}
                    className="rounded-md border border-border-strong px-3 py-2 text-fg">It did not</button>
                </div>
              ) : (
                <button type="button" onClick={() => reprepare(app)}
                  className="rounded-md border border-border-strong px-3 py-2 text-fg">Prepare again</button>
              )}
            </article>
          ))}
        </div>
      )}

      {ready.length > 0 && (
        <div className="flex flex-col gap-3">
          <h3 className="text-sm font-medium text-fg">Ready to submit ({ready.length})</h3>
          {ready.map((app) => {
            const answers = answersOf(app);
            const coverage = app.apply_preview?.keyword_coverage;
            const counting = pending?.id === app.id;
            const links = docs[app.id];
            const held = heldUntil(app);
            const company = companyLine(app);
            return (
              <article key={app.id} aria-label={`${app.company} ${app.role}`}
                className="flex flex-col gap-3 rounded-xl border border-border bg-surface p-4">
                <header className="flex flex-wrap items-baseline justify-between gap-2">
                  <div>
                    <p className="text-base font-medium text-fg">{app.company}</p>
                    <p className="text-sm text-fg-muted">{app.role}</p>
                    {cardMeta(app) && <p className="text-xs text-fg-dim">{cardMeta(app)}</p>}
                    {peopleLine(app) && <p className="text-xs text-emerald-300">{peopleLine(app)}</p>}
                    {company && (
                      <p className={`text-xs ${company.full ? "text-amber-300" : "text-fg-dim"}`}>{company.text}</p>
                    )}
                  </div>
                  <div className="flex flex-wrap gap-2 text-xs">
                    {app.apply_preview?.platform && (
                      <span className="rounded-full border border-border px-2 py-0.5 text-fg-muted">{app.apply_preview.platform}</span>
                    )}
                    {app.pick_verdict && (
                      <span className="rounded-full border border-emerald-500/40 px-2 py-0.5 text-emerald-300">{app.pick_verdict} fit</span>
                    )}
                    {app.visa && (
                      <span className={`rounded-full border px-2 py-0.5 ${VISA_TONE[app.visa.tone]}`}>{app.visa.label}</span>
                    )}
                  </div>
                </header>

                {answers.length > 0 && (
                  <dl className="grid gap-1 text-sm">
                    {answers.map(([q, a]) => (
                      <div key={q} className="grid gap-0.5 sm:grid-cols-[minmax(0,2fr)_minmax(0,3fr)] sm:gap-3">
                        <dt className="text-fg-dim">{q}</dt>
                        <dd className={a.startsWith(NEEDS_REVIEW) ? "text-amber-300" : "text-fg"}>{a}</dd>
                      </div>
                    ))}
                  </dl>
                )}

                {coverage && (coverage.covered.length > 0 || coverage.missing.length > 0) && (
                  <p className="text-xs text-fg-muted">
                    {coverage.covered.length > 0 && <>Your documents cover: {coverage.covered.join(", ")}. </>}
                    {coverage.missing.length > 0 && <>The posting also asks for: {coverage.missing.join(", ")}.</>}
                  </p>
                )}

                {held && (
                  <p role="note" className="flex flex-wrap items-center gap-2 text-sm text-amber-300">
                    Waiting on a referral until {held.toLocaleDateString(undefined, { month: "short", day: "numeric" })}.
                    <button type="button" onClick={() => stopWaiting(app)} className="text-fg-muted underline">Stop waiting</button>
                  </p>
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
                  ) : (
                    <button type="button" onClick={() => loadDocs(app)} className="text-indigo-300 underline">Show documents</button>
                  )}
                  <button type="button" onClick={() => setPeopleOpen(peopleOpen === app.id ? null : app.id)}
                    aria-expanded={peopleOpen === app.id} className="text-indigo-300 underline">People</button>
                  {!held && (
                    <button type="button" onClick={() => hold(app)} disabled={!!pending}
                      className="text-indigo-300 underline disabled:opacity-50">Ask for a referral first</button>
                  )}
                </div>

                {peopleOpen === app.id && <PeoplePanel applicationId={app.id} onChange={load} />}

                {counting ? (
                  <div role="status" className="flex items-center justify-between gap-3 rounded-md bg-indigo-600/20 px-3 py-2 text-sm text-fg">
                    <span>Submitting in {pending.secondsLeft}s</span>
                    <button type="button" onClick={undo} className="rounded-md border border-border-strong px-3 py-1 text-fg">Undo</button>
                  </div>
                ) : (
                  <div className="flex gap-2">
                    <button type="button" onClick={() => startSubmit(app)} disabled={!!pending || busy === app.id}
                      className="flex-1 rounded-md bg-indigo-600 px-4 py-2 text-sm font-medium text-white disabled:opacity-50 sm:flex-none">
                      Submit
                    </button>
                    <button type="button" onClick={() => skip(app)} disabled={!!pending}
                      className="rounded-md border border-border-strong px-4 py-2 text-sm text-fg disabled:opacity-50">
                      Skip
                    </button>
                  </div>
                )}
              </article>
            );
          })}
        </div>
      )}

      {inFlight.length > 0 && (
        <div className="flex flex-col gap-1 text-sm">
          <h3 className="font-medium text-fg">On the way</h3>
          {inFlight.map((app) => (
            <p key={app.id} className="text-fg-muted">
              {app.company} &middot; {app.role}: {app.automation_status === "approved" ? "queued" : "submitting now"}
            </p>
          ))}
        </div>
      )}

      {submitted.length > 0 && (
        <div className="flex flex-col gap-1 text-sm">
          <h3 className="font-medium text-fg">Submitted in the last two weeks ({submitted.length})</h3>
          {submitted.map((app) => {
            const proof = app.submission_evidence;
            return (
              <p key={app.id} className="text-fg-muted">
                {app.company} &middot; {app.role}
                {proof?.url ? (
                  <> &middot; <a href={proof.url} target="_blank" rel="noreferrer" className="text-indigo-300 underline">confirmation</a></>
                ) : proof?.subject ? (
                  <> &middot; receipt: {proof.subject}</>
                ) : null}
              </p>
            );
          })}
        </div>
      )}
    </section>
  );
}
