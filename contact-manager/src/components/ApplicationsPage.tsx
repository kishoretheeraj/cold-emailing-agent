"use client";

import { useEffect, useState, type FormEvent } from "react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/Badge";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/Select";
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

  const handleApprove = async (id: string) => {
    try {
      const res = await fetch(`/api/applications/${id}/submit`, { method: "POST" });
      if (!res.ok) throw new Error("request failed");
      toast.success("Submission triggered -- check back shortly");
    } catch {
      toast.error("Could not trigger submission");
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
                      <button
                        type="button"
                        onClick={() => handleApprove(app.id)}
                        className="px-2 py-1 bg-emerald-600 text-white rounded-md text-xs w-fit"
                      >
                        Approve & Submit
                      </button>
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
    </div>
  );
}
