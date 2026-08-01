"""
Shared entity-resolution-to-company_intel-row logic, used by both the daily
incremental matcher (visa_match_new.py) and the quarterly ingestion job's
full re-match pass (ingest_oflc_lca.py).

Governance rule (mirrors the company_intel migration comment): this module
only ever writes sponsors_h1b as NULL or True. A human "confirmed" decision
via the match review screen is the only path to a confirmed match_status,
and confirmed/rejected rows are never overwritten by a re-match here --
only a confirmed row's denormalized stats are refreshed, in case the linked
employer's numbers changed since the last ingest.
"""
import entity_resolution


def resolve_company(raw_company_name, existing_row, employer_corpus):
    """
    raw_company_name: a contacts.company string.
    existing_row: the current company_intel row for this normalized name
        (dict, from db.get_company_intel_by_normalized_names), or None.
    employer_corpus: list of employer_h1b_stats dicts
        (id, normalized_name, lca_recent_2fy, latest_filing_fy, approval_rate),
        from db.get_employer_h1b_stats_corpus.

    Returns a dict ready for db.upsert_company_intel, or None if there is
    nothing to write.
    """
    normalized = entity_resolution.normalize(raw_company_name)
    if not normalized:
        return None

    by_id = {e["id"]: e for e in employer_corpus}
    by_normalized_name = {e["normalized_name"]: e for e in employer_corpus}

    if existing_row and existing_row.get("match_status") in ("confirmed", "rejected"):
        return _refresh_confirmed_row(existing_row, by_id)

    raw_names = set((existing_row or {}).get("raw_company_names") or [])
    raw_names.add(raw_company_name)

    corpus_names = list(by_normalized_name.keys())
    candidates = entity_resolution.resolve(normalized, corpus_names)
    status, top = entity_resolution.classify(normalized, candidates)

    row = {
        "normalized_name": normalized,
        "raw_company_names": sorted(raw_names),
        "match_status": status,
        "top_candidates": _build_top_candidates(candidates, by_normalized_name),
    }

    if status == "unknown":
        row["matched_employer_id"] = None
        row["match_confidence"] = None
        row["sponsors_h1b"] = None
        row["h1b_recent_count"] = None
        row["latest_filing_fy"] = None
        row["approval_rate"] = None
        return row

    row["match_confidence"] = top.score
    matched_employer = by_normalized_name.get(top.normalized_name)

    if status == "needs_review":
        row["matched_employer_id"] = None
        row["sponsors_h1b"] = None
        row["h1b_recent_count"] = None
        row["latest_filing_fy"] = None
        row["approval_rate"] = None
        return row

    # status == "auto" -- every employer_h1b_stats row is materiality-filtered
    # at ingestion time (lca_recent_2fy >= 1), so an auto match always implies
    # a real, recent sponsor.
    row["matched_employer_id"] = matched_employer["id"] if matched_employer else None
    row["sponsors_h1b"] = True if matched_employer else None
    row["h1b_recent_count"] = matched_employer.get("lca_recent_2fy") if matched_employer else None
    row["latest_filing_fy"] = matched_employer.get("latest_filing_fy") if matched_employer else None
    row["approval_rate"] = matched_employer.get("approval_rate") if matched_employer else None
    return row


def _refresh_confirmed_row(existing_row, by_id):
    if existing_row["match_status"] != "confirmed" or not existing_row.get("matched_employer_id"):
        return None  # rejected, or confirmed with no linked employer -- nothing to refresh

    employer = by_id.get(existing_row["matched_employer_id"])
    if employer is None:
        return None  # linked employer no longer present this run -- leave stats as-is

    return {
        "normalized_name": existing_row["normalized_name"],
        "h1b_recent_count": employer.get("lca_recent_2fy"),
        "latest_filing_fy": employer.get("latest_filing_fy"),
        "approval_rate": employer.get("approval_rate"),
    }


def _build_top_candidates(candidates, by_normalized_name):
    if not candidates:
        return None
    result = []
    for c in candidates[:3]:
        employer = by_normalized_name.get(c.normalized_name)
        result.append({
            "employer_id": employer["id"] if employer else None,
            "normalized_name": c.normalized_name,
            "score": c.score,
        })
    return result
