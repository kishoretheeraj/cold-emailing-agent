"""
Ingests SEC Form D exempt-offering filings into a recently-raised-funding
signal keyed by issuer name.

Form D is filed within 15 days of the first sale in a private raise, published
quarterly by SEC DERA as tab-separated tables. Free, official, no API key.

Governance (mirrors the H-1B gate): a company with no Form D match is
`unknown`, never "did not raise". Absence means not observed -- the company may
have raised through a route that does not file Form D, or under a different
legal entity name. This module only ever reports an observed raise.

Run manually or from the quarterly workflow. Never imported by agent.py.
"""

import csv
import logging
import os
import re

import entity_resolution

log = logging.getLogger(__name__)

INDEX_URL = "https://www.sec.gov/data-research/sec-markets-data/form-d-data-sets"

# SEC requires a descriptive User-Agent with contact info on automated requests.
_USER_AGENT = "cold-email-agent (personal job-search research; kishoretheeraj@gmail.com)"

DEFAULT_QUARTERS_BACK = 4
FUNDING_SOURCE = "sec_form_d"

# ── Table layout ───────────────────────────────────────────────────────────────

SUBMISSION_FILE = "FORMDSUBMISSION.tsv"
ISSUERS_FILE = "ISSUERS.tsv"
OFFERING_FILE = "OFFERING.tsv"

# Pooled investment funds are VC/PE vehicles raising their own capital, not
# operating companies that hire. They are 65% of all filings, and the boolean
# flag alone is not enough -- confirmed against real 2025Q4 data, 222 rows leave
# ISPOOLEDINVESTMENTFUNDTYPE blank and only declare it via INDUSTRYGROUPTYPE.
# Both checks are required.
_POOLED_INDUSTRY_GROUPS = {"pooled investment fund"}

_MONTHS = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}


# ── Value normalization ────────────────────────────────────────────────────────

def parse_filing_date(raw):
    """
    Parse Form D's DD-MON-YYYY filing date (e.g. '31-DEC-2025') to an ISO
    date string. Returns None on anything unparseable -- never raises.
    """
    try:
        if not raw or not isinstance(raw, str):
            return None
        parts = raw.strip().upper().split("-")
        if len(parts) != 3:
            return None
        day, mon, year = parts
        month = _MONTHS.get(mon)
        if month is None:
            return None
        day_i, year_i = int(day), int(year)
        if not (1 <= day_i <= 31) or year_i < 1900:
            return None
        return f"{year_i:04d}-{month:02d}-{day_i:02d}"
    except Exception:
        return None


def parse_amount(raw):
    """
    Parse a Form D dollar amount to a positive int. Returns None for blank,
    zero, negative, or non-integer values. Never raises.
    """
    try:
        if raw is None:
            return None
        text = str(raw).strip()
        if not text or not text.lstrip("-").isdigit():
            return None
        value = int(text)
        return value if value > 0 else None
    except Exception:
        return None


def _is_pooled_fund(offering):
    if (offering.get("ISPOOLEDINVESTMENTFUNDTYPE") or "").strip().lower() == "true":
        return True
    industry = (offering.get("INDUSTRYGROUPTYPE") or "").strip().lower()
    return industry in _POOLED_INDUSTRY_GROUPS


# ── Quarter parsing ────────────────────────────────────────────────────────────

def _read_tsv(path):
    try:
        with open(path, encoding="utf-8", errors="replace", newline="") as fh:
            return list(csv.DictReader(fh, delimiter="\t"))
    except Exception as exc:
        log.warning(f"[FORMD] | read failed | {path} | {exc}")
        return []


def parse_form_d_quarter(quarter_dir):
    """
    Join the three Form D tables in quarter_dir on ACCESSIONNUMBER and return
    filtered records: [{issuer_name, filing_date, amount, cik}].
    Best-effort: a missing or malformed quarter yields [] rather than raising.
    """
    try:
        submissions = {
            r.get("ACCESSIONNUMBER"): r
            for r in _read_tsv(os.path.join(quarter_dir, SUBMISSION_FILE))
        }
        offerings = {
            r.get("ACCESSIONNUMBER"): r
            for r in _read_tsv(os.path.join(quarter_dir, OFFERING_FILE))
        }
        issuers = _read_tsv(os.path.join(quarter_dir, ISSUERS_FILE))
    except Exception as exc:
        log.warning(f"[FORMD] | quarter parse failed | {quarter_dir} | {exc}")
        return []

    records = []
    for issuer in issuers:
        try:
            # IS_PRIMARYISSUER_FLAG is 'YES'/'NO' in the real data, not 'Y'/'N'.
            if (issuer.get("IS_PRIMARYISSUER_FLAG") or "").strip().upper() != "YES":
                continue
            accession = issuer.get("ACCESSIONNUMBER")
            submission = submissions.get(accession)
            offering = offerings.get(accession)
            if not submission or not offering:
                continue
            if (submission.get("TESTORLIVE") or "").strip().upper() != "LIVE":
                continue
            if _is_pooled_fund(offering):
                continue

            amount = parse_amount(offering.get("TOTALAMOUNTSOLD"))
            if amount is None:
                continue
            filing_date = parse_filing_date(submission.get("FILING_DATE"))
            if filing_date is None:
                continue
            name = (issuer.get("ENTITYNAME") or "").strip()
            if not name:
                continue

            records.append({
                "issuer_name": name,
                "filing_date": filing_date,
                "amount": amount,
                "cik": (issuer.get("CIK") or "").strip(),
            })
        except Exception as exc:
            log.warning(f"[FORMD] | row skipped | {exc}")

    log.info(f"[FORMD] | quarter parsed | {quarter_dir} | kept={len(records)}")
    return records


# ── Aggregation ────────────────────────────────────────────────────────────────

def fold_issuer(accumulator, record):
    """
    Accumulate one record, keeping the latest raise per normalized issuer name.
    Ties on filing date break toward the larger amount.
    """
    try:
        name = record.get("issuer_name") or ""
        key = entity_resolution.normalize(name)
        if not key:
            return
        current = accumulator.get(key)
        candidate = (record.get("filing_date") or "", record.get("amount") or 0)
        if current is not None:
            existing = (current.get("last_funding_date") or "",
                        current.get("last_funding_amount") or 0)
            if candidate <= existing:
                return
        accumulator[key] = {
            "normalized_name": key,
            "issuer_name": name,
            "last_funding_date": record.get("filing_date"),
            "last_funding_amount": record.get("amount"),
            "last_funding_source": FUNDING_SOURCE,
            "cik": record.get("cik"),
        }
    except Exception as exc:
        log.warning(f"[FORMD] | fold skipped | {exc}")


def build_rows_for_upsert(accumulator):
    """Return accumulator rows sorted by funding date, newest first."""
    rows = list(accumulator.values())
    rows.sort(key=lambda r: (r.get("last_funding_date") or "",
                             r.get("last_funding_amount") or 0), reverse=True)
    return rows


# ── Link discovery ─────────────────────────────────────────────────────────────

# The path prefix drifts between quarters: the newest quarter has been observed
# under /files/datastandardsinnovation/ while older ones sit under
# /files/structureddata/ (confirmed on the live index page 2026-08-19). Match on
# the FILENAME pattern only -- hardcoding either prefix silently breaks
# ingestion the next time SEC reorganises, which is exactly how the DOL LCA
# link discovery broke in production (see git history).
_FORM_D_ZIP_RE = re.compile(
    r'href="([^"]*/((?:19|20)\d{2}q[1-4])_d\.zip)"',
    re.IGNORECASE,
)


def _absolute_url(url):
    return url if url.startswith("http") else "https://www.sec.gov" + url


def parse_form_d_links(html):
    """
    Pure parser: given the SEC Form D data-sets index HTML, return
    {quarter_label: absolute_url}. Never raises -- a page-structure change
    yields an empty or partial dict, not a crash.
    """
    found = {}
    try:
        for match in _FORM_D_ZIP_RE.finditer(html or ""):
            url, quarter = match.groups()
            found[quarter.lower()] = _absolute_url(url)
    except Exception as exc:
        log.warning(f"[FORMD] | link parse failed | {exc}")
    return found


def discover_form_d_urls():
    """Best-effort scrape of the SEC index page. Returns {} on any failure."""
    try:
        import urllib.request
        request = urllib.request.Request(
            INDEX_URL, headers={"User-Agent": _USER_AGENT})
        with urllib.request.urlopen(request, timeout=60) as response:
            html = response.read().decode("utf-8", errors="replace")
    except Exception as exc:
        log.warning(f"[FORMD] | index fetch failed | {exc}")
        return {}

    links = parse_form_d_links(html)
    log.info(f"[FORMD] | link discovery | quarters_found={len(links)}")
    return links
