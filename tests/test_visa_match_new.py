import pytest

import db
import visa_match_new as vmn


@pytest.fixture
def no_op_record_run(mocker):
    return mocker.patch.object(db, "record_run")


def test_run_no_contacts_records_success(mocker, no_op_record_run):
    mocker.patch.object(db, "get_all_contacts", return_value=[])
    vmn.run()
    no_op_record_run.assert_called_once()
    args, kwargs = no_op_record_run.call_args
    assert args[0] == "success"


def test_run_all_contacts_already_matched_is_noop(mocker, no_op_record_run):
    mocker.patch.object(db, "get_all_contacts", return_value=[
        {"id": 1, "company": "Acme Inc.", "company_intel_id": 5},
    ])
    mock_corpus = mocker.patch.object(db, "get_employer_h1b_stats_corpus")
    vmn.run()
    mock_corpus.assert_not_called()


def test_run_matches_new_company_end_to_end(mocker, no_op_record_run):
    mocker.patch.object(db, "get_all_contacts", return_value=[
        {"id": 1, "company": "Acme Inc.", "company_intel_id": None},
        {"id": 2, "company": "Acme Inc.", "company_intel_id": None},  # same company, two contacts
    ])
    mocker.patch.object(db, "get_employer_h1b_stats_corpus", return_value=[
        {"id": 10, "normalized_name": "acme", "lca_recent_2fy": 5, "latest_filing_fy": 2025, "approval_rate": 0.9},
    ])
    mocker.patch.object(db, "get_company_intel_by_normalized_names", side_effect=[
        [],  # first lookup: no existing row
        [{"id": 99, "normalized_name": "acme"}],  # second lookup: fetch the id we just upserted
    ])
    upsert_mock = mocker.patch.object(db, "upsert_company_intel", return_value=True)
    link_mock = mocker.patch.object(db, "update_contact_company_intel_id", return_value=True)

    vmn.run()

    upsert_mock.assert_called_once()
    row = upsert_mock.call_args.args[0][0]
    assert row["match_status"] == "auto"
    assert row["sponsors_h1b"] is True

    assert link_mock.call_count == 2
    linked_contact_ids = {call.args[0] for call in link_mock.call_args_list}
    assert linked_contact_ids == {1, 2}
    for call in link_mock.call_args_list:
        assert call.args[1] == 99


# ── never-raises sweep ────────────────────────────────────────────────────────────

def test_run_never_raises_when_get_all_contacts_fails(mocker, no_op_record_run):
    mocker.patch.object(db, "get_all_contacts", side_effect=RuntimeError("db down"))
    vmn.run()  # must not raise
    args, kwargs = no_op_record_run.call_args
    assert args[0] == "failure"


def test_run_never_raises_when_corpus_fetch_fails(mocker, no_op_record_run):
    mocker.patch.object(db, "get_all_contacts", return_value=[
        {"id": 1, "company": "Acme Inc.", "company_intel_id": None},
    ])
    mocker.patch.object(db, "get_employer_h1b_stats_corpus", side_effect=RuntimeError("db down"))
    vmn.run()  # must not raise
    args, kwargs = no_op_record_run.call_args
    assert args[0] == "failure"


def test_run_isolates_per_company_failure(mocker, no_op_record_run):
    # One company's resolution blowing up must not stop the others.
    mocker.patch.object(db, "get_all_contacts", return_value=[
        {"id": 1, "company": "Broken Co", "company_intel_id": None},
        {"id": 2, "company": "Acme Inc.", "company_intel_id": None},
    ])
    mocker.patch.object(db, "get_employer_h1b_stats_corpus", return_value=[
        {"id": 10, "normalized_name": "acme", "lca_recent_2fy": 5, "latest_filing_fy": 2025, "approval_rate": 0.9},
    ])

    def _lookup(names):
        if names == ["broken"]:
            raise RuntimeError("lookup exploded")
        return [{"id": 99, "normalized_name": "acme"}] if names == ["acme"] else []

    mocker.patch.object(db, "get_company_intel_by_normalized_names", side_effect=_lookup)
    mocker.patch.object(db, "upsert_company_intel", return_value=True)
    link_mock = mocker.patch.object(db, "update_contact_company_intel_id", return_value=True)

    vmn.run()  # must not raise despite "Broken Co" failing

    link_mock.assert_called_once_with(2, 99)
    args, kwargs = no_op_record_run.call_args
    assert args[0] == "success"  # per-company failures are non-fatal to run status
    assert args[3] == 1  # errors count reflects the one isolated failure
