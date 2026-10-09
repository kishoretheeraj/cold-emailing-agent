"""
Read-only snapshot of the job-application pipeline, printed by the apply_status workflow so the
pipeline can be watched without database access. Never writes.

Privacy: GitHub Actions logs are world-readable while the repo is public. Per-row detail
(company, role, URL, blocked reasons) prints only when REPO_PRIVATE is exactly "true", which the
workflow sets from the GitHub API, failing closed. applicant_eligibility values (EEO,
work-authorization answers) never print in any mode; only their key names do.
"""

import collections
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ats_platform  # noqa: E402
import config  # noqa: E402
import db  # noqa: E402

_EXPECTED_ELIGIBILITY_KEYS = ("work_authorized_us", "requires_visa_sponsorship", "gender",
                              "race_ethnicity", "veteran_status", "disability_status", "salary")
_ATTENTION = ("needs_input", "failed_retryable", "needs_confirmation", "failed_terminal",
              "unsupported")
_ACTIVE = ("preparing", "ready_for_review", "approved", "submitting")
# Only what the report reads (posting snapshots and previews are large), every row in pages.
_COLUMNS = ("id,company,role,job_url,stage,automation_status,pick_verdict,resume_file_ref,"
            "cover_letter_file_ref,resume_error,apply_blocked_reason,source")
# Per-row listings stop here; at fifty a day they would otherwise run to thousands of lines.
_LIST_LIMIT = 50


def _one_line(value, limit):
    text = " ".join(str(value if value is not None else "").split())
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _platform(row):
    try:
        return ats_platform.classify(row.get("job_url"))
    except Exception:
        return "unknown"


def _supported(row):
    return _platform(row) in config.APPLY_AGENT_HAND_MAPPED_PLATFORMS


def funnel(rows):
    """Counts for the path to a submitted application on the supported ATS platforms."""
    strong = [r for r in rows if _supported(r) and r.get("pick_verdict") == "strong"
              and r.get("stage") in ("saved", "ready_to_submit")]
    statuses = collections.Counter(r.get("automation_status") or "none" for r in rows)
    return {
        "supported_strong": len(strong),
        "with_documents": sum(1 for r in strong if r.get("resume_file_ref") and r.get("cover_letter_file_ref")),
        "resume_error": sum(1 for r in strong if r.get("resume_error")),
        "ready_for_review": statuses.get("ready_for_review", 0),
        "approved": statuses.get("approved", 0),
        "needs_confirmation": statuses.get("needs_confirmation", 0),
        "submitted": statuses.get("submitted", 0),
    }


def _eligibility_lines(raw):
    if not raw:
        return ["applicant_eligibility: not set"]
    try:
        answers = json.loads(raw)
        if not isinstance(answers, dict):
            raise ValueError("not an object")
    except Exception:
        return ["applicant_eligibility: unparseable JSON"]
    keys = sorted(k for k, v in answers.items() if v not in (None, ""))
    missing = [k for k in _EXPECTED_ELIGIBILITY_KEYS if k not in keys]
    return [f"applicant_eligibility keys ({len(keys)}): {', '.join(keys) or '-'}",
            f"applicant_eligibility missing expected keys: {', '.join(missing) or 'none'}"]


def _counter_lines(title, counter):
    lines = [title]
    for key, count in sorted(counter.items(), key=lambda kv: (-kv[1], str(kv[0]))):
        lines.append(f"  {key}: {count}")
    return lines


def run_lines(runs, detail):
    """The Beelink worker's recent run log: outcome counts, takeovers and error classes; the
    free-text stop reasons (they can name a company or URL) only in detail mode."""
    runs = [r for r in (runs or []) if isinstance(r, dict)]
    lines = [f"Recent apply runs: {len(runs)}"]
    lines += _counter_lines("Runs by kind/outcome:", collections.Counter(
        f"{r.get('kind')}/{r.get('outcome')}" for r in runs))
    lines.append(f"Takeovers requested: {sum(int(r.get('takeovers') or 0) for r in runs)}")
    errors = collections.Counter(r.get("error_class") for r in runs if r.get("error_class"))
    if errors:
        lines += _counter_lines("Error classes:", errors)
    if detail:
        for r in runs:
            if r.get("stop_reason"):
                lines.append(f"  #{r.get('application_id')} | {r.get('kind')} | {r.get('outcome')} | "
                             f"{_one_line(r.get('stop_reason'), 200)}")
    return lines


def summarize(rows, eligibility_raw, detail, runs=None):
    """Report lines. detail=True adds per-row company/role/reason lines; never call it with
    detail=True unless the repo is private."""
    rows = [r for r in (rows or []) if isinstance(r, dict)]
    lines = [f"job_applications rows: {len(rows)}"]
    lines += _counter_lines("By stage:", collections.Counter(r.get("stage") or "none" for r in rows))
    lines += _counter_lines("By automation_status:",
                            collections.Counter(r.get("automation_status") or "none" for r in rows))
    open_rows = [r for r in rows if r.get("stage") in ("saved", "ready_to_submit")]
    lines += _counter_lines("Open rows by platform:", collections.Counter(_platform(r) for r in open_rows))
    lines += _counter_lines("Open rows by source:", collections.Counter(r.get("source") or "none" for r in open_rows))

    f = funnel(rows)
    lines.append("First-10 funnel (Greenhouse/Ashby/Lever):")
    for key in ("supported_strong", "with_documents", "resume_error", "ready_for_review",
                "approved", "needs_confirmation", "submitted"):
        lines.append(f"  {key}: {f[key]}")

    lines += _eligibility_lines(eligibility_raw)

    if runs is not None:
        lines += run_lines(runs, detail)

    attention = [r for r in rows if r.get("automation_status") in _ATTENTION]
    lines.append(f"Rows needing attention: {len(attention)}")
    if not detail:
        lines.append("(per-row detail hidden: repo is not confirmed private)")
        return lines

    for r in attention[:_LIST_LIMIT]:
        lines.append(
            f"  #{r.get('id')} | {_one_line(r.get('company'), 40)} | {_one_line(r.get('role'), 60)} | "
            f"{r.get('automation_status')} | {_one_line(r.get('apply_blocked_reason'), 200)}"
        )
    pipeline = [r for r in rows if _supported(r) and (
        r.get("automation_status") in _ACTIVE
        or (r.get("pick_verdict") == "strong" and r.get("stage") in ("saved", "ready_to_submit")))]
    if len(attention) > _LIST_LIMIT:
        lines.append(f"  ... and {len(attention) - _LIST_LIMIT} more")
    lines.append(f"Supported pipeline rows: {len(pipeline)}")
    for r in pipeline[:_LIST_LIMIT]:
        docs = "docs" if r.get("resume_file_ref") and r.get("cover_letter_file_ref") else "no-docs"
        if r.get("resume_error"):
            docs = "resume-error: " + _one_line(r.get("resume_error"), 80)
        lines.append(
            f"  #{r.get('id')} | {_one_line(r.get('company'), 40)} | {_one_line(r.get('role'), 60)} | "
            f"{_platform(r)} | {r.get('stage')} | {r.get('automation_status')} | {docs}"
        )
    if len(pipeline) > _LIST_LIMIT:
        lines.append(f"  ... and {len(pipeline) - _LIST_LIMIT} more")
    return lines


def main():
    detail = os.environ.get("REPO_PRIVATE") == "true"
    try:
        rows = db.get_all_job_applications(_COLUMNS)
        eligibility_raw = db.load_prompts().get("applicant_eligibility")
    except Exception as exc:
        print(f"apply_status: database read failed: {type(exc).__name__}: {exc}")
        return 1
    try:
        runs = db.get_recent_application_runs()
    except Exception as exc:
        # Before migration 20261008000000 the table does not exist; the rest of the report stands.
        print(f"application_runs: unavailable ({type(exc).__name__})")
        runs = None
    for line in summarize(rows, eligibility_raw, detail=detail, runs=runs):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
