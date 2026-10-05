# Resume generation on the Claude subscription — design

Date: 2026-10-04. Status: design approved in conversation (option 3 for the cover letter); spec
awaiting review.

## Goal

`resume_agent.py`'s two Claude calls (strategy in `--propose`, cover letter in `--build`) run on
Kishore's Claude Pro/Max subscription instead of the pay-as-you-go `ANTHROPIC_API_KEY`, with the
automatic strong-verdict path moved from GitHub Actions to the Beelink. The resume and cover letter
PDFs carry no tool attribution, no invisible characters, and no build-tool metadata.

Out of scope (separate sub-projects, not designed here): agentic company research, Google Docs
output, Meta Muse. `job_pick.py`'s LLM judge and every other Claude call in the repo stay on the API
key.

## Verified facts this design rests on (2026-10-04)

- **Subscription use is allowed via Claude Code, not the Messages API.** Anthropic's help center
  ("Use the Claude Agent SDK with your Claude plan"): the June 15 change is paused; "Claude Agent
  SDK, `claude -p`, and third-party app usage still draw from your subscription's usage limits."
  A subscription OAuth token is never sent to the Messages API directly.
- **`--bare` cannot be used.** Its help text: "Anthropic auth is strictly ANTHROPIC_API_KEY or
  apiKeyHelper via --settings (OAuth and keychain are never read)."
- **Isolation is a measured requirement, not hygiene.** A one-word `claude -p` call on the Mac from
  an empty directory, tools disabled, cost 223,945 context tokens (the operator's global
  `CLAUDE.md`, plugins, skills, MCP connectors, session hooks); `total_cost_usd` (a notional
  list-price figure) was $1.34. Adding `--strict-mcp-config --setting-sources ""` dropped it to
  ~6.5K (4,127 cache-create + 2,385 cache-read). An empty `CLAUDE_CONFIG_DIR` with no token fails
  cleanly (not logged in), so the config dir is isolated from the keychain login.
- **JSON output shape** (`--output-format json`): `type`, `subtype`, `is_error`, `result`,
  `usage.{input_tokens, output_tokens, cache_creation_input_tokens, cache_read_input_tokens}`,
  `total_cost_usd`, `modelUsage`, `num_turns`.
- **Claude's text is watermarked.** anthropic.com/news/claude-text-watermark (2026-08-14): a
  statistical, SynthID-Text-style pattern in word choice; "nothing is added to the text and there
  are no hidden characters"; light editing won't remove it; detection is restricted to eligible
  organizations under EU law. This corrects the Aug 29 Phase 3 spec, which said no such watermark
  was publicly known.
  - The **resume** contains no Claude-written prose: every bullet is the operator's own text from
    `resume/data/metrics.json`, and Claude only selects ids, projects, skills (from the governed
    pool) and order. There is nothing for a statistical watermark to live in.
  - The **cover letter** is ~270 words of Claude prose and is watermarked. **Decision (option 3):
    keep it Claude-written and accept the watermark.** No watermark-removal step is built or
    integrated, and none will be. A paragraph-library or talking-points cover letter remains an
    option for later.
- **Fonts.** Mac builds embed real Calibri (from Microsoft Word's bundled fonts). Debian has no
  Calibri; LibreOffice would substitute Carlito, which puts a Linux font name into a PDF whose
  scrubbed metadata claims Microsoft Word.

## Components

### `claude_subscription.py` (new, self-contained)

Public surface: `complete(prompt, system=None, model=None) -> (text, usage)`, where `usage` is
`{"input_tokens", "output_tokens"}` (cache tokens folded into input, so the ledger counts real
context). Raises `ClaudeSubscriptionError` on any failure, and `ClaudeUsageLimitError` (a subclass)
when the CLI reports the subscription window is exhausted. Raises, never swallows, matching
`resume_agent`'s raise-on-failure contract.

Invocation, every call:

```
claude -p --output-format json --tools "" --no-session-persistence
       --strict-mcp-config --setting-sources ""
       --model <model> [--system-prompt <system>]
```

- Prompt goes on stdin (no argv length limit, nothing in `ps`).
- `cwd` = a fresh empty temp dir; `CLAUDE_CONFIG_DIR` = a fresh empty temp dir. Both removed after.
- Child env = parent env **minus `ANTHROPIC_API_KEY`** (present via `config.py`'s `load_dotenv()`;
  if it reaches the CLI, Claude Code bills the API key instead of the subscription), plus
  `DISABLE_AUTOUPDATER=1`. `CLAUDE_CODE_OAUTH_TOKEN` must be set; missing it raises before spawning.
- `--system-prompt` *replaces* Claude Code's default agent prompt, so the resume prompts aren't
  framed as a coding session. When no system prompt is given, a short neutral one is used.
- Timeout `config.CLAUDE_CLI_TIMEOUT_SECONDS` (300). Binary `config.CLAUDE_CLI_PATH` (default
  `"claude"`; the Beelink unit sets the absolute path).
- Text post-processing (`sanitize`): strip zero-width, bidi-control and other `Cf`/`Cc` characters
  (newline and tab kept).

### `resume_agent.py`

- `_call_claude` dispatches on `config.RESUME_CLAUDE_BACKEND` (`"subscription"` default, `"api"`
  keeps today's `anthropic` path as a one-line rollback). Both paths run `sanitize`.
- `_track_usage` passes `billing` through: subscription calls record real tokens and `$0` cost
  onto `job_applications.resume_*` and `api_usage_log`.
- New attribution scan on generated text (strategy JSON values and cover letter): hard-fail on
  `generated with`, `co-authored-by`, `claude code`, `as an ai`, `language model`, `anthropic`,
  `noreply@`. The bare word "Claude" is allowed (it appears in the operator's skills/projects).
- New `--drain` CLI mode: `drain(limit=config.RESUME_WORKER_BATCH)` fetches rows with
  `pick_verdict='strong' AND resume_file_ref IS NULL AND resume_error IS NULL`, oldest first, runs
  `propose` (skipped if `resume_strategy` already exists) then `build`, per row in its own
  `try/except`. A `ClaudeUsageLimitError` stops the drain without marking the row (it retries next
  run). Any other exception writes `resume_error` (truncated message) so the row isn't retried every
  run against the subscription window; clearing the column retries it. Returns the error count;
  `__main__` exits 1 when nonzero so the unit's `OnFailure=` fires.

### `resume_scrub.py`

- `scrub_pdf_metadata` deletes the whole existing XMP packet before writing the Word-shaped one, so
  no LibreOffice keys survive in XMP.
- New `read_pdf_xmp_text(pdf_path)`; `build` runs `verify_no_fingerprints` over docinfo **and**
  XMP for both PDFs.
- New `embedded_font_names(pdf_path)`; `build` fails if any embedded font family isn't
  `config.RESUME_FONT_NAME` (catches Carlito/Liberation/DejaVu substitution).
- `_FINGERPRINTS` gains `carlito`, `liberation`, `dejavu`, `claude code`, `anthropic`.

### `job_pick.py`

On `strong`, when `RESUME_CLAUDE_BACKEND == "subscription"`, log
`[JOB-PICK] | ... | queued for Beelink resume worker` instead of calling `propose`/`build`
(GitHub Actions never holds the subscription token). With `"api"`, today's zero-tap behaviour.

### Database (one additive migration)

- `job_applications.resume_error TEXT NULL`, with explicit `GRANT UPDATE (resume_error)` to `anon`
  and `authenticated` (migration `20260925000000`'s column grants were computed once; a new column
  has no privilege otherwise — and `authenticated` must be checked too, per `20261004000001`).
- `api_usage_log.billing TEXT NOT NULL DEFAULT 'api' CHECK (billing IN ('api','subscription'))`,
  plus an INSERT grant on the column if the table uses column-level grants (verify live).
- `db.get_strong_applications_without_resume(limit)`, `db.set_resume_error(id, message)`,
  `db.log_api_usage(..., billing="api")`, `usage_tracking.log_usage(..., billing="api")`
  (subscription rows skip `calculate_cost` and write `0`).

### Beelink

- `deploy/beelink/systemd/resume-worker.service` (oneshot, `User=jobagent`,
  `EnvironmentFile=/etc/job-agent/base.env` and `/etc/job-agent/claude.env`,
  `Environment=CLAUDE_CLI_PATH=/var/lib/job-agent/.local/bin/claude`,
  `Environment=APPLY_AGENT_ARMED=`, `ExecStart=... resume_agent.py --drain`,
  `TimeoutStartSec=3600`, `MemoryMax=1536M`, standard hardening, `OnFailure=notify-failure@%n`).
- `resume-worker.timer`: every 30 min, `RandomizedDelaySec=300`. Not enabled by the provisioning
  script; enabled after the first watched run.
- `claude.env` holds only `CLAUDE_CODE_OAUTH_TOKEN` (root:root 0600), loaded by no other unit.
  The operator creates it with `claude setup-token` output; nothing writes its value for them.
- `provision-debian.sh` adds `libreoffice-writer-nogui`, installs Claude Code for `jobagent`
  (native installer, `HOME=/var/lib/job-agent`), and refuses to proceed if
  `/usr/local/share/fonts/calibri/Calibri.ttf` is missing (the operator copies the Calibri TTFs
  from their own Microsoft Word install; Carlito would fail the font check).

## Error handling summary

| Failure | Result |
|---|---|
| No `CLAUDE_CODE_OAUTH_TOKEN` | `ClaudeSubscriptionError` before spawning; drain records `resume_error` |
| CLI nonzero exit / `is_error` / bad JSON / timeout | `ClaudeSubscriptionError`; row gets `resume_error` |
| Usage window exhausted | `ClaudeUsageLimitError`; drain stops, row untouched, retried next run |
| Attribution phrase, fingerprint, wrong font | `LintFailedError`; row gets `resume_error`; nothing uploaded |
| `api_usage_log` write fails | warning only (existing best-effort contract) |

## Testing

Unit (all subprocess calls mocked; `claude` never runs in the suite): argv contains the isolation
flags and never `--bare`; child env lacks `ANTHROPIC_API_KEY` and has `DISABLE_AUTOUPDATER`;
cwd/config dirs are empty and cleaned up; JSON parse, `is_error`, timeout, missing token,
usage-limit classification; `sanitize`; attribution scan; XMP rewrite and XMP fingerprint check on
a real generated PDF; embedded-font check on real PDFs; drain selection, skip-propose, usage-limit
stop, `resume_error` write, exit code; `job_pick` queue-vs-zero-tap on the backend switch;
`log_usage` billing; static unit tests for the new systemd files; migration SQL static assertions.

Live (done by hand, recorded in the plan): canary test (a unique instruction in the default
`~/.claude/CLAUDE.md` must not change the output); trivial-prompt context under 8K tokens; one
real propose+build on the Beelink against a real strong row; PDF docinfo, XMP and font names
inspected; `api_usage_log` row shows `billing='subscription'`, `cost_usd=0`.
