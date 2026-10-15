"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import type { JobApplication } from "@/lib/types";
import { UNDO_SECONDS, UndoManager, type PendingAction } from "@/lib/undo-manager";
import { QUEUE_POLL_MS } from "@/components/ApprovalQueue";

export type Today = { submitted: number; cap: number };
export type Pending = { id: string; secondsLeft: number };
export type DocLinks = { resume_url: string | null; cover_letter_url: string | null };
export type ReviewBucket = "ready" | "checks" | "blocked";
export type ReviewItem = { app: JobApplication; bucket: ReviewBucket; issues: string[]; priority: number };
export type QueueCounts = { toReview: number; preparing: number; submitted: number };

const NEEDS_REVIEW = "NEEDS HUMAN REVIEW";
const DAY_MS = 86_400_000;

// pick_score may be stored 0-1 or 0-100; fall back to the verdict when it is missing.
function matchQuality(app: JobApplication): number {
  if (app.pick_score != null) return app.pick_score > 1 ? Math.min(app.pick_score / 100, 1) : Math.max(app.pick_score, 0);
  if (app.pick_verdict === "strong") return 1;
  if (app.pick_verdict === "maybe") return 0.6;
  return 0.3;
}

// Confirmations and blocked rows are time-sensitive; anything waiting longer gets a mild boost,
// and a fresher posting is more likely to still be open.
function urgency(app: JobApplication, bucket: ReviewBucket, now: number): number {
  const base = app.automation_status === "needs_confirmation" ? 3 : bucket === "ready" ? 1 : 2;
  const waited = Math.min(Math.max((now - Date.parse(app.updated_at)) / DAY_MS, 0), 3) / 3;
  const posted = app.posted_at ? Date.parse(app.posted_at) : NaN;
  const fresh = Number.isFinite(posted) ? Math.max(0, 1 - (now - posted) / (14 * DAY_MS)) : 0;
  return base + waited + fresh * 0.5;
}

function reviewIssues(app: JobApplication): string[] {
  const issues: string[] = [];
  const preview = app.apply_preview;
  const answers = [
    ...Object.entries(preview?.screening_answers ?? {}),
    ...Object.entries(preview?.eligibility_answers ?? {}),
  ];
  for (const [q, a] of answers) {
    if (typeof a === "string" && a.startsWith(NEEDS_REVIEW)) issues.push(`Answer needed: ${q}`);
  }
  for (const [label, filled] of Object.entries(preview?.fill_report?.eligibility ?? {})) {
    if (!filled) issues.push(`Not filled: ${label}`);
  }
  return issues;
}

export function classify(app: JobApplication, now = Date.now()): ReviewItem | null {
  const status = app.automation_status;
  let bucket: ReviewBucket;
  let issues: string[] = [];
  if (status === "ready_for_review") {
    issues = reviewIssues(app);
    bucket = issues.length > 0 ? "checks" : "ready";
  } else if (
    status === "needs_input" || status === "needs_confirmation" ||
    status === "failed_retryable" || status === "failed_terminal"
  ) {
    bucket = "blocked";
    if (app.apply_blocked_reason) issues = [app.apply_blocked_reason];
  } else {
    return null;
  }
  return { app, bucket, issues, priority: urgency(app, bucket, now) * (0.25 + matchQuality(app)) };
}

export function buildReviewItems(rows: JobApplication[], now = Date.now()): ReviewItem[] {
  return rows
    .map((r) => classify(r, now))
    .filter((r): r is ReviewItem => r !== null)
    .sort((a, b) => b.priority - a.priority);
}

export function countQueue(rows: JobApplication[], items: ReviewItem[]): QueueCounts {
  const is = (s: string) => rows.filter((r) => r.automation_status === s).length;
  return {
    toReview: items.length,
    preparing: is("preparing") + is("approved") + is("submitting"),
    submitted: is("submitted"),
  };
}

// Data and actions for the review inbox. Submit starts a countdown with Undo; only when it runs
// out is the approval sent, bound to the preview_revision_hash the card rendered.
export function useApprovalQueue() {
  const [rows, setRows] = useState<JobApplication[]>([]);
  const [today, setToday] = useState<Today | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [failed, setFailed] = useState(false);
  const [pending, setPending] = useState<Pending | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [docs, setDocs] = useState<Record<string, DocLinks>>({});
  const manager = useRef<UndoManager | null>(null);
  if (manager.current === null) manager.current = new UndoManager();
  const scheduled = useRef<PendingAction | null>(null);

  const load = useCallback(async () => {
    try {
      const res = await fetch("/api/applications?view=queue");
      if (!res.ok) {
        setFailed(true);
        return;
      }
      const body = (await res.json()) as { applications?: JobApplication[] };
      setRows(body.applications ?? []);
      setFailed(false);
      const todayRes = await fetch("/api/applications/today");
      if (todayRes.ok) setToday((await todayRes.json()) as Today);
    } catch {
      setFailed(true);
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
  useEffect(() => {
    const m = manager.current;
    return () => m?.cancelAll();
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
      if (res.ok) toast.success(`${app.company}: approved. It will be submitted within a minute.`);
      else toast.error(body.error ?? "Could not approve this application");
    } catch {
      toast.error("Could not reach the server");
    }
    setBusy(null);
    load();
  }, [load]);

  const undo = useCallback(() => {
    if (scheduled.current) manager.current!.cancel(scheduled.current);
    const toastId = scheduled.current ? `undo-${scheduled.current.id}` : undefined;
    scheduled.current = null;
    setPending(null);
    if (toastId) toast("Not sent", { id: toastId });
    else toast("Not sent");
  }, []);

  const startSubmit = useCallback((app: JobApplication) => {
    if (pending || scheduled.current) return;

    const toastId = `undo-${app.id}`;
    const label = `${app.role} at ${app.company}`;
    const showToast = (secondsLeft: number) =>
      toast(`Approved ${label}`, {
        id: toastId,
        duration: UNDO_SECONDS * 1000,
        action: {
          label: `Undo (${secondsLeft}s)`,
          onClick: () => undo(),
        },
      });

    setPending({ id: app.id, secondsLeft: UNDO_SECONDS });
    showToast(UNDO_SECONDS);
    scheduled.current = manager.current!.schedule(
      { id: app.id, type: "approve", label },
      () => {
        scheduled.current = null;
        setPending(null);
        toast.dismiss(toastId);
        send(app);
      },
      (secondsLeft) => {
        setPending({ id: app.id, secondsLeft });
        showToast(secondsLeft);
      },
    );
  }, [pending, send, undo]);

  const post = useCallback(async (url: string, init: RequestInit, ok: string) => {
    try {
      const res = await fetch(url, init);
      const body = (await res.json().catch(() => ({}))) as { error?: string };
      if (res.ok) toast.success(ok);
      else toast.error(body.error ?? "That did not work");
    } catch {
      toast.error("Could not reach the server");
    }
    load();
  }, [load]);

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

  const loadDocs = async (app: JobApplication) => {
    try {
      const res = await fetch(`/api/applications/${app.id}/files`);
      const body = (await res.json()) as DocLinks;
      setDocs((cur) => ({ ...cur, [app.id]: body }));
    } catch {
      toast.error("Could not load the documents");
    }
  };

  const items = buildReviewItems(rows);
  return {
    rows, items, counts: countQueue(rows, items), today, loaded, failed,
    pending, busy, docs,
    startSubmit, undo, skip, reprepare, resolve, loadDocs, reload: load,
  };
}
