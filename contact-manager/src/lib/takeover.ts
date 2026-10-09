import type { JobApplication, TakeoverRequest } from "@/lib/types";

/** The row's takeover request if a worker is waiting on a human right now, else null. */
export function openTakeover(
  app: Pick<JobApplication, "takeover" | "worker_lease_id">
): TakeoverRequest | null {
  const t = app.takeover;
  if (!t || !app.worker_lease_id || t.lease !== app.worker_lease_id || t.continue_at) return null;
  return t;
}
