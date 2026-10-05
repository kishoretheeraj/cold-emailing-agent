# Lifecycle, form drift, and receipt reconciliation review

Reviewed follow-up commits through `6b4da48` after PR #11 (already merged at `fa03df7`).
These commits require a new PR; pushing to a merged PR's branch does not deploy them.

## Findings fixed before merge

- **P1 — false receipt confirmation.** The matcher accepted reminders such as “Complete your
  application to Acme” and generic “Thanks for submitting” messages. It also accepted a receipt
  explicitly naming another role when only one application remained pending. Require explicit
  application acknowledgement and the application's role in the subject/body. Receipts without
  a role remain uncertain for manual confirmation.
- **P1 — receipt reuse across monitor runs.** The consumed-message set existed only in memory.
  Once one application left the pending queue, its email could confirm another application on
  the next scan. Add migration `20261004000002`: a unique index on trimmed receipt message IDs
  and a guarded RPC returning false for an already-used receipt. This also covers concurrent
  writers. Rejected evidence leaves the row pending and still permits manual escalation.
- **P1 — new previews could skip form drift protection.** Empty or failed fingerprint extraction
  produced an approvable preview with a null signature, which submit treated as legacy data.
  New previews now fail before filling or saving when the form cannot be fingerprinted.
- **P2 — mailbox failures masqueraded as no receipt.** IMAP SELECT/SEARCH rejection returned an
  empty list, causing an inaccurate “No receipt email” escalation. These failures now propagate
  to the reconciler's error path. Include the two-minute matching slack in the search start date.

## Verification

- Eleven new regression cases failed before their corresponding fixes and now pass.
- Final Python suite: **1,417 passed**. UI unit suite: **778 passed**. TypeScript check passed.
- Applications browser suite: **5 passed**. Existing screenshot changes preserved.
- Live database, rollback-only functional tests: lifecycle RPC transitions and grants;
  authenticated approval-column restrictions; receipt uniqueness and invalid IDs.
- Mutation check: dropping the new unique index makes the receipt dry-run fail with
  “receipt reused across applications”; no fixture rows or schema changes persisted.
- Production before rollout: migrations `20261004000000` and `20261004000001` present;
  478 idle rows, one ready-for-review row, no pending submission or receipt evidence.

## Operational limits

Receipt phrase matching and Gmail authentication still need a real receipt/uncertain-submit
canary. The tests use synthetic mail; this review did not submit an application or send email.
Conservative role matching intentionally leaves generic or differently titled receipts for a
human. Legacy previews with null signatures retain the documented warning-and-skip behavior;
new previews cannot enter that path. The fingerprint describes the initial form fields, not a
complete proof of every dynamic or multi-page form's semantics.

The grants protect direct lifecycle and approval-column writes; the existing anonymous RPC/API
architecture is not a substitute for authenticating the application's owner.

Verdict: merge after applying migration `20261004000002` and confirming the PR checks.
