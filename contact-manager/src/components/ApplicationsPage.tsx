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
import { pickVerdictVariant } from "@/lib/applicationBadges";
import {
  JOB_APPLICATION_STAGES,
  JOB_APPLICATION_STAGE_LABELS,
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
  // Keyed the same way as submittingIds: id -> the row's last-known apply_blocked_reason once
  // polling observes one, so the UI can show a specific failure and a Try again action instead
  // of leaving the row looking stuck until the 90s timeout.
  const [blockedReasons, setBlockedReasons] = useState<Record<string, string>>({});
  const pollTimers = useRef<Record<string, ReturnType<typeof setTimeout>>>({});

  useEffect(() => {
    try {
      sessionStorage.setItem(SUBMITTING_IDS_STORAGE_KEY, JSON.stringify([...submittingIds]));
    } catch {
      // sessionStorage unavailable
    }
  }, [submittingIds]);

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

  const POLL_INTERVAL_MS = 5000;
  const POLL_TIMEOUT_MS = 90000;

  const stopPolling = (id: string) => {
    setSubmittingIds((cur) => {
      const next = new Set(cur);
      next.delete(id);
      return next;
    });
    delete pollTimers.current[id];
  };

  const pollForCompletion = (id: string) => {
    const startedAt = Date.now();
    const tick = async () => {
      if (Date.now() - startedAt > POLL_TIMEOUT_MS) {
        stopPolling(id);
        toast.error("Still processing -- check back in a bit");
        return;
      }
      try {
        const res = await fetch(`/api/applications/${id}`);
        const data = await res.json();
        const app: { stage?: string; apply_blocked_reason?: string | null } | undefined =
          data.application;
        if (app?.stage === "applied") {
          stopPolling(id);
          toast.success("Application submitted");
          load(stageFilter, sourceFilter);
          return;
        }
        if (app?.apply_blocked_reason) {
          stopPolling(id);
          setBlockedReasons((cur) => ({ ...cur, [id]: app.apply_blocked_reason as string }));
          toast.error("Submission failed -- see the row for details");
          return;
        }
      } catch {
        // best-effort poll; retry on the next tick
      }
      pollTimers.current[id] = setTimeout(tick, POLL_INTERVAL_MS);
    };
    pollTimers.current[id] = setTimeout(tick, POLL_INTERVAL_MS);
  };

  const doApprove = async () => {
    if (!confirmingApplication) return;
    const app = confirmingApplication;
    setApproveLoading(true);
    try {
      const res = await fetch(`/api/applications/${app.id}/submit`, { method: "POST" });
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
      load(stageFilter, sourceFilter);
    } catch {
      toast.error("Could not reset -- try again in a moment");
    }
  };

  return (
    <div className="p-6 flex flex-col gap-6">
      <h1 className="text-lg font-medium text-fg">Applications</h1>

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
            {applications.map((app) => (
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
                      ) : blockedReasons[app.id] ? (
                        <div className="flex flex-col gap-1">
                          <span className="text-red-400 text-xs">{blockedReasons[app.id]}</span>
                          <button
                            type="button"
                            onClick={() => handleTryAgain(app.id)}
                            className="px-2 py-1 bg-surface-2 text-fg-muted rounded-md text-xs border border-border hover:text-fg w-fit"
                          >
                            Try again
                          </button>
                        </div>
                      ) : (
                        <button
                          type="button"
                          onClick={() => setConfirmingApplication(app)}
                          className="px-2 py-1 bg-emerald-600 text-white rounded-md text-xs w-fit"
                        >
                          Approve & Submit
                        </button>
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
            ))}
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
