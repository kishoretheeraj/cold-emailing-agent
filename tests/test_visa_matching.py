import visa_matching as vm


CORPUS = [
    {"id": 1, "normalized_name": "acme", "lca_recent_2fy": 12, "latest_filing_fy": 2025, "approval_rate": 0.95},
]


def test_resolve_company_new_auto_match():
    row = vm.resolve_company("Acme Inc.", existing_row=None, employer_corpus=CORPUS)
    assert row["normalized_name"] == "acme"
    assert row["match_status"] == "auto"
    assert row["matched_employer_id"] == 1
    assert row["sponsors_h1b"] is True
    assert row["h1b_recent_count"] == 12
    assert row["raw_company_names"] == ["Acme Inc."]


def test_resolve_company_no_candidate_is_unknown_never_false():
    # Governance: absence from the corpus must resolve to unknown, never a
    # false "doesn't sponsor" result.
    row = vm.resolve_company("Some Obscure Startup", existing_row=None, employer_corpus=CORPUS)
    assert row["match_status"] == "unknown"
    assert row["sponsors_h1b"] is None
    assert row["matched_employer_id"] is None


def test_resolve_company_needs_review_never_sets_sponsors_true():
    corpus = [{"id": 2, "normalized_name": "acme industries", "lca_recent_2fy": 3,
               "latest_filing_fy": 2025, "approval_rate": 0.5}]
    row = vm.resolve_company("Acme Indst", existing_row=None, employer_corpus=corpus)
    assert row["match_status"] == "needs_review"
    assert row["sponsors_h1b"] is None
    assert row["matched_employer_id"] is None
    assert row["top_candidates"]


def test_resolve_company_empty_name_returns_none():
    assert vm.resolve_company("", existing_row=None, employer_corpus=CORPUS) is None


def test_resolve_company_accumulates_raw_names_across_calls():
    existing = {"normalized_name": "acme", "match_status": "needs_review", "raw_company_names": ["ACME LLC"]}
    row = vm.resolve_company("Acme Inc.", existing_row=existing, employer_corpus=CORPUS)
    assert set(row["raw_company_names"]) == {"ACME LLC", "Acme Inc."}


# ── governance: confirmed/rejected rows are never overwritten by re-match ───────

def test_resolve_company_confirmed_row_is_never_reclassified():
    existing = {
        "normalized_name": "acme",
        "match_status": "confirmed",
        "matched_employer_id": 1,
    }
    row = vm.resolve_company("Acme Inc.", existing_row=existing, employer_corpus=CORPUS)
    assert "match_status" not in row  # untouched -- stays "confirmed" in the DB
    assert "sponsors_h1b" not in row  # untouched -- stays whatever the human set


def test_resolve_company_confirmed_row_refreshes_denormalized_stats():
    existing = {"normalized_name": "acme", "match_status": "confirmed", "matched_employer_id": 1}
    updated_corpus = [{"id": 1, "normalized_name": "acme", "lca_recent_2fy": 999,
                        "latest_filing_fy": 2026, "approval_rate": 0.99}]
    row = vm.resolve_company("Acme Inc.", existing_row=existing, employer_corpus=updated_corpus)
    assert row["h1b_recent_count"] == 999
    assert row["latest_filing_fy"] == 2026
    assert row["approval_rate"] == 0.99


def test_resolve_company_rejected_row_is_left_alone():
    existing = {"normalized_name": "acme", "match_status": "rejected", "matched_employer_id": None}
    row = vm.resolve_company("Acme Inc.", existing_row=existing, employer_corpus=CORPUS)
    assert row is None


def test_resolve_company_confirmed_row_with_missing_employer_is_left_alone():
    # The linked employer dropped out of this run's corpus (e.g. fell below
    # the materiality filter) -- don't crash, don't null out human-set stats.
    existing = {"normalized_name": "acme", "match_status": "confirmed", "matched_employer_id": 999}
    row = vm.resolve_company("Acme Inc.", existing_row=existing, employer_corpus=CORPUS)
    assert row is None
