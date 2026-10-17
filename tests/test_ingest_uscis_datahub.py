import pytest

import ingest_uscis_datahub as ingest


# ── resolve_columns ──────────────────────────────────────────────────────────────

def test_resolve_columns_current_headers():
    header = ["Employer", "Initial Approval", "Initial Denial", "Continuing Approval", "Continuing Denial", "NAICS"]
    resolved, missing = ingest.resolve_columns(header)
    assert missing == set()
    assert resolved["employer_name"] == 0


def test_resolve_columns_missing_required():
    header = ["Initial Approval", "Initial Denial"]
    resolved, missing = ingest.resolve_columns(header)
    assert missing == {"employer_name"}


# ── parse_datahub_csv ─────────────────────────────────────────────────────────────

def test_parse_datahub_csv_sums_initial_and_continuing():
    rows = [
        ["Employer", "Initial Approval", "Initial Denial", "Continuing Approval", "Continuing Denial", "NAICS"],
        ["Acme Inc.", "10", "2", "5", "1", "5415"],
        ["Acme Inc.", "3", "0", "1", "0", "5415"],
    ]
    acc = {}
    rows_folded = ingest.parse_datahub_csv(rows, acc)
    assert rows_folded == 2
    entry = acc["Acme Inc."]
    assert entry["approvals"] == 10 + 5 + 3 + 1
    assert entry["denials"] == 2 + 1
    assert entry["naics_code"] == "5415"


def test_parse_datahub_csv_handles_comma_thousands_separators():
    rows = [
        ["Employer", "Initial Approval", "Initial Denial", "Continuing Approval", "Continuing Denial"],
        ["Big Corp", "1,234", "56", "789", "0"],
    ]
    acc = {}
    ingest.parse_datahub_csv(rows, acc)
    assert acc["Big Corp"]["approvals"] == 1234 + 789


def test_parse_datahub_csv_missing_required_column_raises():
    rows = [["Initial Approval", "Initial Denial"], ["10", "2"]]
    with pytest.raises(ingest.MissingRequiredColumnError):
        ingest.parse_datahub_csv(rows, {})


def test_parse_datahub_csv_empty_file_raises():
    with pytest.raises(ingest.MissingRequiredColumnError):
        ingest.parse_datahub_csv([], {})


# ── build_enrichment_rows ─────────────────────────────────────────────────────────

def test_build_enrichment_rows_only_applies_confident_matches():
    accumulator = {
        "Acme Inc.": {"approvals": 10, "denials": 2, "naics_code": "5415"},
        "Totally Unrelated Widgets Emporium": {"approvals": 1, "denials": 0, "naics_code": None},
    }
    existing_corpus = [{"id": 1, "normalized_name": "acme"}]

    rows = ingest.build_enrichment_rows(accumulator, existing_corpus)
    assert len(rows) == 1
    assert rows[0]["normalized_name"] == "acme"
    assert rows[0]["uscis_approvals"] == 10
    assert rows[0]["uscis_denials"] == 2
    assert rows[0]["approval_rate"] == pytest.approx(10 / 12, rel=1e-3)


def test_build_enrichment_rows_never_targets_a_name_outside_existing_corpus():
    # Governance: this module must never create new employer_h1b_stats rows.
    accumulator = {"Brand New Startup Inc": {"approvals": 5, "denials": 0, "naics_code": None}}
    existing_corpus = []  # nothing to match against

    rows = ingest.build_enrichment_rows(accumulator, existing_corpus)
    assert rows == []


def test_build_enrichment_rows_zero_total_has_null_approval_rate():
    accumulator = {"Acme Inc.": {"approvals": 0, "denials": 0, "naics_code": None}}
    existing_corpus = [{"id": 1, "normalized_name": "acme"}]
    rows = ingest.build_enrichment_rows(accumulator, existing_corpus)
    assert rows[0]["approval_rate"] is None


# ── run() ─────────────────────────────────────────────────────────────────────────

def test_run_counts_a_failed_enrichment_upsert_as_an_error(mocker, tmp_path):
    # upsert_employer_h1b_stats returns False on failure instead of raising; run() used to record
    # "enriched=N, errors=0" with nothing written.
    mocker.patch.object(ingest.db, "get_employer_h1b_stats_corpus",
                        return_value=[{"id": 1, "normalized_name": "acme"}])

    def fake_download(url, dest):
        with open(dest, "w") as fh:
            fh.write("Employer,Initial Approval,Initial Denial\nAcme Inc.,10,2\n")

    mocker.patch.object(ingest, "download_file", side_effect=fake_download)
    mocker.patch.object(ingest.db, "upsert_employer_h1b_stats", return_value=False)
    record = mocker.patch.object(ingest.db, "record_run")

    ingest.run(csv_url="https://example.test/uscis.csv")

    args = record.call_args[0]
    assert args[1] == 0   # nothing enriched
    assert args[3] == 1   # errors
