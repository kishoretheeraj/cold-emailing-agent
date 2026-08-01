"""
Ingests the USCIS H-1B Employer Data Hub bulk CSV and enriches EXISTING
employer_h1b_stats rows with approval/denial counts. Unlike
ingest_oflc_lca.py, this module never creates new employer_h1b_stats rows --
LCA presence is the primary "is this a target" signal (see CLAUDE.md); an
employer present in USCIS data but absent from the LCA-derived corpus is
logged and skipped, not backfilled.

Only confident ("auto") entity-resolution matches against the existing
employer_h1b_stats corpus are applied. Ambiguous USCIS employer names are
left unenriched (approval_rate stays NULL) rather than risking a wrong link
in a pipeline with no human-review touchpoint of its own.
"""
import csv
import logging
import time
import urllib.request
from datetime import datetime, timezone

import db
import entity_resolution

log = logging.getLogger(__name__)

DATA_HUB_PAGE_URL = "https://www.uscis.gov/tools/reports-and-studies/h-1b-employer-data-hub"
_USER_AGENT = (
    "Mozilla/5.0 (compatible; cold-email-agent-visa-intel/1.0; "
    "+https://github.com/)"
)

COLUMN_ALIASES = {
    "employer_name": ["Employer", "EMPLOYER_NAME", "Petitioner Name"],
    "initial_approval": ["Initial Approval", "Initial_Approval", "INITIAL_APPROVAL"],
    "initial_denial": ["Initial Denial", "Initial_Denial", "INITIAL_DENIAL"],
    "continuing_approval": ["Continuing Approval", "Continuing_Approval", "CONTINUING_APPROVAL"],
    "continuing_denial": ["Continuing Denial", "Continuing_Denial", "CONTINUING_DENIAL"],
    "naics_code": ["NAICS", "NAICS_CODE", "Industry (NAICS) Code"],
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


def _to_int(raw):
    try:
        return int(str(raw).strip().replace(",", ""))
    except (TypeError, ValueError):
        return 0


def parse_datahub_csv(rows_iter, accumulator):
    """
    rows_iter: an iterable of raw CSV rows (list-of-cells), first row is the
    header. accumulator: dict keyed by raw employer name -> summed counts.
    Raises MissingRequiredColumnError if employer_name can't be resolved.
    """
    rows_iter = iter(rows_iter)
    header_row = next(rows_iter, None)
    if header_row is None:
        raise MissingRequiredColumnError("empty file, no header row")

    resolved, missing_required = resolve_columns(header_row)
    if missing_required:
        raise MissingRequiredColumnError(f"could not resolve required column(s): {sorted(missing_required)}")

    rows_folded = 0
    for row in rows_iter:
        def _cell(field):
            idx = resolved.get(field)
            if idx is None or idx >= len(row):
                return None
            return row[idx]

        employer_name = _cell("employer_name")
        if not employer_name:
            continue

        entry = accumulator.setdefault(employer_name, {
            "approvals": 0, "denials": 0, "naics_code": None,
        })
        entry["approvals"] += _to_int(_cell("initial_approval")) + _to_int(_cell("continuing_approval"))
        entry["denials"] += _to_int(_cell("initial_denial")) + _to_int(_cell("continuing_denial"))
        if entry["naics_code"] is None and _cell("naics_code"):
            entry["naics_code"] = str(_cell("naics_code")).strip()
        rows_folded += 1

    return rows_folded


def build_enrichment_rows(accumulator, existing_corpus):
    """
    Resolves each raw USCIS employer name against the existing
    employer_h1b_stats corpus (list of {"id","normalized_name"} dicts).
    Only confident ("auto") matches are enriched -- see module docstring.
    Returns a list of partial-column row dicts ready for db.upsert_employer_h1b_stats
    (never creates new rows, since every normalized_name here already exists
    in existing_corpus by construction).
    """
    corpus_names = [row["normalized_name"] for row in existing_corpus]
    ingested_at = datetime.now(timezone.utc).isoformat()

    matched = {}  # normalized_name -> {approvals, denials, naics_code}
    unmatched_count = 0

    for raw_name, counts in accumulator.items():
        normalized = entity_resolution.canonicalize_alias_group(
            entity_resolution.normalize(raw_name)
        )
        if not normalized:
            continue

        candidates = entity_resolution.resolve(normalized, corpus_names)
        status, top = entity_resolution.classify(normalized, candidates)
        if status != "auto":
            unmatched_count += 1
            continue

        target = matched.setdefault(top.normalized_name, {"approvals": 0, "denials": 0, "naics_code": None})
        target["approvals"] += counts["approvals"]
        target["denials"] += counts["denials"]
        if target["naics_code"] is None and counts["naics_code"]:
            target["naics_code"] = counts["naics_code"]

    if unmatched_count:
        log.info(f"[RESEARCH-C] visa_intel | uscis_datahub | unmatched_employers={unmatched_count}")

    rows = []
    for normalized_name, counts in matched.items():
        total = counts["approvals"] + counts["denials"]
        approval_rate = round(counts["approvals"] / total, 4) if total > 0 else None
        rows.append({
            "normalized_name": normalized_name,
            "uscis_approvals": counts["approvals"],
            "uscis_denials": counts["denials"],
            "approval_rate": approval_rate,
            "naics_code": counts["naics_code"],
            "source_vintages": {"uscis_datahub": {"ingested_at": ingested_at}},
        })
    return rows


# ── Orchestration (network I/O, not unit-tested) ─────────────────────────────────

def download_file(url, dest_path):
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = resp.read()
    with open(dest_path, "wb") as out:
        out.write(data)


def run(csv_url=None):
    start = time.time()
    if not csv_url:
        log.warning(
            "[RESEARCH-C] visa_intel | uscis_datahub | no CSV URL configured, "
            "skipping (see DATA_HUB_PAGE_URL for the manual download page)"
        )
        db.record_run("failure", 0, 0, 1, time.time() - start,
                       failure_reason="no USCIS Data Hub CSV URL configured", source="visa_ingest_uscis")
        return

    existing_corpus = db.get_employer_h1b_stats_corpus()
    if not existing_corpus:
        log.warning("[RESEARCH-C] visa_intel | uscis_datahub | employer_h1b_stats is empty, "
                    "run ingest_oflc_lca.py first")
        db.record_run("failure", 0, 0, 1, time.time() - start,
                       failure_reason="employer_h1b_stats empty, nothing to enrich", source="visa_ingest_uscis")
        return

    import tempfile
    import os
    accumulator = {}
    errors = 0
    with tempfile.TemporaryDirectory() as tmpdir:
        dest = os.path.join(tmpdir, "uscis_datahub.csv")
        try:
            download_file(csv_url, dest)
            with open(dest, newline="", encoding="utf-8-sig") as f:
                rows_folded = parse_datahub_csv(csv.reader(f), accumulator)
            log.info(f"[RESEARCH-C] visa_intel | uscis_datahub | rows_folded={rows_folded}")
        except Exception as exc:
            log.warning(f"[RESEARCH-C] visa_intel | uscis_datahub | ingest failed: {exc}")
            errors += 1

    if not accumulator:
        db.record_run("failure" if errors else "success", 0, 0, errors, time.time() - start,
                       source="visa_ingest_uscis")
        return

    rows = build_enrichment_rows(accumulator, existing_corpus)
    batch_size = 500
    for i in range(0, len(rows), batch_size):
        db.upsert_employer_h1b_stats(rows[i:i + batch_size])

    log.info(f"[RESEARCH-C] visa_intel | uscis_datahub | DONE | enriched={len(rows)} | errors={errors}")
    db.record_run("success", len(rows), 0, errors, time.time() - start, source="visa_ingest_uscis")


if __name__ == "__main__":
    logging.basicConfig(
        filename="visa_intel_ingest.log",
        level=logging.INFO,
        format="%(asctime)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M",
    )
    run()
