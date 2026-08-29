# Phase 3 — Resume Intelligence — Design

**Status:** Approved for implementation
**Date:** 2026-08-29

Phase 3 of the full-fledged job platform buildout (see
`docs/superpowers/specs/2026-08-26-full-fledged-job-platform-buildout.md`).
That doc stubbed Phase 3 narrow ("fit-score a resume, optionally draft a
cover letter") and separately stubbed Phase 2.5 (auto-apply agent) with a
"resume generation" bullet that said it "likely needs Phase 3 designed
first, or folded into this phase." This design absorbs that bullet: Phase 3
is the full resume + cover letter generation pipeline, human-reviewed, no
auto-submit. Phase 2.5 later only needs to add the actual submit action on
top of documents this phase already produces.

Source material: `RESUME_AGENT_SPEC.md`, a 620-line document the user wrote
themselves, distilled from 30 real Claude-web-app project sessions
(9 May – 17 Aug 2026) manually building tailored resumes/cover letters for
30+ internship applications. Referenced throughout below as "the corpus
spec." Its single strongest conclusion: zero outcome tracking existed across
all 30+ applications, and that gap was more valuable to close than any
further document-quality work.

---

## Rejected, not deferred: AI-detection evasion

**Decision: do not build this.** Recorded here, same posture as the tracer-links
rejection in `docs/superpowers/specs/2026-08-25-engagement-outcome-tracking-design.md`,
so this doesn't quietly resurface as an assumed requirement later.

Mid-design, the user asked to include a third-party "Claude watermark remover"
tool (found via a blog post) so that "no ATS / any human can recognize its AI
generated," and to run it before the resume is generated. Two separate things
were happening in that ask, and only one of them shipped:

1. **File-metadata scrubbing (corpus spec Part 14)** — DOCX `core.xml`/`app.xml`
   and PDF XMP/docinfo fields naming the build tool ("python-docx",
   "LibreOffice") get overwritten, and timestamps are set to realistic,
   non-identical created/modified values. **This shipped** — it's document
   hygiene on the user's own truthful content, touching only incidental file
   properties left behind by whichever tool rendered the file, not the writing
   itself. See "Metadata scrub" below.
2. **Defeating text-based AI-content-detection systems** — the linked tool's
   actual claim was removing a steganographic "watermark" from the generated
   prose itself, so that ATS-integrated or third-party AI-writing detectors
   can't flag the document as AI-authored. **This was declined**, for reasons
   independent of each other:
   - Downloading and running an unverified third-party tool whose stated
     purpose is evading a detection system is not something this project does,
     regardless of the target — same posture as "never download or execute
     files from untrusted sources."
   - There is no publicly known cryptographic/steganographic watermark
     embedded in Claude's text output for such a tool to reliably strip. A
     tool claiming otherwise is most likely either non-functional or is
     silently just doing prose-style editing (em dashes, phrasing cadence)
     under a more dramatic name.
   - The corpus spec's own **Stage 7 humanizer pass** already covers the
     legitimate version of "read naturally, not obviously AI-authored" — kill-list
     phrases, em-dash removal, hedge-closer limits. That's prose polish in the
     user's own voice, honestly described, and it shipped (see "Lint" below).
     What didn't ship is a dedicated component whose job is defeating detection
     systems an employer or ATS may run against a real application — a
     materially different thing from polishing prose or cleaning up a file's
     technical metadata, and one the user should decide on with full context
     rather than have it folded in as an implementation detail.

Net effect: the humanizer lint pass and the metadata scrub both exist in this
design. No AI-content-detector-evasion layer exists, and no third-party tool
was fetched or integrated.

---

## Architecture

New top-level subpackage, following this repo's existing pattern for
self-contained modules (`ats.py`, `visa_matching.py`):

```
resume/
├── data/
│   ├── master.json        # real experience: roles, dates, bullets (source of truth)
│   ├── metrics.json        # corpus spec Part 4 whitelist -- conflicts marked explicitly
│   ├── jargon.json         # corpus spec Part 5 plain-language translation table
│   ├── projects.json       # corpus spec Part 6 project-selection matrix by role type
│   ├── skills.json         # spine + swap pool + banned list (corpus spec Part 8)
│   └── moments.json        # corpus spec Part 9 cover-letter moment bank
├── resume_agent.py         # orchestrator -- CLI entry point, both propose and build modes
├── resume_lint.py          # humanizer, jargon scan, metrics whitelist, cover-letter checks
├── resume_build.py         # python-docx build, LibreOffice->PDF conversion, fitting ladder
└── resume_scrub.py         # DOCX/PDF metadata scrub + fingerprint verification
```

All data files are **versioned in git**, not a database table — the corpus
spec's own diagnosis is that the master resume drifted for months because it
lived only in an ad-hoc DOCX with no maintained source of truth. Git gives
exactly the change history that was missing. This also matches how the user
already iterates on this content: conversationally, in a session, not through
a web form.

Initial content for `metrics.json`, `jargon.json`, `projects.json`, and
`moments.json` is **transcribed directly from the corpus spec's Parts 4–9** —
that data already exists, verified across 30 real sessions, and re-deriving
it would just reintroduce the drift this design is meant to fix. `master.json`
(structured role/bullet data) is seeded from the same Part 4 timeline. The one
piece of new work the user does once, by hand, is resolving the three flagged
metric conflicts (Part 4's "known conflicts" table) — the plan does not guess
at those.

**Stack: pure Python**, matching the rest of this repo (no type annotations,
`# ── Section ──` banners, stdlib-first). The corpus spec's own toolchain
(Node.js `docx` package + LibreOffice + `pikepdf`) is Node-only in one place;
Python equivalents are used instead:

- `python-docx` for DOCX generation (was: `docx` npm package)
- `soffice --headless --convert-to pdf` via `subprocess` for PDF conversion (same tool, called from Python instead of a Node wrapper script)
- `pikepdf` for PDF metadata scrub (same library, it's already Python)

`resume_agent.py`'s generative stages (JD diagnosis, strategy, resume prose,
cover letter) call Claude via the same SDK client pattern `emailer.py` and
`research.py` already use, feeding the JSON data files in as context.
Lint/build/scrub are pure deterministic Python — no LLM involved, fully
unit-testable.

---

## Trigger & the strategy-approval gate

This cannot run unattended like `agent.py`/`monitor.py`. The corpus spec is
explicit that its Stage 4 (announce strategy, get approval *before* building)
is the highest-value stage and the one most often skipped — and that skipping
it is what caused the expensive rebuild cycles documented throughout the
corpus (Glean: ~12 rebuild cycles). So this is manual-only, invoked per
`job_applications` row, as two separate commands:

```bash
python3 resume_agent.py --job-id 42 --propose
```

Runs corpus-spec stages 0–4: load context (master resume + 2–3 nearest prior
roles), JD diagnosis, research (1–2 verified company facts, no fact no
claim), strategy. Stops there. Writes the strategy summary (section order,
which projects survive, title parentheticals, cover-letter angle, named
gaps) to `job_applications.resume_strategy` (JSONB). Nothing is built yet.

```bash
python3 resume_agent.py --job-id 42 --build
```

Only proceeds if `resume_strategy` is already populated on that row — i.e.
a human looked at the proposal. Runs stages 5–9: build resume, build cover
letter, humanizer lint pass, metadata scrub, upload to Supabase Storage.
Writes file references back to the row.

This mirrors the existing Gmail-draft pattern (`create_draft` proposes, the
user reviews, the user manually sends) rather than the critic-loop pattern
(auto-retry against a score threshold) — "is this the right strategy" isn't
something a rubric can validate the way tone can; every documented correction
in the corpus spec landed at exactly this decision point, made by a human.

**Deadline gate**: `--propose` refuses to run if the job's deadline is known
and has already passed (corpus spec Part 11's Cott/McKinsey lesson — timing
beat document quality, twice, decisively). Logs and exits rather than
building a document for a closed posting.

---

## Schema changes

Extends `job_applications` (not a new table — one row per application already
tracks company/role/stage/dates; this adds what document was used and how it
got in the door):

```sql
ALTER TABLE job_applications
  ADD COLUMN resume_strategy JSONB,        -- stage-4 output: section order, projects chosen, CL angle, named gaps
  ADD COLUMN resume_file_ref TEXT,         -- Supabase Storage path for the built DOCX/PDF
  ADD COLUMN cover_letter_file_ref TEXT,
  ADD COLUMN resume_variant TEXT,          -- which data snapshot/section-choices produced it (traceability)
  ADD COLUMN source_channel TEXT,          -- 'portal' | 'referral' | 'direct' -- corpus spec's strongest outcome signal
  ADD COLUMN response_date DATE,
  ADD COLUMN outcome TEXT;                 -- free text, not another CHECK-constrained stage machine
```

`stage` already covers `saved -> applied -> phone_screen -> onsite ->
offer/rejected/withdrawn/accepted` — that's the outcome-tracking pipeline the
corpus spec said was completely absent. These new columns add what document
produced the application and how it entered the pipeline, which is what
turns "we got rejected" into an actual signal once enough rows accumulate
(e.g. portal applications with an ATS-optimized variant vs. referral
applications with a human-scan variant).

`metrics.json` entries carry a `resolved` field, `null` for the three
conflicting figures the corpus spec flagged and never resolved (vendor cost
eliminated: $20K vs $120K/$200K; business loss prevented: $200K/month vs
$10K/month; build-vs-buy horizon: 3-year vs 5-year). `resume_lint.py` hard-fails
the build if a bullet references a metric that's still unresolved — the
corpus spec's own "fail loudly, don't silently pick" rule, now enforced
rather than stated. Resolving those three figures is a prerequisite the user
does once, not part of this implementation.

Files themselves go to a new Supabase Storage bucket; `resume_file_ref` /
`cover_letter_file_ref` store the bucket path. The `/applications` page in
the contact-manager can surface a download/preview link from those refs —
wiring that UI is not required for this phase to be useful (the CLI alone
produces a usable file), but the schema supports it without another
migration later.

---

## Lint (deterministic, pure functions)

`resume_lint.py` implements the mechanically-checkable rules from the corpus
spec's Part 3 and Part 9 — the ones that failed repeatedly even when stated
directly in a prompt, because prompt text alone doesn't prevent them:

- Em-dash scan (byte-level, post-build — corpus spec's own lesson: scan the
  built file, not the source, since a build step can reintroduce them)
- Jargon-table scan against `jargon.json` (Part 5)
- Metrics whitelist enforcement against `metrics.json`, hard-fail on unresolved conflicts
- Cover-letter violation detectors (Part 9): number-overlap with the resume,
  6-word n-gram overlap, exactly-three-capabilities enumeration, banned
  openers ("I am writing to apply"), word count > 300, closing-sentence
  hedge-stacking
- Page-count gate (`pdfinfo | grep Pages` via subprocess) driving the Part 13
  fitting ladder, applied in order (line spacing → bullet spacing → header
  spacing → margins → orphan-word trims → section folding → bullet drops →
  font-size floor, never skipping a rung)

Each check is a pure function: string (or built-file path) in, a list of
violations out. No network, no DB, no mocking needed beyond what the repo's
existing lint-adjacent code (`preflight.py`, `content_trust.py`) already
demonstrates.

## Metadata scrub

`resume_scrub.py` implements corpus spec Part 14: DOCX `core.xml` gets
`dc:creator`/`cp:lastModifiedBy` set to the user's name, `cp:keywords` to the
top JD keywords, realistic non-identical `dcterms:created`/`dcterms:modified`.
`app.xml` is **rewritten from a known-good template**, never regex-patched in
place (the corpus spec documents this exact bug: a naive `<TAG>.*?</TAG>`
pattern with `re.S` collapses the whole `Properties` element). PDF metadata
(XMP + docinfo) is overwritten via `pikepdf`. A verification gate greps the
unpacked and packed file for tool fingerprints (`docx-js`, `libreoffice`,
`soffice`, `python-docx`, etc.) before the file is considered done — content
occurrences of "Claude" in the resume's own Skills/Projects text are
expected and must not trip this check; only metadata-field occurrences count.

---

## Testing & error handling

- **Lint checks are pure functions, fully unit-testable** — no mocking beyond
  the repo's usual `pytest-mock` patterns.
- **Build/scrub get mocked at the subprocess/library boundary** — the
  LibreOffice `subprocess` call and `pikepdf` calls are tested the way
  `gmail.py`'s IMAP calls are tested: mock the call, assert arguments, never
  touch a real file conversion in CI.
- **`resume_agent.py`'s Claude-calling stages get mocked** exactly like
  `emailer.py`/`research.py` — `mocker.patch.object` on the SDK client,
  following `test_emailer_research.py`'s pattern.
- **This tool does not swallow failures.** Unlike the background enrichment
  scripts (`ats.py`, `jobright.py`), which must never raise because a failure
  there would cost a draft, `resume_agent.py` is manual and interactive — a
  failure should surface loudly (stack trace, non-zero exit) rather than
  degrade to an empty result, and must never leave `job_applications` pointing
  at a `resume_file_ref` for a file that doesn't actually exist in Storage.

---

## Explicitly out of scope for this phase

- **Auto-apply / actual submission** — Phase 2.5, a separate future design,
  gated behind its own explicit opt-in and CI-safety constraints (see the
  buildout spec's Phase 2.5 stub).
- **A contact-manager UI for editing `data/*.json`** — these are git-versioned
  files edited in a Claude Code session, same as the user already does today;
  a live editor is future work if ever needed, not part of this phase.
- **AI-content-detection evasion** — see the rejected-decision section above.
