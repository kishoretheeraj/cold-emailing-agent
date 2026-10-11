import type { AutomationStatus } from "@/lib/types";

export type DisplayBucket = "action" | "progress" | "done";

/** User-facing label for each display bucket. */
export const BUCKET_LABELS: Record<DisplayBucket, string> = {
  action: "Action Needed",
  progress: "In Progress",
  done: "Done",
};

/**
 * Collapse the eleven-value execution status into the three buckets the
 * dashboard shows: things waiting on the user, things the system is working
 * on, and things that are finished.
 */
export function toBucket(status: AutomationStatus): DisplayBucket {
  switch (status) {
    case "ready_for_review":
    case "needs_input":
    case "needs_confirmation":
    case "failed_retryable":
    case "failed_terminal":
    case "unsupported":
      return "action";
    case "idle":
    case "preparing":
    case "approved":
    case "submitting":
      return "progress";
    case "submitted":
      return "done";
  }
}

/** Count applications per display bucket; every bucket is always present. */
export function bucketCounts(
  apps: { automation_status: AutomationStatus }[],
): Record<DisplayBucket, number> {
  const counts: Record<DisplayBucket, number> = {
    action: 0,
    progress: 0,
    done: 0,
  };
  for (const app of apps) {
    counts[toBucket(app.automation_status)] += 1;
  }
  return counts;
}
