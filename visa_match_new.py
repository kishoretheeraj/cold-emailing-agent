"""
Visa-intel matcher. Two modes:

- Incremental (default, daily): links any contact whose company_intel_id is
  still NULL against the already-materialized employer_h1b_stats corpus.
  Deliberately separate from agent.py::run() -- see CLAUDE.md. Wired as a
  `continue-on-error: true` step in daily_agent.yml so a bug here can never
  fail the outbound-email workflow. No Claude/Gmail calls, cheap even at
  scale (a handful of new companies per day at most).

- Full re-match (--full, quarterly): re-resolves EVERY distinct contact
  company against the freshly-refreshed corpus, not just unmatched ones --
  needed after employer_h1b_stats changes, since a company that resolved to
  "unknown" against last quarter's corpus may now match. Governance is
  enforced in visa_matching.resolve_company: confirmed/rejected rows are
  never reclassified, only their denormalized stats refresh.
"""
import logging
import sys
import time

import db
import visa_matching

log = logging.getLogger(__name__)


def _distinct_unmatched_companies(contacts):
    by_company = {}
    for c in contacts:
        if c.get("company_intel_id"):
            continue
        company = (c.get("company") or "").strip()
        if not company:
            continue
        by_company.setdefault(company, []).append(c["id"])
    return by_company


def _distinct_all_companies(contacts):
    by_company = {}
    for c in contacts:
        company = (c.get("company") or "").strip()
        if not company:
            continue
        by_company.setdefault(company, []).append(c["id"])
    return by_company


def run(full_rematch=False):
    start = time.time()
    matched = 0
    errors = 0

    try:
        contacts = db.get_all_contacts()
    except Exception as exc:
        log.warning(f"[RESEARCH-C] visa_match | get_all_contacts failed: {exc}")
        db.record_run("failure", 0, 0, 1, round(time.time() - start),
                       failure_reason=str(exc), source="visa_match")
        return

    by_company = (_distinct_all_companies if full_rematch else _distinct_unmatched_companies)(contacts)
    if not by_company:
        log.info("[RESEARCH-C] visa_match | no companies to process, nothing to do")
        db.record_run("success", 0, 0, 0, round(time.time() - start), source="visa_match")
        return

    try:
        employer_corpus = db.get_employer_h1b_stats_corpus()
    except Exception as exc:
        log.warning(f"[RESEARCH-C] visa_match | employer_h1b_stats read failed: {exc}")
        db.record_run("failure", 0, 0, 1, round(time.time() - start),
                       failure_reason=str(exc), source="visa_match")
        return

    for company, contact_ids in by_company.items():
        try:
            normalized = _normalize_for_lookup(company)
            existing_rows = db.get_company_intel_by_normalized_names([normalized]) if normalized else []
            existing_row = existing_rows[0] if existing_rows else None

            row = visa_matching.resolve_company(company, existing_row, employer_corpus)
            if row is None:
                continue

            if not db.upsert_company_intel([row]):
                errors += 1
                continue

            linked_rows = db.get_company_intel_by_normalized_names([row["normalized_name"]])
            if not linked_rows:
                errors += 1
                continue
            company_intel_id = linked_rows[0]["id"]

            for contact_id in contact_ids:
                if not db.update_contact_company_intel_id(contact_id, company_intel_id):
                    errors += 1
            matched += 1
        except Exception as exc:
            log.warning(f"[RESEARCH-C] visa_match | {company} | match failed: {exc}")
            errors += 1

    log.info(f"[RESEARCH-C] visa_match | DONE | companies_matched={matched} | errors={errors}")
    status = "success" if errors == 0 else "success"  # per-company failures are non-fatal
    db.record_run(status, matched, 0, errors, round(time.time() - start), source="visa_match")


def _normalize_for_lookup(company):
    import entity_resolution
    return entity_resolution.canonicalize_alias_group(entity_resolution.normalize(company))


if __name__ == "__main__":
    logging.basicConfig(
        filename="visa_match.log",
        level=logging.INFO,
        format="%(asctime)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M",
    )
    run(full_rematch="--full" in sys.argv)
