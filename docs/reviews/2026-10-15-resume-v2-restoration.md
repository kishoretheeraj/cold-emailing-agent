# Resume v2 and cover-letter restoration review — 2026-10-15

## Scope and concurrent work

Restore the omitted `resume/v2/` and `cover_letter/` modules from
`overnight-247-restart` at `c8ea3d3b9ec0e93ded0a181148176eb073e5c303` onto main.
All 20 production files are byte-for-byte identical to that source commit, including
`wm_stage.py`, both executable shims, and `cover_letter/wm.py`. Existing
`dewatermark.py` and `resume_rewrite.py` already match the source and need no changes.
The five standalone test files are also restored unchanged.

Claude is independently integrating the 24/7 backend and dashboard on `integrate/247`.
At inspection, it had committed the transactional reply/outbox fix (`219bfd8`) and
the reply-link RPC/schema fix (`d737282`). It also has separate contact-manager
and apply-hardening worktrees. This restoration does not include those changes or
modify those worktrees. The existing profile data on main is retained.

## Review findings and integration fixes

- Main lacked the explicit ReportLab and Pydantic dependencies used by these modules.
  Both are now declared in `requirements.txt`.
- Main lacked the cover-letter toggle and configurable asset deadline. These settings
  are restored with the same behavior as the source branch. Before this fix, the
  restored standalone tests had one failure for the missing cover-letter setting.
- The full watermark-enabled entry point is `resume.v2.build.build_assets`, with
  validation before and after rewriting, bounded repair, isolated temporary outputs,
  a PDF hash manifest, and a six-minute maximum wait. Missing or failed watermark
  commands fail the build. Cover-letter failures return review reasons and no letter PDF.
- The older `tailor_v2.tailor_resume` and `delivery.generate_cover_letter` helpers do
  not run the full watermark stage. The 24/7 apply-worker caller currently uses those
  helpers; `apply_pipeline.prepare_assets` uses `build_assets`. Preserve this distinction
  when enabling services. This PR restores code; it does not enable or deploy a worker.
- Timeout enforcement stops waiting and publication; it does not forcibly cancel
  Python worker threads. The implementation documents this limitation. Model/subprocess
  activity can continue until its own timeout after a stage wait expires.
- Grounding checks are lexical heuristics, not proof of factual entailment. The letter
  validator explicitly exempts paragraphs citing only identity-bank fields from its
  lexical claim-overlap check. Existing source behavior is preserved.
- `delivery.emit_run_digest` deliberately raises `NotImplementedError`; digest delivery
  is not part of the restored asset build.

No blocking defect was found for this additive module restoration. Service rollout and
its choice of entry point remain part of the concurrent integration work.

## Validation

- Composed snapshot based on `integrate/247` at `219bfd8`: 169 passed, 1 skipped across
  the restored modules, apply-worker v2, and prepare-assets import/error mapping tests.
  The skip is the missing-module test because the modules are installed.
- Complete Python suite on main plus this restoration: **4,144 passed, 14 skipped**
  in 734.30 seconds. Skips cover unavailable browser-use/load-test prerequisites and
  the optional local stress stack.
- All 20 restored production files and the two existing watermark dependencies verified
  byte-for-byte against the source branch. No live model requests, deployments, database
  migrations, emails, or job applications were performed during this review.

`tests/test_apply_worker_v2.py` belongs to the 24/7 caller integration and was exercised
in the composed snapshot; it cannot run on main until those worker methods land.
