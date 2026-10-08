"""
Read-only probe of real application forms, run by the apply_dryrun workflow. For each
job_applications id it opens the application page, fills only deterministic data (standard
contact fields, the resume/cover-letter files, fixed applicant_eligibility answers), and reports
what landed: the per-field fill report, the form inventory, and which required questions are
still empty. This is how form filling gets debugged from somewhere that cannot reach the job
sites directly.

It never writes to the database, never calls Claude or browser-use, never clicks a button, and
refuses to start if APPLY_AGENT_ARMED is present in the environment at all. Identifying detail
(company, role, page HTML) prints only when REPO_PRIVATE is exactly "true".
"""

import argparse
import base64
import gzip
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import apply_agent  # noqa: E402
import ats_fillers  # noqa: E402
import ats_platform  # noqa: E402
import config  # noqa: E402
import db  # noqa: E402

_MAX_IDS = 20
_HTML_CHUNK = 4000
_FILLERS = {"greenhouse": "fill_greenhouse", "ashby": "fill_ashby", "lever": "fill_lever"}


def parse_ids(raw):
    """'7, 8,9' -> [7, 8, 9]. Anything other than a short comma-separated list of positive
    integers raises ValueError."""
    text = (raw or "").strip()
    if not re.fullmatch(r"\d{1,9}(\s*,\s*\d{1,9})*", text):
        raise ValueError(f"job ids must be comma-separated integers, got {raw!r}")
    ids = [int(part) for part in text.split(",")]
    if len(ids) > _MAX_IDS:
        raise ValueError(f"at most {_MAX_IDS} ids per run")
    return ids


def probe(job_id, dump_html=False):
    """Opens one job's application form and reports what deterministic filling achieves.
    Never raises; failures land in report['error']."""
    report = {"id": job_id, "company": None, "role": None, "platform": None, "skipped": None,
              "signature": None, "fields": None, "attachments": None, "eligibility": None,
              "inventory": None, "required_unfilled": None, "missing": None, "html": None,
              "error": None}
    try:
        job = db.get_job_application(job_id)
    except Exception as exc:
        report["error"] = f"read failed: {type(exc).__name__}: {exc}"
        return report
    if not job:
        report["error"] = "no such job_applications row"
        return report
    report["company"] = job.get("company")
    report["role"] = job.get("role")
    platform = ats_platform.classify(job.get("job_url"))
    report["platform"] = platform
    if platform in ("workday", "aggregator"):
        report["skipped"] = platform
        return report

    page = None
    try:
        page = apply_agent._launch_page(apply_agent._application_url(job.get("job_url")))
        report["signature"] = bool(apply_agent._form_signature(page))
        if platform in _FILLERS:
            filler = getattr(ats_fillers, _FILLERS[platform])
            report["fields"] = filler(page, apply_agent._standard_field_values(job))
            report["attachments"] = apply_agent._attach_resume_and_cover_letter(page, job)
        report["eligibility"] = apply_agent._fill_eligibility_answers(
            page, apply_agent._eligibility_answers())
        inventory = apply_agent._form_inventory(page)
        report["inventory"] = [{k: f.get(k) for k in ("label", "kind", "required", "filled", "options")}
                               for f in (inventory or [])]
        report["required_unfilled"] = apply_agent._required_unfilled(inventory)
        if report["fields"] is not None:
            report["missing"] = apply_agent._missing_required({
                "fields": report["fields"], "attachments": report["attachments"],
                "required_unfilled": report["required_unfilled"]})
        else:
            report["missing"] = list(report["required_unfilled"])
        if dump_html:
            report["html"] = base64.b64encode(gzip.compress(page.content().encode())).decode()
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        if page is not None:
            apply_agent._close_page(page)
    return report


def _short(value, limit=200):
    text = " ".join(str(value).split())
    return text if len(text) <= limit else text[: limit - 3] + "..."


def render(report, detail):
    """Lines for one report. Company/role and page HTML appear only with detail=True."""
    head = f"== job #{report['id']}"
    if detail:
        head += f" | {_short(report.get('company'), 40)} | {_short(report.get('role'), 60)}"
    lines = [head, f"platform: {report.get('platform')}"]
    if report.get("skipped"):
        lines.append(f"skipped: excluded platform ({report['skipped']})")
        return lines
    if report.get("error"):
        # Playwright errors usually quote the page URL, which names the company.
        error = report["error"] if detail else report["error"].split(":", 1)[0] + ": (detail hidden)"
        lines.append(f"error: {_short(error, 300)}")
    lines.append(f"form signature: {'yes' if report.get('signature') else 'NO'}")
    lines.append(f"standard fields: {json.dumps(report.get('fields'))}")
    lines.append(f"attachments: {json.dumps(report.get('attachments'))}")
    lines.append(f"eligibility filled: {json.dumps(report.get('eligibility'))[:1500]}")
    lines.append(f"required still empty: {json.dumps(report.get('required_unfilled'))[:1500]}")
    lines.append(f"would block preview on: {json.dumps(report.get('missing'))[:1500]}")
    for field in report.get("inventory") or []:
        options = field.get("options") or []
        lines.append(
            f"  field: {_short(field.get('label'), 120)!r} | {field.get('kind')} | "
            f"{'required' if field.get('required') else 'optional'} | "
            f"{'filled' if field.get('filled') else 'EMPTY'}"
            + (f" | options: {_short('; '.join(map(str, options)), 300)}" if options else "")
        )
    if detail and report.get("html"):
        blob = report["html"]
        lines.append(f"HTML-BEGIN job #{report['id']} (gzip+base64)")
        lines.extend(blob[i:i + _HTML_CHUNK] for i in range(0, len(blob), _HTML_CHUNK))
        lines.append(f"HTML-END job #{report['id']}")
    return lines


def main(argv=None):
    parser = argparse.ArgumentParser(description="Read-only probe of real application forms.")
    parser.add_argument("--dump-html", action="store_true")
    args = parser.parse_args(argv)
    if "APPLY_AGENT_ARMED" in os.environ:
        print("REFUSED: APPLY_AGENT_ARMED is set; the dry run never runs in an armed environment.")
        return 2
    try:
        ids = parse_ids(os.environ.get("JOB_IDS", ""))
    except ValueError as exc:
        print(f"REFUSED: {exc}")
        return 2
    detail = os.environ.get("REPO_PRIVATE") == "true"
    if not detail:
        print("(company names and page HTML hidden: repo is not confirmed private)")
    for job_id in ids:
        for line in render(probe(job_id, dump_html=args.dump_html), detail=detail):
            print(line)
    return 0


if __name__ == "__main__":
    import logging
    # apply_agent/ats_fillers log exception text, which can quote a URL naming the company.
    _private = os.environ.get("REPO_PRIVATE") == "true"
    logging.basicConfig(level=logging.INFO if _private else logging.CRITICAL,
                        format="%(message)s", stream=sys.stdout)
    sys.exit(main())
