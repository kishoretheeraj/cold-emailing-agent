"use client";

import { BUCKET_LABELS, bucketCounts } from "@/lib/application-status";
import type { AutomationStatus } from "@/lib/types";

interface ApplicationStatsProps {
  applications: { automation_status: AutomationStatus }[];
}

export function ApplicationStats({ applications }: ApplicationStatsProps) {
  const counts = bucketCounts(applications);

  return (
    <div className="flex gap-4 text-sm" aria-label="Application counts">
      <span className="text-amber-300">
        <span className="font-semibold">{counts.action}</span>{" "}
        <span className="text-fg-muted">{BUCKET_LABELS.action}</span>
      </span>
      <span className="text-indigo-300">
        <span className="font-semibold">{counts.progress}</span>{" "}
        <span className="text-fg-muted">{BUCKET_LABELS.progress}</span>
      </span>
      <span className="text-emerald-300">
        <span className="font-semibold">{counts.done}</span>{" "}
        <span className="text-fg-muted">{BUCKET_LABELS.done}</span>
      </span>
    </div>
  );
}
