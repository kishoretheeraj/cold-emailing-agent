# Independent review: resume subscription transport

Reviewed on 2026-10-04 against the working copy of
`docs/superpowers/specs/2026-10-04-resume-subscription-transport-design.md`, including its updated
font, XMP, and attribution sections. Cross-checked the existing Python pipeline, systemd units,
Claude Code 2.1.289 help, and official Anthropic documentation. No paid generation was run.

**Verdict: request changes before implementation.** The subscription transport is viable, but
the current design does not yet establish its cost or queue-reliability guarantees.

## Findings

### 1. P1 — subscription authentication does not establish zero additional charges

Spec lines 88–89 and 137–141 always record subscription calls at zero cost and lines 99–100
assume quota exhaustion will stop the worker. With usage credits enabled, Claude Code can consume
paid usage beyond the subscription allowance. OAuth authentication alone cannot establish that
no marginal charge occurred. The ledger could report zero while paid credits are consumed.

Require usage credits/paid continuation to be disabled for a subscription-only worker, document
how this prerequisite is verified, and stop if billing eligibility is uncertain. Distinguish the
billing route from measured/verified incremental cost; the CLI's notional API-equivalent cost is
not a bill either. Do not silently fall back to the API.

Evidence: Anthropic documents both the current
[subscription allowance for headless/SDK calls](https://support.claude.com/en/articles/15036540-use-the-claude-agent-sdk-with-your-claude-plan)
and separately charged
[usage credits for Claude Code](https://support.claude.com/en/articles/12429409-manage-usage-credits-for-paid-claude-plans).

### 2. P1 — removing only ANTHROPIC_API_KEY does not isolate the billing route

Spec lines 74–76 inherit every other environment variable. If the caller has
`ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_BASE_URL`, or a cloud-provider selection flag configured,
the child can use a different credential/endpoint/provider or fail instead of using the intended
subscription. Fresh configuration directories do not remove environment overrides.

Build an explicit child-environment allowlist with the intended OAuth token, essential OS
variables, and deliberately supported network settings. Exclude alternate credentials, provider
selectors, endpoint overrides and unrelated application secrets. Test each conflicting variable
and validate the effective authentication route before draining rows.

Evidence: the official [environment variable reference](https://code.claude.com/docs/en/env-vars)
documents the alternate authorization header, endpoint overrides, and provider selection.

### 3. P1 — deployment can turn off generation before its replacement is running

Spec lines 86–87 make subscription the default; lines 128–130 then stop inline generation in
GitHub Actions. Lines 150–151 intentionally leave the replacement timer disabled until a watched
run. Deploying the code before the Beelink/token/fonts are ready therefore queues strong jobs
without a working consumer. There is no specified queue-age alert or activation gate.

Keep the existing API behavior during a staged rollout. Provision and validate the isolated
worker first, run a canary with an explicit backend override, then switch the producer and enable
the consumer in a documented sequence. Add queue-age/last-success monitoring and a rollback that
also drains already-queued rows; merely changing job_pick back only handles newly scored jobs.

### 4. P2 — transient or machine-wide failures permanently exclude individual jobs

Spec lines 96–102 and 163–164 write `resume_error` for every non-quota exception, and the query
excludes those rows forever until manually cleared. A missing/expired token or service outage
can mark an entire batch as bad. Fixing the machine or waiting for recovery does not resume it.

Run configuration/authentication/font preflight once before selecting jobs. Stop the batch for
machine-wide failures without poisoning rows. Give transient network/server/timeout errors a
bounded retry count and next-attempt timestamp. Reserve terminal per-row errors for invalid
content or exhausted retries, with an explicit retry operation.

### 5. P2 — the new consumer has neither eligibility guards nor a claim protocol

Spec lines 96–102 select every strong row missing a resume regardless of stage, deadline,
automation state, or a missing cover letter alone. Skipping propose when a strategy exists also
skips its current deadline check; build has no equivalent check. The design has no atomic claim
shared with manual propose/build entry points. A timer does not overlap itself, but a separate
manual invocation or another host can race it. Current builds use fixed local filenames and
upsert the same storage paths, so overlapping runs can mix or overwrite outputs.

Define eligible stages, require either needed artifact to be absent, check current deadlines,
and exclude cancelled/completed/submitting rows. Add a claim with expiry and guarded completion
used by every writer, plus per-attempt temporary paths. Publish both artifact references together
only if the input revision and claim still match. Test concurrent workers and cancellation/edit
during a build.

### 6. P2 — the claimed fully curated resume still renders unrestricted generated labels

Spec lines 39–41 say Claude only selects curated content. However, the strategy prompt accepts
arbitrary `skills_groups[].label`, `_check_skills_governance` validates only the skill items, and
`resume_build._add_skills_section` renders the labels verbatim. A model-written heading or claim
can therefore appear in the resume even if every skill and bullet is governed.

If the intended invariant is no generated resume prose, select group-label IDs from a curated
list and resolve them in code, validating stored strategies too. Otherwise narrow the stated
invariant to curated bullets/skills; do not claim all rendered text is governed.

## Verification and rollout details to add

- Pin the tested Claude Code version and retain real, redacted success/quota/auth-failure JSON
  fixtures. Strictly validate the final JSON result and error status; do not guess a success
  object by slicing from the first opening brace to the last closing brace in noisy stdout.
- Define timeout cleanup for the whole CLI process group and use an isolated LibreOffice profile
  and output directory per conversion. Verify the expected new PDF exists and can be opened.
- Font tests should cover regular, bold, italic, and bold-italic faces, subset prefixes, missing
  embedding, and nested font resources. A file named Calibri.ttf alone is not a rendering canary.
- Sanitizing only the two new model responses does not validate stored strategies or curated
  source text. If the PDF must contain no unwanted control characters, validate all rendered
  inputs and the extracted text of both final PDFs before upload.
- A functional migration rollback test should verify actual anon/authenticated writes, and the
  live canary should run under the real hardened systemd unit rather than only an interactive shell.

## Confirmed premises and limits

The subscription/SDK change is currently **paused**, not permanently cancelled. The official
support notice supports using the proposed headless transport with the current subscription
allowance. Local CLI help confirms that `--bare` excludes OAuth/keychain authentication.

Anthropic's [watermark article](https://www.anthropic.com/news/claude-text-watermark) supports the
word-choice mechanism and absence of hidden characters, but describes a phased rollout for older
models. It does not establish that every response from this project's configured model is already
watermarked. Accepting possible watermarking in generated cover letters is a reasonable explicit
product decision; metadata/font checks do not prove human authorship or predict ATS treatment.

The reported context-token and font experiments were not reproduced in this review. They should
remain attributed measurements with command/version/output artifacts, rather than broad claims
about every past document or every environment. The existing parallel review's numeric security
ratings and categorical ATS-detection claims are not evidence for approving this design.
