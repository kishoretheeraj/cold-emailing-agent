"""
Ingests DOL OFLC LCA disclosure files, aggregates per-employer H-1B filing
stats, and upserts them into employer_h1b_stats.

DOL publishes no official manifest of per-fiscal-year download URLs or
filenames (column names and file naming both drift year to year) -- link
discovery below is a best-effort HTML scrape of the performance data page,
not a hardcoded URL list. Validate discovered links against
https://www.dol.gov/agencies/eta/foreign-labor/performance before trusting
a fresh fiscal year's ingest.
"""
import io
import logging
import re
import time
import urllib.request
import zipfile
from datetime import datetime, timezone

import openpyxl

import db
import entity_resolution

log = logging.getLogger(__name__)

PERFORMANCE_PAGE_URL = "https://www.dol.gov/agencies/eta/foreign-labor/performance"
_USER_AGENT = (
    "Mozilla/5.0 (compatible; cold-email-agent-visa-intel/1.0; "
    "+https://github.com/)"
)

# Last ~4 fiscal years only for the first/steady-state ingest -- see CLAUDE.md
# for why (recency is what the sponsorship signal actually needs; the
# full FY2008+ backfill is explicitly out of scope for Stage 1).
DEFAULT_FISCAL_YEARS = 4

MIN_LCA_RECENT_2FY = 1
MAX_EMPLOYER_ROWS = 150000

# ── Column drift mapping ─────────────────────────────────────────────────────────

# Canonical field -> known raw header spellings across FY vintages. Only
# employer_name is required; every other field degrades gracefully when
# unresolvable for a given file.
COLUMN_ALIASES = {
    "employer_name": ["EMPLOYER_NAME", "Employer Name", "EMPLOYER (Petitioner) Name"],
    "soc_code": ["SOC_CODE", "PW_SOC_CODE", "SOC_CODE_TITLE"],
    "worksite_state": ["WORKSITE_STATE", "WORKSITE_STATE_1", "worksite_state"],
    "wage_level": ["PW_WAGE_LEVEL", "PW_WAGE_LEVEL_9089", "WAGE_LEVEL"],
    "case_status": ["CASE_STATUS", "STATUS"],
    # Since FY2020, DOL's combined "LCA" disclosure file covers H-1B, H-1B1
    # (Chile/Singapore treaty visa), and E-3 (Australia treaty visa) together
    # -- confirmed against the live FY2026 file, ~3% of rows are H-1B1/E-3.
    # This module is H-1B specifically; rows are filtered by this column when
    # present. Pre-2020 vintages predate the consolidation and have no such
    # column -- absence degrades to "don't filter," not an error.
    "visa_class": ["VISA_CLASS"],
}

REQUIRED_FIELDS = {"employer_name"}


class MissingRequiredColumnError(Exception):
    pass


def resolve_columns(header_row):
    normalized_headers = {}
    for idx, header in enumerate(header_row):
        if header is None:
            continue
        normalized_headers[str(header).strip().upper()] = idx

    resolved = {}
    for canonical, variants in COLUMN_ALIASES.items():
        for variant in variants:
            key = variant.strip().upper()
            if key in normalized_headers:
                resolved[canonical] = normalized_headers[key]
                break

    missing_required = REQUIRED_FIELDS - resolved.keys()
    return resolved, missing_required


# ── Value normalization ─────────────────────────────────────────────────────────

_COUNTED_STATUSES = {"certified", "certified_withdrawn"}


def normalize_case_status(raw):
    if not raw:
        return "unknown"
    s = str(raw).strip().lower().replace("-", "_").replace(" ", "_")
    if "certified" in s and "withdrawn" in s:
        return "certified_withdrawn"
    if "certified" in s:
        return "certified"
    if "denied" in s:
        return "denied"
    if "withdrawn" in s:
        return "withdrawn"
    return "unknown"


def normalize_wage_level(raw):
    if not raw:
        return "unknown"
    s = str(raw).strip().upper()
    if s in ("I", "1", "LEVEL I", "LEVEL 1"):
        return "I"
    if s in ("II", "2", "LEVEL II", "LEVEL 2"):
        return "II"
    if s in ("III", "3", "LEVEL III", "LEVEL 3"):
        return "III"
    if s in ("IV", "4", "LEVEL IV", "LEVEL 4"):
        return "IV"
    return "unknown"


# ── Aggregation ──────────────────────────────────────────────────────────────────

def _new_accumulator_entry():
    return {
        "display_names": {},
        "lca_total": 0,
        "lca_by_fy": {},
        "socs": set(),
        "worksite_states": set(),
        "wage_level_counts": {"I": 0, "II": 0, "III": 0, "IV": 0, "unknown": 0},
    }


def fold_row(accumulator, raw_employer_name, fiscal_year, soc_code=None,
             worksite_state=None, wage_level=None):
    normalized = entity_resolution.canonicalize_alias_group(
        entity_resolution.normalize(raw_employer_name)
    )
    if not normalized:
        return

    entry = accumulator.setdefault(normalized, _new_accumulator_entry())
    entry["display_names"][raw_employer_name] = entry["display_names"].get(raw_employer_name, 0) + 1
    entry["lca_total"] += 1
    entry["lca_by_fy"][fiscal_year] = entry["lca_by_fy"].get(fiscal_year, 0) + 1
    if soc_code:
        entry["socs"].add(str(soc_code).strip())
    if worksite_state:
        entry["worksite_states"].add(str(worksite_state).strip().upper())
    entry["wage_level_counts"][normalize_wage_level(wage_level)] += 1


def parse_lca_file(path, fiscal_year, accumulator):
    """
    Streams one LCA disclosure workbook into accumulator (dict keyed by
    normalized employer name). Raises MissingRequiredColumnError if
    employer_name can't be resolved -- that's the one field a caller should
    treat as a hard failure for this file. Every other field degrades.
    """
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        ws = wb.active
        rows = ws.iter_rows(values_only=True)
        header_row = next(rows, None)
        if header_row is None:
            raise MissingRequiredColumnError(f"{path}: empty file, no header row")

        resolved, missing_required = resolve_columns(header_row)
        if missing_required:
            raise MissingRequiredColumnError(
                f"{path}: could not resolve required column(s): {sorted(missing_required)}"
            )

        rows_folded = 0
        for row in rows:
            employer_name = row[resolved["employer_name"]] if resolved["employer_name"] < len(row) else None
            if not employer_name:
                continue

            def _cell(field):
                idx = resolved.get(field)
                if idx is None or idx >= len(row):
                    return None
                return row[idx]

            status = normalize_case_status(_cell("case_status"))
            if "case_status" in resolved and status not in _COUNTED_STATUSES:
                continue

            visa_class = _cell("visa_class")
            if "visa_class" in resolved and visa_class is not None:
                visa_class_str = str(visa_class).strip()
                if visa_class_str and visa_class_str.upper() != "H-1B":
                    continue

            fold_row(
                accumulator,
                raw_employer_name=employer_name,
                fiscal_year=fiscal_year,
                soc_code=_cell("soc_code"),
                worksite_state=_cell("worksite_state"),
                wage_level=_cell("wage_level"),
            )
            rows_folded += 1

        return rows_folded
    finally:
        wb.close()


def build_rows_for_upsert(accumulator, ingested_fiscal_years,
                           min_recent=MIN_LCA_RECENT_2FY, max_rows=MAX_EMPLOYER_ROWS):
    recent_fys = sorted(set(ingested_fiscal_years), reverse=True)[:2]
    ingested_at = datetime.now(timezone.utc).isoformat()

    rows = []
    for normalized_name, entry in accumulator.items():
        lca_recent_2fy = sum(entry["lca_by_fy"].get(fy, 0) for fy in recent_fys)
        if lca_recent_2fy < min_recent:
            continue

        display_name = max(entry["display_names"].items(), key=lambda kv: kv[1])[0]
        latest_filing_fy = max(entry["lca_by_fy"]) if entry["lca_by_fy"] else None

        rows.append({
            "normalized_name": normalized_name,
            "display_name": display_name,
            "aliases": sorted(entry["display_names"].keys()),
            "lca_total": entry["lca_total"],
            "lca_recent_2fy": lca_recent_2fy,
            "distinct_socs": len(entry["socs"]),
            "latest_filing_fy": latest_filing_fy,
            "worksite_states": sorted(entry["worksite_states"]),
            "wage_level_dist": entry["wage_level_counts"],
            "source_vintages": {
                "oflc_lca": {"fy": sorted(set(ingested_fiscal_years)), "ingested_at": ingested_at},
            },
        })

    rows.sort(key=lambda r: (r["lca_recent_2fy"], r["lca_total"]), reverse=True)
    return rows[:max_rows]


# ── Link discovery + download ────────────────────────────────────────────────────

# DOL's naming convention for this file series has changed over time -- the
# fiscal year lives in the FILENAME, not the surrounding link text (an earlier
# version of this regex assumed the latter and crashed in production, see
# git history). Since FY2020, H-1B/H-1B1/E-3 disclosures are published as one
# combined "LCA_Disclosure_Data" series, quarterly-cumulative
# (Q4 = full fiscal year). Pre-2020 vintages used a separate "H-1B_Disclosure_Data"
# / "H-1B_Case_Data" naming scheme with no quarter suffix. Confirmed against
# the live page on 2026-08-10 -- re-verify here if ingestion coverage looks
# thin, since DOL publishes no official manifest for either series.
# DOL's own site has shipped "LCA_Dislclosure_Data" (missing the "c") for at
# least one live quarter's link -- tolerate the typo, confirmed against the
# live page on 2026-08-10.
_LCA_QUARTERLY_RE = re.compile(
    r'href="([^"]*LCA_Dis(?:l)?closure_Data_FY(\d{4})_Q(\d)\.xlsx)"',
    re.IGNORECASE,
)
_LCA_ANNUAL_RE = re.compile(
    r'href="([^"]*H-1B_(?:Disclosure|Case)_Data_FY(\d{4})[^"]*\.xlsx)"',
    re.IGNORECASE,
)


def _absolute_url(url):
    return url if url.startswith("http") else "https://www.dol.gov" + url


def parse_lca_links(html):
    """
    Pure parser: given the DOL performance-data page HTML, returns
    {fiscal_year: url} for the best (most-cumulative) LCA disclosure file per
    FY found in the page. Never raises -- a page-structure change should
    yield an empty/partial dict, not crash the caller.
    """
    best_by_fy = {}  # fy -> (quarter, url), quarter=99 for the annual-only series

    for match in _LCA_QUARTERLY_RE.finditer(html):
        url, year_str, quarter_str = match.groups()
        fy, quarter = int(year_str), int(quarter_str)
        existing = best_by_fy.get(fy)
        if existing is None or quarter > existing[0]:
            best_by_fy[fy] = (quarter, _absolute_url(url))

    for match in _LCA_ANNUAL_RE.finditer(html):
        url, year_str = match.groups()
        fy = int(year_str)
        if fy not in best_by_fy:
            best_by_fy[fy] = (99, _absolute_url(url))

    return {fy: url for fy, (_, url) in best_by_fy.items()}


def discover_lca_file_urls():
    """
    Best-effort scrape of the DOL performance data page for per-fiscal-year
    LCA disclosure file links. Returns {fiscal_year: url}, empty dict on any
    failure (network error, page structure change) -- never raises. DOL
    publishes no manifest, so this must be treated as a heuristic and
    spot-checked against the live page whenever ingestion coverage looks off.
    """
    try:
        req = urllib.request.Request(PERFORMANCE_PAGE_URL, headers={"User-Agent": _USER_AGENT})
        with urllib.request.urlopen(req, timeout=30) as resp:
            html = resp.read().decode("utf-8", errors="ignore")
        return parse_lca_links(html)
    except Exception as exc:
        log.warning(f"[RESEARCH-C] visa_intel | discover_lca_file_urls failed: {exc}")
        return {}


def download_file(url, dest_path):
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = resp.read()
    if url.lower().endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            names = [n for n in zf.namelist() if n.lower().endswith((".xlsx", ".xls"))]
            if not names:
                raise ValueError(f"No spreadsheet found inside zip: {url}")
            with zf.open(names[0]) as f, open(dest_path, "wb") as out:
                out.write(f.read())
    else:
        with open(dest_path, "wb") as out:
            out.write(data)


# ── Orchestration ─────────────────────────────────────────────────────────────────

def current_dol_fiscal_year(today=None):
    # DOL fiscal years run Oct 1 - Sep 30 (FY2026 = Oct 2025 - Sep 2026).
    today = today or datetime.now(timezone.utc)
    return today.year + 1 if today.month >= 10 else today.year


def run(fiscal_years_back=DEFAULT_FISCAL_YEARS):
    start = time.time()
    current_fy = current_dol_fiscal_year()
    target_fys = list(range(current_fy - fiscal_years_back + 1, current_fy + 1))

    urls_by_fy = discover_lca_file_urls()
    accumulator = {}
    ingested_fys = []
    errors = 0

    for fy in target_fys:
        url = urls_by_fy.get(fy)
        if not url:
            log.warning(f"[RESEARCH-C] visa_intel | FY{fy} | no LCA file link discovered, skipping")
            errors += 1
            continue

        import tempfile
        import os
        with tempfile.TemporaryDirectory() as tmpdir:
            dest = os.path.join(tmpdir, f"oflc_lca_fy{fy}.xlsx")
            try:
                download_file(url, dest)
                rows_folded = parse_lca_file(dest, fy, accumulator)
                ingested_fys.append(fy)
                log.info(f"[RESEARCH-C] visa_intel | FY{fy} | rows_folded={rows_folded}")
            except MissingRequiredColumnError as exc:
                log.warning(f"[RESEARCH-C] visa_intel | FY{fy} | aborted, required column missing: {exc}")
                errors += 1
            except Exception as exc:
                log.warning(f"[RESEARCH-C] visa_intel | FY{fy} | ingest failed: {exc}")
                errors += 1

    if not ingested_fys:
        log.warning("[RESEARCH-C] visa_intel | no fiscal years ingested, nothing to upsert")
        db.record_run("failure", 0, 0, errors, round(time.time() - start),
                       failure_reason="no LCA fiscal years ingested", source="visa_ingest_lca")
        return

    rows = build_rows_for_upsert(accumulator, ingested_fys)
    batch_size = 500
    for i in range(0, len(rows), batch_size):
        db.upsert_employer_h1b_stats(rows[i:i + batch_size])

    status = "success" if errors == 0 else "success"  # partial FY misses are non-fatal
    log.info(
        f"[RESEARCH-C] visa_intel | DONE | fys={ingested_fys} | employers={len(rows)} | errors={errors}"
    )
    db.record_run(status, len(rows), 0, errors, round(time.time() - start), source="visa_ingest_lca")


if __name__ == "__main__":
    logging.basicConfig(
        filename="visa_intel_ingest.log",
        level=logging.INFO,
        format="%(asctime)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M",
    )
    run()
