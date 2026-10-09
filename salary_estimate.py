"""
Salary answer for one job application, from offered H-1B wages (h1b_wage_stats, built by
ingest_oflc_lca.py from the same DOL LCA files as employer_h1b_stats).

estimate(job) walks, most specific first: this employer's filings for the job's role family in the
posting's state, this employer's filings in any state, then every employer's filings in that state,
then nationally. The first level with enough filings gives the range (25th to 75th percentile,
rounded to $5,000, never below config.SALARY_FLOOR_USD). Returns None when the role is outside
every role family or no level has enough filings; the caller then keeps the operator's fixed
applicant_eligibility answer. Never raises.

The employer is matched by exact normalized name (the same normalize + alias-group pipeline as
the visa gate), or through a company_intel row whose H-1B match is 'confirmed' (a human decision;
'auto' fuzzy matches are not trusted). There is no fuzzy fallback: a wrong employer would anchor a
salary on someone else's pay, so an unmatched employer degrades to market data, never to a guess.
"""
import json
import logging
import re

import config
import db
import entity_resolution
import ingest_oflc_lca

log = logging.getLogger(__name__)

_STATE_RE = re.compile(r"(?:^|,\s*|[\s/;|(\[])([A-Z]{2})\b")


def _state_codes_in(text):
    """State abbreviations in a location string. Matches 'City, ST' as well as standalone
    codes after delimiters ('NY / TX', 'Remote (TX)'), so multi-state postings are detected
    instead of silently picking the first comma-prefixed one."""
    return {code for code in _STATE_RE.findall(str(text or "")) if code in _US_STATES}
_US_STATES = frozenset(
    "AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM "
    "NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY".split())
_ROUND_TO = 5000


def state_from_location(location):
    # Several distinct states (or a USA-wide location) is ambiguous: None sends the walk to
    # the employer-anywhere / national levels instead of guessing the first one listed.
    if isinstance(location, (list, tuple)):
        location = "; ".join(str(item) for item in location if item)
    states = _state_codes_in(location)
    return next(iter(states)) if len(states) == 1 else None


def _posting_location(job):
    snapshot = job.get("posting_snapshot") or {}
    if isinstance(snapshot, str):
        try:
            snapshot = json.loads(snapshot)
        except ValueError:
            return None
    return snapshot.get("location") if isinstance(snapshot, dict) else None


def _employer_key(company):
    normalized = entity_resolution.canonicalize_alias_group(entity_resolution.normalize(company or ""))
    if not normalized:
        return None, None
    intel = (db.get_company_intel_by_normalized_names([normalized]) or [None])[0]
    if intel and intel.get("match_status") == "confirmed" and intel.get("matched_employer_id"):
        matched = db.get_employer_h1b_normalized_name(intel["matched_employer_id"])
        if matched:
            return matched, "matched"
    return normalized, "exact"


def _round(value):
    return int(round(value / _ROUND_TO) * _ROUND_TO)


def _format_range(low, high):
    return f"${low:,}" if low == high else f"${low:,} - ${high:,}"


def estimate(job):
    """Returns {"text", "low", "high", "basis"} or None. Never raises."""
    try:
        family = ingest_oflc_lca.role_family_for(job.get("role"))
        if family is None:
            return None
        state = state_from_location(_posting_location(job))
        employer, how = _employer_key(job.get("company"))
        names = [ingest_oflc_lca.MARKET_KEY] + ([employer] if employer else [])
        rows = {(r["normalized_name"], r["worksite_state"]): r
                for r in db.get_h1b_wage_stats(names, family)}

        market = ingest_oflc_lca.MARKET_KEY
        levels = []
        if employer:
            if state:
                levels.append(((employer, state), config.SALARY_MIN_EMPLOYER_FILINGS,
                               f"{job.get('company')}'s H-1B filings in {state}"))
            levels.append(((employer, market), config.SALARY_MIN_EMPLOYER_FILINGS,
                           f"{job.get('company')}'s H-1B filings (all states)"))
        if state:
            levels.append(((market, state), config.SALARY_MIN_MARKET_FILINGS, f"all H-1B filings in {state}"))
        levels.append(((market, market), config.SALARY_MIN_MARKET_FILINGS, "all H-1B filings nationally"))

        for key, min_filings, label in levels:
            row = rows.get(key)
            if not row or row["filings"] < min_filings:
                continue
            low = max(config.SALARY_FLOOR_USD, _round(row["wage_p25"]))
            high = max(low, _round(row["wage_p75"]))
            years = row.get("fiscal_years") or []
            span = f"FY{min(years)}-FY{max(years)}" if years else "recent years"
            matched_note = f" (employer matched as '{employer}')" if how == "matched" and key[0] == employer else ""
            basis = (f"{label}{matched_note}: {row['filings']} {family.replace('_', ' ')} filings, {span}, "
                     f"25th-75th percentile ${row['wage_p25']:,}-${row['wage_p75']:,}; "
                     f"floor ${config.SALARY_FLOOR_USD:,}")
            return {"text": _format_range(low, high), "low": low, "high": high, "basis": basis}
        return None
    except Exception as exc:
        log.warning(f"[APPLY-AGENT] | salary estimate failed | {job.get('company')} | {exc}")
        return None
