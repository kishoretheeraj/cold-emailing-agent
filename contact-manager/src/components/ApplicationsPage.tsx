"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/Badge";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/Select";
import { ConfirmModal } from "@/components/ui/ConfirmModal";
import { Loader2 } from "lucide-react";
import { ApplicationDetailSheet } from "@/components/ApplicationDetailSheet";
import { SystemHealthStrip } from "@/components/SystemHealthStrip";
import { TakeoverBanner } from "@/components/TakeoverBanner";
import { pickVerdictVariant } from "@/lib/applicationBadges";
import {
  JOB_APPLICATION_STAGES,
  JOB_APPLICATION_STAGE_LABELS,
  AUTOMATION_STATUS_LABELS,
  type AutomationStatus,
  type JobApplication,
  type JobApplicationStage,
} from "@/lib/types";

const SOURCE_OPTIONS = ["linkedin", "ats_scan", "jobright", "manual"] as const;

export function ApplicationsPage() {
  const [applications, setApplications] = useState<JobApplication[]>([]);
  const [loading, setLoading] = useState(true);
  const [company, setCompany] = useState("");
  const [role, setRole] = useState("");
  const [jobUrl, setJobUrl] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [selectedApplication, setSelectedApplication] = useState<JobApplication | null>(null);
  const [stageFilter, setStageFilter] = useState<string>("__all__");
  const [sourceFilter, setSourceFilter] = useState<string>("__all__");

  const SUBMITTING_IDS_STORAGE_KEY = "applications_submitting_ids";
  const TIMED_OUT_IDS_STORAGE_KEY = "applications_timed_out_ids";

  const [confirmingApplication, setConfirmingApplication] = useState<JobApplication | null>(null);
  const [approveLoading, setApproveLoading] = useState(false);
  const [submittingIds, setSubmittingIds] = useState<Set<string>>(() => {
    try {
      const raw = sessionStorage.getItem(SUBMITTING_IDS_STORAGE_KEY);
      return raw ? new Set(JSON.parse(raw) as string[]) : new Set();
    } catch {
      return new Set();
    }
  });
  // Merge review 2026-09-28, finding 5: a poll timeout doesn't mean the underlying GitHub
  // Actions run is dead -- it may still be installing dependencies or genuinely running. Rather
  // than silently dropping back to "Approve & Submit" (which just 409s against the still-set
  // approved_at with no explanation), a timed-out id moves here: a distinct, persisted "may
  // still be running" state offering an explicit re-check and an explicit reset, instead of
  // either pretending nothing happened or auto-resetting while a run could still be active.
  const [timedOutIds, setTimedOutIds] = useState<Set<string>>(() => {
    try {
      const raw = sessionStorage.getItem(TIMED_OUT_IDS_STORAGE_KEY);
      return raw ? new Set(JSON.parse(raw) as string[]) : new Set();
    } catch {
      return new Set();
    }
  });
  // Keyed the same way as submittingIds: id -> the row's last-known apply_blocked_reason once
  // polling observes one, so the UI can show a specific failure and a Try again action instead
  // of leaving the row looking stuck until the 90s timeout.
  const [blockedReasons, setBlockedReasons] = useState<Record<string, string>>({});
  // Polling's last-seen automation_status per id. The list row is stale after a submit attempt,
  // so Try again / resolve buttons gate on this first and fall back to the row's own value.
  const [liveStatuses, setLiveStatuses] = useState<Record<string, AutomationStatus>>({});
  const pollTimers = useRef<Record<string, ReturnType<typeof setTimeout>>>({});

  useEffect(() => {
    try {
      sessionStorage.setItem(SUBMITTING_IDS_STORAGE_KEY, JSON.stringify([...submittingIds]));
    } catch {
      // sessionStorage unavailable
    }
  }, [submittingIds]);

  useEffect(() => {
    try {
      sessionStorage.setItem(TIMED_OUT_IDS_STORAGE_KEY, JSON.stringify([...timedOutIds]));
    } catch {
      // sessionStorage unavailable
    }
  }, [timedOutIds]);

  useEffect(() => {
    return () => {
      Object.values(pollTimers.current).forEach(clearTimeout);
    };
  }, []);

  const load = async (stage: string = stageFilter, source: string = sourceFilter) => {
    setLoading(true);
    try {
      const params = new URLSearchParams();
      if (stage !== "__all__") params.set("stage", stage);
      if (source !== "__all__") params.set("source", source);
      const qs = params.toString();
      const res = await fetch(`/api/applications${qs ? `?${qs}` : ""}`);
      const data = await res.json();
      setApplications(data.applications ?? []);
    } catch {
      toast.error("Could not load applications");
    } finally {
      setLoading(false);
    }
  };

  const handleStageFilterChange = (v: string) => {
    setStageFilter(v);
    load(v, sourceFilter);
  };

  const handleSourceFilterChange = (v: string) => {
    setSourceFilter(v);
    load(stageFilter, v);
  };

  useEffect(() => {
    load();
  }, []);

  const handleAdd = async (e: FormEvent) => {
    e.preventDefault();
    if (!company.trim() || !role.trim()) {
      toast.error("Company and role are required");
      return;
    }
    setSubmitting(true);
    try {
      const res = await fetch("/api/applications", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ company, role, job_url: jobUrl || undefined }),
      });
      if (!res.ok) throw new Error("request failed");
      const data = await res.json();
      setApplications((prev) => [data.application, ...prev]);
      setCompany("");
      setRole("");
      setJobUrl("");
      toast.success("Application added");
    } catch {
      toast.error("Could not add application");
    } finally {
      setSubmitting(false);
    }
  };

  const handleStageChange = async (id: string, stage: JobApplicationStage) => {
    const prev = applications;
    setApplications((cur) => cur.map((a) => (a.id === id ? { ...a, stage } : a)));
    try {
      const res = await fetch(`/api/applications/${id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ stage }),
      });
      if (!res.ok) throw new Error("request failed");
    } catch {
      setApplications(prev);
      toast.error("Could not update stage");
    }
  };

  const handleApplicationSaved = (updated: JobApplication) => {
    setApplications((cur) => cur.map((a) => (a.id === updated.id ? updated : a)));
    setSelectedApplication(updated);
  };

  // C2: apply_agent_submit.yml's own budget is `timeout-minutes: 15` -- and before it ever gets
  // to launching a browser and driving an LLM-based form fill, it has to checkout, set up
  // Python, `pip install -r requirements-apply.txt`, and `playwright install --with-deps
  // chromium`. A 90s poll timeout meant this branch showed "still processing" on every
  // submission, success or failure, since neither terminal state was reachable that fast in
  // practice. 16 minutes gives a small margin over the workflow's own 15-minute budget.
  const POLL_INTERVAL_MS = 15000;
  const POLL_TIMEOUT_MS = 16 * 60 * 1000;

  const stopPolling = (id: string) => {
    setSubmittingIds((cur) => {
      const next = new Set(cur);
      next.delete(id);
      return next;
    });
    delete pollTimers.current[id];
  };

  // Shared by the recurring poll tick and the timed-out state's manual "Check now" -- one fetch,
  // one interpretation of the row's status. Returns "pending" (still no terminal state observed,
  // including on a fetch error -- best-effort) so callers decide what to do next: a tick
  // schedules another poll, a manual check re-arms active polling.
  const checkApplicationStatus = async (id: string): Promise<"applied" | "blocked" | "pending"> => {
    try {
      const res = await fetch(`/api/applications/${id}`);
      const data = await res.json();
      const app:
        | {
            stage?: string;
            apply_blocked_reason?: string | null;
            automation_status?: AutomationStatus;
          }
        | undefined = data.application;
      if (app?.stage === "applied") {
        toast.success("Application submitted");
        load(stageFilter, sourceFilter);
        return "applied";
      }
      if (app?.automation_status === "needs_confirmation") {
        // Checked before the generic blocked branch: the Submit click may have landed, so this
        // must never read as a plain failure with a retry.
        const reason = app.apply_blocked_reason ?? "Submit outcome unknown";
        setBlockedReasons((cur) => ({ ...cur, [id]: reason }));
        setLiveStatuses((cur) => ({ ...cur, [id]: "needs_confirmation" }));
        toast.error("Submit outcome unknown -- confirm whether it went through");
        return "blocked";
      }
      if (app?.apply_blocked_reason) {
        if (app.automation_status) {
          const status = app.automation_status;
          setLiveStatuses((cur) => ({ ...cur, [id]: status }));
        }
        setBlockedReasons((cur) => ({ ...cur, [id]: app.apply_blocked_reason as string }));
        toast.error("Submission failed -- see the row for details");
        return "blocked";
      }
    } catch {
      // best-effort; caller treats this the same as still-pending
    }
    return "pending";
  };

  const pollForCompletion = (id: string) => {
    const startedAt = Date.now();
    const tick = async () => {
      if (Date.now() - startedAt > POLL_TIMEOUT_MS) {
        stopPolling(id);
        // finding 5: a timeout is not evidence of failure -- the GitHub Actions run may still be
        // installing dependencies or genuinely mid-fill. Surface a distinct, recoverable state
        // instead of silently reverting to "Approve & Submit".
        setTimedOutIds((cur) => new Set(cur).add(id));
        toast.error("Still processing after 16 minutes -- check status or reset to try again");
        return;
      }
      const status = await checkApplicationStatus(id);
      if (status !== "pending") {
        stopPolling(id);
        return;
      }
      pollTimers.current[id] = setTimeout(tick, POLL_INTERVAL_MS);
    };
    pollTimers.current[id] = setTimeout(tick, POLL_INTERVAL_MS);
  };

  // finding 4: submittingIds is restored from sessionStorage on mount, but nothing previously
  // resumed the actual poll timer for those ids -- the row rendered "Submitting..." forever with
  // no way to observe success, failure, or a timeout. Guarded by pollTimers.current so this never
  // double-starts a poll that doApprove already kicked off in the same render pass.
  useEffect(() => {
    submittingIds.forEach((id) => {
      if (!pollTimers.current[id]) {
        pollForCompletion(id);
      }
    });
    // Mount-only: resumes whatever sessionStorage restored into the initial submittingIds state.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // finding 5: manual recovery from the timed-out state -- one fresh check rather than either
  // assuming success/failure or blindly resetting approval while a run may still be active.
  const handleCheckNow = async (id: string) => {
    const status = await checkApplicationStatus(id);
    setTimedOutIds((cur) => {
      const next = new Set(cur);
      next.delete(id);
      return next;
    });
    if (status === "pending") {
      // Still no terminal state -- resume active polling instead of leaving it in limbo.
      setSubmittingIds((cur) => new Set(cur).add(id));
      pollForCompletion(id);
    }
  };

  const doApprove = async () => {
    if (!confirmingApplication) return;
    const app = confirmingApplication;
    if (!app.preview_revision_hash) {
      toast.error("This preview has no revision hash yet -- reload and try again");
      setConfirmingApplication(null);
      return;
    }
    setApproveLoading(true);
    try {
      // Approval binds to the preview revision rendered in this row; if the preview changed
      // since, the server refuses (409) instead of approving something the human didn't see.
      const res = await fetch(`/api/applications/${app.id}/submit`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ revision_hash: app.preview_revision_hash }),
      });
      if (!res.ok) {
        const errBody = await res.json().catch(() => ({}));
        throw new Error(errBody.error || "request failed");
      }
      toast.success("Submission triggered -- watching for it to land");
      setBlockedReasons((cur) => {
        const next = { ...cur };
        delete next[app.id];
        return next;
      });
      setLiveStatuses((cur) => {
        const next = { ...cur };
        delete next[app.id];
        return next;
      });
      setSubmittingIds((cur) => new Set(cur).add(app.id));
      pollForCompletion(app.id);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Could not trigger submission");
    } finally {
      setApproveLoading(false);
      setConfirmingApplication(null);
    }
  };

  // I8: a row that ends up here still has approved_at set (the dispatch itself succeeded;
  // apply_agent.py failed downstream, inside the workflow) -- re-clicking Approve & Submit
  // directly would just 409 against approve_application's own already-approved guard. Clear it
  // via Task 1's reset_approval RPC first, then the row is a normal candidate for Approve again.
  const handleTryAgain = async (id: string) => {
    try {
      const res = await fetch(`/api/applications/${id}/reset-approval`, { method: "POST" });
      if (!res.ok) throw new Error("request failed");
      setBlockedReasons((cur) => {
        const next = { ...cur };
        delete next[id];
        return next;
      });
      // finding 5: the timed-out state also offers a reset (the row may genuinely be dead, not
      // just slow) -- clear it here too so a reset from either state converges on the same
      // "Approve & Submit" outcome once the row reloads.
      setTimedOutIds((cur) => {
        const next = new Set(cur);
        next.delete(id);
        return next;
      });
      setLiveStatuses((cur) => {
        const next = { ...cur };
        delete next[id];
        return next;
      });
      load(stageFilter, sourceFilter);
    } catch {
      toast.error("Could not reset -- try again in a moment");
    }
  };

  const handleRequeue = async (id: string) => {
    try {
      const res = await fetch(`/api/applications/${id}/requeue-preview`, { method: "POST" });
      if (!res.ok) throw new Error("request failed");
      setBlockedReasons((cur) => {
        const next = { ...cur };
        delete next[id];
        return next;
      });
      setLiveStatuses((cur) => {
        const next = { ...cur };
        delete next[id];
        return next;
      });
      load(stageFilter, sourceFilter);
    } catch {
      toast.error("Could not re-prepare -- try again in a moment");
    }
  };

  const handleResolveConfirmation = async (id: string, submitted: boolean) => {
    try {
      const res = await fetch(`/api/applications/${id}/resolve-confirmation`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ submitted }),
      });
      if (!res.ok) throw new Error("request failed");
      setBlockedReasons((cur) => {
        const next = { ...cur };
        delete next[id];
        return next;
      });
      setLiveStatuses((cur) => {
        const next = { ...cur };
        delete next[id];
        return next;
      });
      setTimedOutIds((cur) => {
        const next = new Set(cur);
        next.delete(id);
        return next;
      });
      load(stageFilter, sourceFilter);
    } catch {
      toast.error("Could not record that -- try again");
    }
  };

  return (
    <div className="p-6 flex flex-col gap-6">
      <h1 className="text-lg font-medium text-fg">Applications</h1>

      <SystemHealthStrip />

      <TakeoverBanner />

      <form onSubmit={handleAdd} className="flex flex-wrap items-end gap-3">
        <label className="flex flex-col gap-1 text-sm text-fg-muted">
          Company
          <input
            aria-label="Company"
            value={company}
            onChange={(e) => setCompany(e.target.value)}
            className="px-3 py-2 bg-surface-2 border border-border rounded-md text-sm text-fg"
          />
        </label>
        <label className="flex flex-col gap-1 text-sm text-fg-muted">
          Role
          <input
            aria-label="Role"
            value={role}
            onChange={(e) => setRole(e.target.value)}
            className="px-3 py-2 bg-surface-2 border border-border rounded-md text-sm text-fg"
          />
        </label>
        <label className="flex flex-col gap-1 text-sm text-fg-muted">
          Job URL
          <input
            aria-label="Job URL"
            value={jobUrl}
            onChange={(e) => setJobUrl(e.target.value)}
            className="px-3 py-2 bg-surface-2 border border-border rounded-md text-sm text-fg"
          />
        </label>
        <button
          type="submit"
          disabled={submitting}
          className="px-3 py-2 bg-indigo-600 text-white rounded-md text-sm disabled:opacity-50"
        >
          Add application
        </button>
      </form>

      <div className="flex gap-3">
        <label data-testid="stage-filter" className="flex flex-col gap-1 text-sm text-fg-muted">
          Stage
          <Select value={stageFilter} onValueChange={handleStageFilterChange}>
            <SelectTrigger>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="__all__">All stages</SelectItem>
              {JOB_APPLICATION_STAGES.map((s) => (
                <SelectItem key={s} value={s}>
                  {JOB_APPLICATION_STAGE_LABELS[s]}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </label>
        <label data-testid="source-filter" className="flex flex-col gap-1 text-sm text-fg-muted">
          Source
          <Select value={sourceFilter} onValueChange={handleSourceFilterChange}>
            <SelectTrigger>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="__all__">All sources</SelectItem>
              {SOURCE_OPTIONS.map((s) => (
                <SelectItem key={s} value={s}>
                  {s}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </label>
      </div>

      {loading ? (
        <p className="text-sm text-fg-dim">Loading...</p>
      ) : applications.length === 0 ? (
        <p className="text-sm text-fg-dim">No applications yet.</p>
      ) : (
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-fg-dim border-b border-border">
              <th className="py-2 pr-4">Company</th>
              <th className="py-2 pr-4">Role</th>
              <th className="py-2 pr-4">Stage</th>
              <th className="py-2 pr-4">Applied</th>
              <th className="py-2 pr-4">Pick</th>
              <th className="py-2 pr-4">Blocked</th>
              <th className="py-2 pr-4">Source</th>
              <th className="py-2 pr-4">Filed via</th>
              <th className="py-2 pr-4">Preview / Submit</th>
              <th className="py-2 pr-4">Details</th>
            </tr>
          </thead>
          <tbody>
            {applications.map((app) => {
              const automationStatus = liveStatuses[app.id] ?? app.automation_status;
              return (
              <tr key={app.id} className="border-b border-border">
                <td className="py-2 pr-4 text-fg">{app.company}</td>
                <td className="py-2 pr-4 text-fg-muted">{app.role}</td>
                <td className="py-2 pr-4">
                  <Select
                    value={app.stage}
                    onValueChange={(v) => handleStageChange(app.id, v as JobApplicationStage)}
                  >
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {JOB_APPLICATION_STAGES.map((s) => (
                        <SelectItem key={s} value={s}>
                          {JOB_APPLICATION_STAGE_LABELS[s]}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </td>
                <td className="py-2 pr-4 text-fg-dim">{app.applied_date ?? <Badge>Not yet</Badge>}</td>
                <td className="py-2 pr-4">
                  {app.pick_verdict ? (
                    <Badge variant={pickVerdictVariant(app.pick_verdict)}>{app.pick_verdict}</Badge>
                  ) : (
                    <span className="text-fg-dim">—</span>
                  )}
                </td>
                <td className="py-2 pr-4 text-fg-dim">
                  {automationStatus && automationStatus !== "idle" && (
                    <div className="mb-1">
                      <Badge>{AUTOMATION_STATUS_LABELS[automationStatus]}</Badge>
                    </div>
                  )}
                  {app.apply_blocked_reason ?? "—"}
                </td>
                <td className="py-2 pr-4 text-fg-dim">{app.source ?? "—"}</td>
                <td className="py-2 pr-4 text-fg-dim">{app.source_channel ?? "—"}</td>
                <td className="py-2 pr-4">
                  {app.stage === "ready_to_submit" && app.apply_preview ? (
                    <div className="flex flex-col gap-1">
                      <span className="text-fg-dim text-xs">
                        {Object.entries(app.apply_preview.screening_answers).length} screening answer(s)
                      </span>
                      {submittingIds.has(app.id) ? (
                        <span className="text-fg-dim text-xs flex items-center gap-1">
                          <Loader2 className="size-3 animate-spin" /> Submitting...
                        </span>
                      ) : timedOutIds.has(app.id) ? (
                        <div className="flex flex-col gap-1">
                          <span className="text-amber-400 text-xs">
                            Taking longer than expected -- may still be running
                          </span>
                          <div className="flex gap-1">
                            <button
                              type="button"
                              onClick={() => handleCheckNow(app.id)}
                              className="px-2 py-1 bg-surface-2 text-fg-muted rounded-md text-xs border border-border hover:text-fg w-fit"
                            >
                              Check now
                            </button>
                            <button
                              type="button"
                              onClick={() => handleTryAgain(app.id)}
                              className="px-2 py-1 bg-surface-2 text-fg-muted rounded-md text-xs border border-border hover:text-fg w-fit"
                            >
                              Reset approval
                            </button>
                          </div>
                        </div>
                      ) : automationStatus === "needs_confirmation" ? (
                        // The Submit click may have landed. Never offer Approve or Try again
                        // here -- a retry could file a duplicate real application. Only the
                        // human's answer moves the row on (resolve_confirmation RPC).
                        <div className="flex flex-col gap-1">
                          {(blockedReasons[app.id] ?? app.apply_blocked_reason) && (
                            <span className="text-fg-dim text-xs">
                              {blockedReasons[app.id] ?? app.apply_blocked_reason}
                            </span>
                          )}
                          <span className="text-amber-400 text-xs">
                            Submit may have gone through -- check the employer portal or your
                            inbox, then confirm:
                          </span>
                          <div className="flex gap-1">
                            <button
                              type="button"
                              onClick={() => handleResolveConfirmation(app.id, true)}
                              className="px-2 py-1 bg-surface-2 text-fg-muted rounded-md text-xs border border-border hover:text-fg w-fit"
                            >
                              It went through
                            </button>
                            <button
                              type="button"
                              onClick={() => handleResolveConfirmation(app.id, false)}
                              className="px-2 py-1 bg-surface-2 text-fg-muted rounded-md text-xs border border-border hover:text-fg w-fit"
                            >
                              It didn&apos;t go through
                            </button>
                          </div>
                        </div>
                      ) : automationStatus === "needs_input" ? (
                        <div className="flex flex-col gap-1">
                          <span className="text-amber-400 text-xs">
                            {blockedReasons[app.id] ?? app.apply_blocked_reason ?? "Needs your input"}
                          </span>
                          <button
                            type="button"
                            onClick={() => handleRequeue(app.id)}
                            className="px-2 py-1 bg-surface-2 text-fg-muted rounded-md text-xs border border-border hover:text-fg w-fit"
                          >
                            Re-prepare
                          </button>
                        </div>
                      ) : automationStatus === "ready_for_review" ? (
                        <div className="flex flex-col gap-1">
                          {(blockedReasons[app.id] ?? app.apply_blocked_reason) && (
                            <span className="text-fg-dim text-xs">
                              {blockedReasons[app.id] ?? app.apply_blocked_reason}
                            </span>
                          )}
                          <div className="flex gap-1">
                            <button
                              type="button"
                              onClick={() => setConfirmingApplication(app)}
                              className="px-2 py-1 bg-emerald-600 text-white rounded-md text-xs w-fit"
                            >
                              Approve & Submit
                            </button>
                            <button
                              type="button"
                              onClick={() => handleRequeue(app.id)}
                              className="px-2 py-1 bg-surface-2 text-fg-muted rounded-md text-xs border border-border hover:text-fg w-fit"
                            >
                              Re-prepare
                            </button>
                          </div>
                        </div>
                      ) : automationStatus === "failed_retryable" ? (
                        <div className="flex flex-col gap-1">
                          <span className="text-red-400 text-xs">
                            {blockedReasons[app.id] ?? app.apply_blocked_reason}
                          </span>
                          <button
                            type="button"
                            onClick={() => handleTryAgain(app.id)}
                            className="px-2 py-1 bg-surface-2 text-fg-muted rounded-md text-xs border border-border hover:text-fg w-fit"
                          >
                            Try again
                          </button>
                        </div>
                      ) : automationStatus === "approved" ? (
                        <div className="flex flex-col gap-1">
                          <span className="text-amber-400 text-xs">
                            Approved -- queued for submission
                          </span>
                          <button
                            type="button"
                            onClick={() => handleTryAgain(app.id)}
                            className="px-2 py-1 bg-surface-2 text-fg-muted rounded-md text-xs border border-border hover:text-fg w-fit"
                          >
                            Reset approval
                          </button>
                        </div>
                      ) : automationStatus === "preparing" || automationStatus === "submitting" ? (
                        <span className="text-fg-dim text-xs flex items-center gap-1">
                          <Loader2 className="size-3 animate-spin" /> In progress --{" "}
                          {AUTOMATION_STATUS_LABELS[automationStatus]}
                        </span>
                      ) : (
                        (blockedReasons[app.id] ?? app.apply_blocked_reason) && (
                          <span className="text-fg-dim text-xs">
                            {blockedReasons[app.id] ?? app.apply_blocked_reason}
                          </span>
                        )
                      )}
                    </div>
                  ) : (
                    <span className="text-fg-dim">—</span>
                  )}
                </td>
                <td className="py-2 pr-4">
                  <button
                    type="button"
                    onClick={() => setSelectedApplication(app)}
                    className="px-2 py-1 bg-surface-2 text-fg-muted rounded-md text-xs border border-border hover:text-fg"
                  >
                    View
                  </button>
                </td>
              </tr>
              );
            })}
          </tbody>
        </table>
      )}

      <ApplicationDetailSheet
        application={selectedApplication}
        onClose={() => setSelectedApplication(null)}
        onSaved={handleApplicationSaved}
      />

      <ConfirmModal
        open={confirmingApplication !== null}
        title="Submit this application?"
        body={
          confirmingApplication ? (
            <p>
              This will submit a real application to <strong>{confirmingApplication.company}</strong>{" "}
              for <strong>{confirmingApplication.role}</strong>. This cannot be undone.
            </p>
          ) : null
        }
        confirmLabel="Approve & Submit"
        confirmVariant="primary"
        onConfirm={doApprove}
        onCancel={() => setConfirmingApplication(null)}
        loading={approveLoading}
      />
    </div>
  );
}
