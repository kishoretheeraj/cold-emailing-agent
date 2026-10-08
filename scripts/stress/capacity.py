"""Capacity model for the Beelink pipeline (spec 2026-10-08 fifty-a-day §5): can the shipped timers,
batch sizes and time limits carry `target` submissions a day, and which stage gives out first?

Pure arithmetic over two kinds of input:
- facts read from the repo: each unit's OnCalendar firing count and TimeoutStartSec
  (deploy/beelink/systemd), batch sizes and caps (config.py, job_filters.DEFAULT_PREFERENCES);
- assumptions about the world (posting supply, verdict and success rates, seconds per row). These
  are labeled as assumptions and are meant to be replaced with a week of real numbers from
  scripts/apply_status.py. The model never reads the database and never touches the network.

Run: python3 scripts/stress/capacity.py [--target 50] [--json]
"""

import argparse
import json
import math
import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_UNITS = _ROOT / "deploy" / "beelink" / "systemd"

# ── Assumptions (replace with measured values) ─────────────────────────────────

ASSUMPTIONS = {
    "new_postings_per_day": 120,     # new rows sourcing + JobRight + LinkedIn save after dedup and triage
    "embedding_pass_rate": 0.6,      # share that clears the local similarity threshold
    "strong_rate": 0.35,             # share of judged postings the judge calls strong
    "resume_success_rate": 0.9,      # builds that pass lint, fit one page and upload
    "preparable_rate": 0.75,         # share on Greenhouse/Lever/Ashby (+ Workday with a vault key)
    "prepare_success_rate": 0.7,     # previews that reach ready_for_review (live 2026-10-06: 7/8 got the basics)
    "approve_rate": 0.8,             # previews you approve
    "submit_success_rate": 0.9,      # confirmed submissions among approved
    "judge_seconds_per_batch": 60,   # one claude -p call judging JOB_PICK_JUDGE_BATCH postings
    "resume_seconds_per_row": 180,   # propose + cover letter calls, LibreOffice, lint, upload
    "prepare_seconds_per_row": 120,  # real ATS page, uploads, one batched screening call
    "submit_seconds_per_row": 90,    # refill, click, up to APPLY_CONFIRMATION_POLLS seconds of polling
    "resume_calls_per_row": 2,
    "prepare_calls_per_row": 1,
}

# ── Facts from the repo ────────────────────────────────────────────────────────

_FIELD_MAX = {"hour": 24, "minute": 60}


def _expand(field, kind):
    values = set()
    for part in field.split(","):
        step = 1
        if "/" in part:
            part, step_text = part.split("/", 1)
            step = int(step_text)
            start = 0 if part in ("*", "") else int(part)
            values.update(range(start, _FIELD_MAX[kind], step))
        elif part == "*":
            values.update(range(_FIELD_MAX[kind]))
        else:
            values.add(int(part))
    return values


def firings_per_day(on_calendar):
    """How many times a systemd OnCalendar expression fires per day. Handles the forms the units
    use: an optional '*-*-*' date part, then HH:MM[:SS] with *, lists, a/b steps. Seconds must be
    a single value (every unit fires at most once a minute)."""
    parts = on_calendar.split()
    time = parts[-1]
    if len(parts) > 2 or (len(parts) == 2 and parts[0] != "*-*-*"):
        raise ValueError(f"unsupported OnCalendar: {on_calendar!r}")
    fields = time.split(":")
    if len(fields) == 3:
        if not re.fullmatch(r"\d+", fields[2]):
            raise ValueError(f"unsupported seconds in OnCalendar: {on_calendar!r}")
        fields = fields[:2]
    if len(fields) != 2:
        raise ValueError(f"unsupported OnCalendar: {on_calendar!r}")
    return len(_expand(fields[0], "hour")) * len(_expand(fields[1], "minute"))


def unit_schedule(name, units_dir=_UNITS):
    timer = (units_dir / f"{name}.timer").read_text()
    service = (units_dir / f"{name}.service").read_text()
    calendar = re.search(r"^OnCalendar=(.+)$", timer, re.M).group(1).strip()
    timeout = re.search(r"^TimeoutStartSec=(\d+)", service, re.M)
    return {"runs_per_day": firings_per_day(calendar), "timeout_seconds": int(timeout.group(1)) if timeout else None,
            "on_calendar": calendar}


def repo_facts(units_dir=_UNITS):
    sys.path.insert(0, str(_ROOT))
    import config
    import job_filters
    return {
        "units": {u: unit_schedule(u, units_dir) for u in
                  ("job-sourcing", "job-pick", "resume-worker", "apply-prepare", "apply-submit")},
        "job_pick_max_per_run": config.JOB_PICK_MAX_PER_RUN,
        "job_pick_judge_batch": config.JOB_PICK_JUDGE_BATCH,
        "resume_batch": config.RESUME_WORKER_BATCH,
        "prepare_batch": config.APPLY_PREPARE_BATCH,
        "submit_batch": config.APPLY_SUBMIT_BATCH,
        "daily_submit_cap": job_filters.DEFAULT_PREFERENCES["daily_submit_cap"],
    }


# ── The model ──────────────────────────────────────────────────────────────────

def _per_run(batch, seconds_per_row, timeout):
    # A run does at most `batch` rows and stops when its unit's start timeout would kill it.
    if timeout is None:
        return batch
    return max(0, min(batch, int(timeout // max(seconds_per_row, 1))))


def model(facts, assumptions, target):
    """Daily ceiling of each stage, the demand `target` submissions put on it, and the bottleneck.
    Ceilings are in rows per day at that stage; demand is what that stage must handle per day."""
    a = assumptions
    u = facts["units"]
    # Rows needed at each stage per submitted application, walking the funnel backwards.
    submits = target
    approved = submits / a["submit_success_rate"]
    prepared = approved / a["approve_rate"]
    prepare_attempts = prepared / a["prepare_success_rate"]
    resumes = prepare_attempts / a["preparable_rate"] / a["resume_success_rate"]
    judged = resumes / a["strong_rate"]
    scored = judged / a["embedding_pass_rate"]

    ceilings = {
        "supply": a["new_postings_per_day"],
        "job_pick": u["job-pick"]["runs_per_day"] * facts["job_pick_max_per_run"],
        "judge": u["job-pick"]["runs_per_day"] * min(
            facts["job_pick_max_per_run"],
            facts["job_pick_judge_batch"] * int(u["job-pick"]["timeout_seconds"] // a["judge_seconds_per_batch"])),
        "resume": u["resume-worker"]["runs_per_day"] * _per_run(
            facts["resume_batch"], a["resume_seconds_per_row"], u["resume-worker"]["timeout_seconds"]),
        "prepare": u["apply-prepare"]["runs_per_day"] * _per_run(
            facts["prepare_batch"], a["prepare_seconds_per_row"], u["apply-prepare"]["timeout_seconds"]),
        "submit": u["apply-submit"]["runs_per_day"] * _per_run(
            facts["submit_batch"], a["submit_seconds_per_row"], u["apply-submit"]["timeout_seconds"]),
        "daily_cap": facts["daily_submit_cap"] or math.inf,
    }
    demand = {
        "supply": scored, "job_pick": scored, "judge": judged, "resume": resumes,
        "prepare": prepare_attempts, "submit": approved, "daily_cap": submits,
    }
    # Prepare and submit share one display (display :1, one flock), so their seconds add up.
    display_seconds = prepare_attempts * a["prepare_seconds_per_row"] + approved * a["submit_seconds_per_row"]
    stages = {name: {"ceiling": ceilings[name], "demand": round(demand[name], 1),
                     "utilization": round(demand[name] / ceilings[name], 3) if ceilings[name] else math.inf}
              for name in ceilings}
    stages["display"] = {"ceiling": 86_400, "demand": round(display_seconds), "utilization":
                         round(display_seconds / 86_400, 3)}
    bottleneck = max(stages, key=lambda n: stages[n]["utilization"])
    achievable = min(target / stages[n]["utilization"] for n in stages if stages[n]["utilization"] > 0)
    calls = (math.ceil(judged / facts["job_pick_judge_batch"]) + resumes * a["resume_calls_per_row"]
             + prepare_attempts * a["prepare_calls_per_row"])
    return {
        "target": target,
        "stages": stages,
        "bottleneck": bottleneck,
        "achievable_per_day": round(min(achievable, facts["daily_submit_cap"] or math.inf), 1),
        "subscription_calls_per_day": round(calls),
        "display_hours_per_day": round(display_seconds / 3600, 1),
    }


def render(result, assumptions):
    lines = [f"Capacity for {result['target']} submissions a day",
             f"{'stage':<10} {'ceiling/day':>12} {'needed/day':>11} {'use':>6}"]
    for name, s in result["stages"].items():
        ceiling = "no cap" if s["ceiling"] == math.inf else f"{s['ceiling']:,.0f}"
        unit = " s" if name == "display" else ""
        lines.append(f"{name:<10} {ceiling + unit:>12} {s['demand']:>11,.1f} {s['utilization']:>6.0%}")
    lines += [
        f"bottleneck: {result['bottleneck']}; achievable with these assumptions: {result['achievable_per_day']}/day",
        f"display busy {result['display_hours_per_day']} h/day; about {result['subscription_calls_per_day']} "
        f"claude -p calls/day on the subscription",
        "assumptions (replace with a week of apply_status numbers): "
        + ", ".join(f"{k}={v}" for k, v in assumptions.items()),
    ]
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--target", type=int, default=50)
    parser.add_argument("--json", action="store_true")
    for key, value in ASSUMPTIONS.items():
        parser.add_argument(f"--{key.replace('_', '-')}", type=type(value), default=value)
    args = parser.parse_args(argv)
    assumptions = {k: getattr(args, k) for k in ASSUMPTIONS}
    result = model(repo_facts(), assumptions, args.target)
    print(json.dumps(result, default=str, indent=1) if args.json else render(result, assumptions))
    return 0 if result["achievable_per_day"] >= args.target else 1


if __name__ == "__main__":
    sys.exit(main())
