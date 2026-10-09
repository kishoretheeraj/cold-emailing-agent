# Every site: one filler for every application page (2026-10-09)

Status: revised after advisor review (2026-10-09, verdict "approve with changes"); §7 maps every finding. Plan: docs/superpowers/plans/2026-10-09-every-site.md.

## 1. Problem

The worker fills four platforms: Greenhouse, Ashby and Lever (hand-mapped fillers) and Workday
(Beelink only). Every other host routes to `browser-use`, an LLM browser agent that needs a paid API
key. The operator runs everything on the Claude subscription, so the Beelink sets
`APPLY_GENERIC_ADAPTER=none` and those rows are skipped for good.

Measured on 2026-10-09 against SimplifyJobs' listings.json (3,100 active postings, all categories;
120 in Product):

| Platform | All | Product | Filled today |
|---|---|---|---|
| Workday | 28.7% | 30.8% | yes (Beelink) |
| Greenhouse / Ashby / Lever | 27.8% | 28.4% | yes |
| Oracle Cloud (Candidate Experience) | 5.1% | 10.0% | no |
| SmartRecruiters / iCIMS / Workable | 11.8% | 7.5% | no |
| Company's own page | 26.6% | 25.8% | no |

The "own page" bucket is led by TikTok/ByteDance (310 of 824), Apple, JHU APL, JazzHR
(applytojob.com), Eightfold, Tesla, L3Harris, Rippling, Garmin, Amazon, BambooHR, Deloitte, Taleo.
About 41% of postings, and 41% of Product postings, never get a preview.

LinkedIn rows (`cu_linkedin.py`) are a second gap: the stored `job_url` is the LinkedIn posting, an
aggregator, so they are marked `unsupported` even when LinkedIn only links out to the employer's
own form.

## 2. Goal

Every posting whose application lives on a real form gets a preview, on the subscription, with the
same gates as today: preview stops before Submit; Submit happens only after the operator's signed,
hash-bound approval, on the armed unit. Where the worker cannot finish (CAPTCHA, an account it
cannot open, a question it cannot answer), the row lands in "Needs you" with a precise reason, not
in a silent skip.

Non-goals (unchanged rules): no CAPTCHA solving, no stealth or proxies, no password resets, no
second accounts, no LinkedIn Easy Apply (it acts inside the operator's LinkedIn account; those rows
go to the apply-by-hand list), no paid API on the apply path.

## 3. Design

### 3.1 The universal filler (`universal_filler.py`, new; replaces browser-use)

Deterministic, inventory-driven, no model in the loop for navigation. It reuses what the apply
path already has: `_form_inventory` (every visible control with label, kind, required, options),
`_fill_field` (input, select, radio, checkbox, combobox, listbox), the eligibility and screening
passes (screening answers come from `claude -p`), file attachment, the required-field gate.

New pieces:

1. **`contact_key(field)`**, pure. Maps one inventory field to a contact key from its label,
   `name`, `id`, `autocomplete` and `type`: `first_name`, `last_name`, `name`, `email`, `phone`,
   `linkedin`, `location`, `city`, `website`, `github`, or None. Word-boundary rules with exclusions
   ("Referrer's name", "Emergency contact phone", "Hiring manager email" are not the candidate's).
   `autocomplete` wins when present (`given-name`, `family-name`, `email`, `tel`, `url`).
2. **`fill_contact(page, inventory, values)`** fills only empty contact fields and returns
   `{key: True|False}` for the keys it found, the same report shape `ats_fillers` returns, so
   `_missing_required` gates universal pages too.
3. **`page_state(page)`** → one of `form`, `posting`, `auth`, `email_code`, `closed`, `unknown`.
   `form`: at least one visible fillable field that is not a site search box (role=search,
   `type=search`, a lone keyword field in a header). `auth`: a visible password field.
   `email_code`: a visible one-time-code field (`autocomplete=one-time-code`, a 4-8 digit
   `inputmode=numeric` field, or a label reading verification/one-time/PIN code). `posting`: an
   entry control (below) and no form.
4. **`entry_control(page)`**: the one visible control reading Apply, Apply now, Apply for this
   job/position/role, I'm interested, Start application. Never "Apply with LinkedIn/Indeed/Google/
   SEEK" (they hand our identity to a third party) and never a control whose label also reads
   Submit/Send. Following it may open a new tab; the walker follows the newest page.
5. **`forward_control(page)`**: classifies every visible, enabled button (and `input[type=submit]`)
   on the current step:
   - `next`: exactly `next`, `continue`, `save and continue`, `save & continue`, `proceed`,
     `next step`, `review`, `review application`;
   - `submit`: submit, send, apply, finish, complete, done (as words), or `input[type=submit]`
     whose value reads that way;
   - anything else is ignored.
   Returns `("next", control)` when exactly one `next` exists, `("final", None)` when none exists
   and exactly one `submit` does, else `("stuck", reason)`.
6. **`walk(page, fill_step, max_steps=12)`**: on each step, `fill_step()` fills contact fields,
   files, eligibility and screening answers; the required-field gate runs; then `forward_control`:
   `next` is pressed and the walker waits until the step's form signature changes (an unchanged
   signature after 10 s with a visible error reads as "the site refused this step", reported with
   the error text); `final` ends the walk; `stuck` stops with the reason. The walker never clicks
   a `submit`-class control. Returns the step labels (headings), answers, and any missing
   questions, the same contract as `_walk_workday`.
7. **Submit** (`submit()` only, armed): the walk is replayed with the reviewed answers
   (`replay=`), then `final_submit_control(page)` must find exactly one visible, enabled
   submit-class button on the final step (the same rule as `_resolve_submit_control`, widened from
   "Submit application" to the submit class). Everything after the click is the existing tail:
   confirmation polling, emailed codes, challenge handover, evidence, `needs_confirmation` on any
   doubt.

### 3.2 Accounts and sign-in

- **Saved sessions first.** `_launch_for` opens every non-hand-mapped page with the tenant's saved
  `storage_state` (`ats_sessions.tenant_key`, already per host + company slug), as Workday does.
- **Email-code sign-in** (Oracle Candidate Experience asks for an email, then a one-time code;
  other sites do the same): on `email_code` after we typed the operator's email and pressed a
  `next`-class control, `email_verification.wait_for_verification` reads the receipt inbox for
  mail from `UNIVERSAL_CODE_SENDERS` (config: oraclecloud.com, icims.com, smartrecruiters.com,
  workable.com, the posting's own domain) sent after the press, types the code, presses `next`.
  No code within 120 s → takeover.
- **Password sign-in or signup** (`auth`): never automated on universal sites in this phase.
  A takeover opens ("Sign in or create the account for <site>, then press I'm done"); after the
  human continues, the session is saved with `ats_sessions.save_state`, so every later application
  on that site goes straight in. One sign-in per site, ever, unless the site expires it. The vault
  and its rules stay Workday-only until a site's flow is verified live.

### 3.3 LinkedIn rows go to the employer's form

`cu_linkedin.py` already reads each posting in a real browser. Its reply schema gains
`apply_type` (`easy_apply` | `external` | `unknown`) and `external_url` (the employer link behind
LinkedIn's Apply button, when the model can read it). `persist_postings`:
- `external` with an http(s) `external_url` on a non-aggregator host → saved with that URL as
  `job_url` (dedup by its job_key), the LinkedIn URL kept in `posting_snapshot.linkedin_url`;
- `easy_apply` → saved as today and marked `unsupported` with "LinkedIn Easy Apply: apply by
  hand" (the apply-by-hand list picks these up);
- `unknown` → saved as today.
No new LinkedIn actions: the model reads the link while it is already on the posting.

### 3.4 Navigation help on the subscription (phase 2, behind `UNIVERSAL_NAV_ASSIST`)

When the deterministic rules return `stuck` or find no entry control, `claude -p` (stripped
context, as `claude_subscription.complete`) may be shown the page title, the visible field labels
and a numbered list of visible buttons/links (labels only, truncated, never values) and asked for
one number. The answer is validated: it must be in range and its label must not be submit-class or
a third-party sign-in. At most 3 assists per application, each logged. Page text is untrusted;
the model can only pick one of our own numbered, non-submit options, so injection can at worst
press a harmless button, which the walker then re-reads.

### 3.5 Site adapters only where recon says so

Per-site code is added only for a site whose live structure the universal filler cannot handle,
decided from a recon report, not guessed. `scripts/form_recon.py` (read-only, runs on the Beelink
or any host with open egress): for given posting URLs it presses only the entry control and
records the page states, steps, field labels/kinds/required flags and forward controls, never
values. This cloud session's egress policy blocks job sites, so the first recon runs on the
Beelink.

### 3.6 Wiring

- `config.APPLY_GENERIC_ADAPTER` gains `universal` (default; `browser_use` stays selectable;
  `none` still skips). The Beelink units set `universal`.
- `ats_platform.unpreparable_platforms()` excludes generic platforms only for `none`.
- `_process_one_preview` / `submit()`: generic platforms go through `_prepare_universal` /
  the universal replay, mirroring the Workday branch. `preview["platform"]` stays the classified
  platform; `preview["universal_steps"]` lists step labels for the review card.
- The aggregator branch is unchanged (LinkedIn rows that still point at LinkedIn stay excluded).

## 4. Safety

- The walker's only clicks are an entry control, `next`-class controls, a listbox/combobox option
  while filling, and (in `submit()` only, armed, after the signed approval and lease renewal) the
  single final submit-class control. Tests assert a page whose only forward button reads "Submit"
  is never clicked in preview, and that "Apply with LinkedIn" is never pressed.
- `APPLY_AGENT_ARMED` stays set only in the two existing places.
- Required-field gate, form-drift signature, quality gate, knock-outs, posting-closed check and
  the `clicked` boundary apply unchanged.
- No passwords typed by the universal filler. Sessions saved per tenant, 0600 files.

## 5. Tests

Local fixture pages in `tests/fixtures/universal/`, each modelled on a real platform's structure
as recorded by recon (until recon runs, on public documentation of the platform's form and marked
"unverified"): single-page form, multi-step wizard with Next/Review, a posting page with an Apply
entry that opens a new tab, an email-code gate, a password wall, a page whose only button is
Submit, a page offering "Apply with LinkedIn", a form inside an iframe. Real Chromium, as the
Workday tests do. `contact_key` gets a table of real label strings. The load test gains universal
rows.

## 6. Rollout

1. Recon on the Beelink for the sampled URLs of every bucket above.
2. Ship the universal filler with `universal` default; watched preview runs per platform (RUNBOOK
   section 10 style), then submit runs.
3. Add nav assist and per-site adapters for what the watched runs show is stuck.

Expected coverage after step 2: every posting reaches a preview attempt; the share that reaches
`ready_for_review` without help is measured in the watched runs, not promised here.

## 7. Advisor review (2026-10-09) and what changed

Verdict: approve with changes. Blockers 1-5 are resolved in the design below before any code;
phase 1 ships the **one-page form** path only, and multi-step walking waits for recon.

| # | Finding | Resolution |
|---|---|---|
| 1 | A `next` press can be a real submit (single-page "Continue", final-step "Next", "Review") | Phase 1 presses no forward control at all. A page offering a `next`-class control is a multi-step form and stops with needs_input ("multi-step form on <host>; not supported yet"). Multi-step (phase 2) presses Next only when a visible step indicator shows a later step, or a per-platform rule proven by recon. Preview and the submit pass run with a **submit guard** init script: a capture-phase `submit` listener that prevents default and counts, and `HTMLFormElement.prototype.submit/requestSubmit` overridden to count instead of sending. A blocked submit releases needs_input. The submit pass lifts the guard only right before the one approved click. Fixture server counts POSTs; tests assert 0 for every preview and every unarmed submit. |
| 2 | Enter on a combobox can submit | The universal path never presses Enter: a combobox is answered by clicking a visible `role=option` that `_pick_option` matches, or not at all. The guard catches the rest. |
| 3 | `entry_control` on a review page = submit | The entry control is pressed at most once, on the landing page, before anything is filled; after that a page without a form is stuck. |
| 4 | A press that sent the application, then failed_retryable, is retried (double apply) | Phase 1 has no forward presses, so in `submit()` the only press is the approved final click, after the ARMED check and `renew_submission_lease` (`clicked=True` as today). After filling, preview reads `_submission_state`; a confirmation-looking page releases `unsupported` with "The site may have received this application during preview; check before applying again" (terminal, not requeueable). Phase 2 multi-step: ARMED + lease renewal before the first forward press, which becomes the `clicked` boundary. |
| 5 | Data sent to a host we never chose | Fill only when the form's host is the job_url's registrable domain (or a subdomain) or a host `job_identity` recognises as an ATS. Anything else stops before typing. LinkedIn `external_url` (§3.3) is removed from this spec: its own spec after recon, and it would accept only known-ATS URLs. |
| 6 | Wrong fields filled | Contact fields are filled only inside the target form (the `<form>` that holds the final submit control or the resume input; the whole page when neither is in a form) and only on positive label rules; a key matching more than one field fills none (an "email" + "confirm email" pair is the one allowed pair). The label-to-key map is stored in `fill_report.contact_fields`. File inputs are chosen by inventory label (resume/CV; cover letter); a single unlabelled file input counts as the resume only when its label does not name something else (transcript, photo, writing sample, portfolio). |
| 7 | Email code false positives / wrong code | The email-code sign-in is deferred with multi-step. Phase 1 reads `email_code` (prompt text plus a code field, as `_email_code_requested`) as a stop: takeover on the Beelink, else needs_input. |
| 8 | Session files carry Google cookies; keyed by the wrong host | `ats_sessions.save_state` gains a domain filter: only cookies and origins of the form host's registrable domain are written. Sessions are keyed by the form page's host, not job_url's. |
| 9 | Drift signature on a posting page; step change by signature only | The signature is taken on the form page (after entry). The final control's label is stored in the preview and must match at submit. |
| 10 | Cross-step required gate | Phase 2 (multi-step). Phase 1 is one page. |
| 11 | iframes | When the main page has no application fields and exactly one child frame on an allowed host does, that frame is the form (`FrameView`: frame methods, page keyboard/mouse). Otherwise stuck. |
| 12 | Rollout too broad, public logs | `APPLY_UNIVERSAL_ENABLED=1` only in the Beelink apply units (test_ops_workflows fails if a workflow sets it), plus `APPLY_UNIVERSAL_PLATFORMS` allow list (job_identity platforms) that grows as recon/watched runs prove each one. `unpreparable_platforms()` keeps excluding everything else, so the resume worker builds no documents for unproven platforms. |
| 13 | Long walks hold the display | Phase 1 is one page; phase 2 heartbeats per step, a per-preview deadline, and yields to approved submits. |
| 14 | Nav assist, LinkedIn links overbuilt; submit words; next+submit ambiguity | Nav assist (§3.4) and §3.3 move to later specs. Submit-class stays broad (it only ever stops presses); the final control must be exactly one *strict* submit label (submit / submit application / send application / apply / apply now) in the target form, and a page with both a `next`-class and a submit-class control is ambiguous (stop). |
| 15 | Missing tests | Fixtures: POST-counting server, Enter submit, single page whose submit reads "Continue", review page with "Apply" and no fields, Apply opening another site, referrer and job-alert email fields, ZIP field, Google cookies in a saved session, unarmed submit presses nothing. Fixtures built without recon are marked unverified and are not safety evidence for a live site. |

### Phase 1 flow (what ships now)

1. Launch with the tenant's saved session (keyed by host) and the submit guard.
2. Landing: `posting` → press the entry control once (follow a new tab); `auth` / `email_code` →
   takeover on the Beelink, else needs_input; the form may be in one allowed iframe.
3. Host check (§7 #5). Signature on the form page.
4. Fill: contact fields (scoped, unambiguous), files by label, eligibility, screening (no Enter).
5. Stop conditions, each a precise reason: a `next`-class control present (multi-step), no single
   strict final control, guard counted a submit, required questions blank, confirmation visible.
6. `complete_preview` with `universal: {host, final_label, contact_fields}`.
7. `submit()`: same launch and fill from the reviewed answers (no generation), same checks, the
   final label must match, then ARMED, lease renewal, lift the guard, one click, existing tail.
