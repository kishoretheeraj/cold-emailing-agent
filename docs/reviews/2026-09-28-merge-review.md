Merge review — 2026-09-28

**Decision: changes requested. No branches merged or pushed.**

Reviewed the pending Applications UI M2 branch at `98fa158`, draft [PR #6](https://github.com/kishoretheeraj/cold-emailing-agent/pull/6) at `c49b3a1`, and the merged PR diffs #1–5 and #7–8 against their current implementations. Remote main is `e3f7fe0`; local main is `66d3a08` (one documentation commit ahead). M2 has five local commits beyond its remote branch and no open PR. Findings below distinguish new M2/PR #6 defects from problems already on main. This is a code and local-test review; no production submissions, migrations, or live automation runs were performed.

1. **[P1, M2] Approval does not freeze the answers that will be submitted.**

   Location: [application PATCH route](/Users/kishoretheeraj/Documents/cold-email-agent/.claude/worktrees/beelink-m2-applications-ui/contact-manager/src/app/api/applications/[id]/route.ts:98); [answer editor](/Users/kishoretheeraj/Documents/cold-email-agent/.claude/worktrees/beelink-m2-applications-ui/contact-manager/src/components/ApplicationDetailSheet.tsx:135).

   After Approve & Submit sets `approved_at`, View and Save changes remain usable. PATCH updates `apply_preview` using only the row ID, with no check on `approved_at` or stage. The workflow can spend minutes installing dependencies before `submit()` reads the row. An edit during that interval changes the screening/eligibility answers sent under the earlier approval; edits after the worker reads the row instead make the UI show answers different from those sent. The timestamp therefore does not certify a particular preview. Reject preview edits atomically while approval is active, and bind approval to a preview version/snapshot that the worker consumes. Cover concurrent save/approve and edits after dispatch.

2. **[P1, already on main, apply-agent changes carried through PR #8] The generic browser filler is incompatible with an allowed installed dependency.**

   Location: [browser-use adapter](/Users/kishoretheeraj/Documents/cold-email-agent/apply_agent.py:267) and [dependency specification](/Users/kishoretheeraj/Documents/cold-email-agent/requirements-apply.txt:7).

   The installed `browser-use==0.1.40` satisfies `browser-use>=0.1.0`, but has no `browser_use.llm` module, no `page` constructor argument, and no `Agent.run_sync()`. Its agent exposes async `run()`. The adapter cannot run against that supported installation. `_fill_generic_via_browser_use()` catches the failure and returns normally, so the preview path still marks the application ready even though its generic fill failed. Pin and integrate a tested version, connect it to the intended browser/page, and propagate fill failure to a blocked preview. Add an adapter smoke test that does not mock the entire adapter. This finding is verified against the local dependency, not a claim that every available browser-use release has the same API.

3. **[P1, already on main, apply-agent/job-pick changes carried through PR #8] Candidate-grounded prompts omit the candidate's actual experience.**

   Location: [screening prompt construction](/Users/kishoretheeraj/Documents/cold-email-agent/apply_agent.py:78); [job-fit judge](/Users/kishoretheeraj/Documents/cold-email-agent/job_pick.py:105).

   Screening answers receive `job["role"]` as `profile_summary`: a posting titled Senior Product Manager is presented as the candidate's facts. No resume/profile accomplishments reach the model, so it cannot produce the promised factual experience answers. The job-fit judge likewise asks for fit against a real candidate but supplies only the employer, role, and job description; `_profile_text()` is used for embeddings and never included in this final decision. A strong verdict automatically spends money on resume generation. Pass the real, approved profile/resume facts to both prompts and keep the target role separate. If candidate evidence is missing, require review rather than presenting unsupported experience answers or a personalized fit verdict.

4. **[P2, M2] Refreshing during submission permanently stops status polling.**

   Location: [restored submission IDs](/Users/kishoretheeraj/Documents/cold-email-agent/.claude/worktrees/beelink-m2-applications-ui/contact-manager/src/components/ApplicationsPage.tsx:42) and [polling setup](/Users/kishoretheeraj/Documents/cold-email-agent/.claude/worktrees/beelink-m2-applications-ui/contact-manager/src/components/ApplicationsPage.tsx:167).

   `submittingIds` reloads from sessionStorage, but only `doApprove()` starts polling. Mount never restarts timers for restored IDs. The row renders Submitting indefinitely without observing success/failure or timing out. Reproduced with a temporary regression test: preload `["5"]`, render, advance fake time by 30 seconds; only `/api/system-health` and `/api/applications` are fetched, never `/api/applications/5`. Resume polling on mount and reconcile persisted IDs with server state; test an actual unmount/remount, not only the sessionStorage write.

5. **[P2, M2] A workflow failure before `submit()` starts leaves the row unrecoverably approved in the UI.**

   Location: [poll timeout](/Users/kishoretheeraj/Documents/cold-email-agent/.claude/worktrees/beelink-m2-applications-ui/contact-manager/src/components/ApplicationsPage.tsx:170); [approval guard](/Users/kishoretheeraj/Documents/cold-email-agent/.claude/worktrees/beelink-m2-applications-ui/supabase/migrations/20260925000000_add_approved_at_and_approve_application_rpc.sql:72); [workflow dependency installation](/Users/kishoretheeraj/Documents/cold-email-agent/.github/workflows/apply_agent_submit.yml:31).

   A successful dispatch sets approval before checkout/dependency/browser installation. If one of those steps fails, or the job is canceled/killed, Python never writes `apply_blocked_reason`. At timeout the UI removes the spinner and offers Approve again, but the RPC rejects the still-approved row. Try again is shown only when a blocked reason exists. Track the workflow outcome and expose a recovery path for confirmed terminal failures; do not blindly reset on a timeout while a run may still be active.

6. **[P2, M2] Approval reset and blocked-reason cleanup are not atomic, and the advertised retry cannot recover.**

   Location: [reset-approval route](/Users/kishoretheeraj/Documents/cold-email-agent/.claude/worktrees/beelink-m2-applications-ui/contact-manager/src/app/api/applications/[id]/reset-approval/route.ts:18).

   The RPC first clears `approved_at`; a separate update clears `apply_blocked_reason`. If the second update fails, the route logs it and returns 200. The UI reloads a blocked row and still shows Try again. Its next RPC fails because `reset_approval` requires `approved_at IS NOT NULL`, so execution never reaches the cleanup update. Contrary to the comment, a second click does not retry the clear. Clear both fields in one guarded transaction/RPC, or implement explicitly idempotent recovery. Add a first-success/second-write-failure/retry test with persistent row state.

7. **[P2, PR #6] The email syntax gate permanently rejects valid addresses.**

   Location: [email_verify.py, line 13](https://github.com/kishoretheeraj/cold-emailing-agent/blob/c49b3a136e0212b8a5c035de6a32c913e57bd461/email_verify.py#L13) on PR #6.

   With working DNS mocked, `verify("o'connor@example.com")` and `verify("_team@example.com")` both return invalid before DNS. Apostrophes and underscores are permitted in unquoted local parts; the expression also accepts an invalid consecutive-dot local part such as `alice..smith@example.com`. Because invalid results skip first-touch preparation on every run, valid contacts can silently never receive a draft. Use a standards-aware validator or conservatively return unknown for syntax the limited validator cannot handle. Cover valid punctuation, dot placement, and ordinary addresses. Reference: [RFC 5322, sections 3.2.3 and 3.4.1](https://www.rfc-editor.org/rfc/rfc5322.html#section-3.2.3).

8. **[P2, PR #6] A null MX is incorrectly treated as a valid mail route.**

   Location: [email_verify.py, line 33](https://github.com/kishoretheeraj/cold-emailing-agent/blob/c49b3a136e0212b8a5c035de6a32c913e57bd461/email_verify.py#L33) on PR #6.

   `_check_domain()` ignores the MX answer and returns valid whenever resolution succeeds. Reproduced with a real dnspython MX record parsed from `0 .`: verification returns valid. A sole null MX explicitly says the domain accepts no mail, so this defeats the bounce-prevention gate for conclusively undeliverable domains. Inspect the records, return invalid for a null MX, and do not fall back to A/AAAA for it. Add a null-MX test using a real record object. Reference: [RFC 7505, section 3](https://www.rfc-editor.org/rfc/rfc7505.html#section-3).

PR #6 also has merge conflicts in `agent.py` and `docs/superpowers/specs/2026-08-26-full-fledged-job-platform-buildout.md`, confirmed with `git merge-tree`. Resolve against the parallel preparation changes on main before rerunning integration tests.

Validation:

- M2 frontend: 47 test files, 713 tests passed.
- M2 TypeScript: `tsc --noEmit` passed.
- Targeted Python apply/LinkedIn/systemd/application-DB tests: 128 passed.
- PR #6 isolated email-verification unit tests: 20 passed; the additional DNS/syntax probes above expose uncovered defects.
- Independent refresh regression: failed as expected, confirming finding 4. The temporary test was removed after reproducing it.
- Full M2 Python suite: 1,207 passed in 434.76 seconds.
- No additional actionable regression identified in the inspected merged diffs #1–5 and #7. Passing tests do not establish that all production integrations work.

Existing screenshot edits in the main checkout were preserved. No implementation fixes, external review comments, approvals, database changes, or merge actions were made.
