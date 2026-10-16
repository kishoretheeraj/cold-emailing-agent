// automation_status values GET /api/applications?view=queue returns. Route files may only export
// handlers, so the list lives here for the route and its tests.
export const QUEUE_STATUSES = [
  "ready_for_review", "approved", "submitting", "needs_input", "needs_confirmation", "submitted",
  // The review inbox (useApprovalQueue) shows failed rows as blocked and counts preparing ones.
  "preparing", "failed_retryable", "failed_terminal",
];
