"""Tests for engagement_report -- decision-context outcome report."""

from unittest.mock import MagicMock

import pytest

import db
import engagement_report


@pytest.fixture
def fake_client(mocker):
    client = MagicMock(name="supabase_client")
    mocker.patch.object(db, "_client", client)
    mocker.patch.object(db, "get_client", return_value=client)
    return client


def _draft(contact_id, prompt_hash="aaaaaaaaaaaaaaaa",
           stage="first_touch_drafted", drafted_at="2026-08-20T00:00:00Z"):
    ctx = None if prompt_hash is None else {"prompt_hash": prompt_hash}
    return {"contact_id": contact_id, "stage": stage,
            "decision_context": ctx, "drafted_at": drafted_at}


def _contact(cid, name="Dana", company="Acme", classifier_status=None):
    return {"id": cid, "name": name, "company": company,
            "classifier_status": classifier_status}


# ── db.get_draft_history_by_stages ─────────────────────────────────────────────

def test_get_draft_history_by_stages_filters_and_orders(fake_client):
    chain = fake_client.table.return_value.select.return_value.in_.return_value.order.return_value
    chain.execute.return_value.data = [_draft(1)]

    rows = db.get_draft_history_by_stages(
        engagement_report._FIRST_TOUCH_DRAFTED_STAGES)

    fake_client.table.assert_called_with("draft_history")
    fake_client.table.return_value.select.return_value.in_.assert_called_with(
        "stage", list(engagement_report._FIRST_TOUCH_DRAFTED_STAGES))
    assert rows[0]["contact_id"] == 1


def test_get_draft_history_by_stages_empty_on_no_data(fake_client):
    chain = fake_client.table.return_value.select.return_value.in_.return_value.order.return_value
    chain.execute.return_value.data = None
    assert db.get_draft_history_by_stages(["first_touch_drafted"]) == []


def test_get_draft_history_by_stages_propagates_on_error(mocker):
    # An empty report and a failed read must not look alike.
    mocker.patch.object(db, "get_client", side_effect=RuntimeError("db down"))
    with pytest.raises(RuntimeError):
        db.get_draft_history_by_stages(["first_touch_drafted"])


# ── db.get_research_reliability_map ────────────────────────────────────────────

def test_get_research_reliability_map_keys_by_cache_key(fake_client):
    fake_client.table.return_value.select.return_value.execute.return_value.data = [
        {"cache_key": "dana|acme", "brief_reliable": True},
        {"cache_key": "sam|beta", "brief_reliable": False},
    ]
    result = db.get_research_reliability_map()
    fake_client.table.assert_called_with("research_cache")
    assert result == {"dana|acme": True, "sam|beta": False}


def test_get_research_reliability_map_empty_on_no_data(fake_client):
    fake_client.table.return_value.select.return_value.execute.return_value.data = None
    assert db.get_research_reliability_map() == {}


# ── Join and grouping ──────────────────────────────────────────────────────────

def test_build_rows_joins_contact_fields_onto_draft_rows():
    rows = engagement_report.build_rows(
        [_draft(1, "abc123abc123abc1")],
        [_contact(1, "Dana", "Acme", "positive_reply")],
        {"dana|acme": True},
    )
    assert rows == [{
        "name": "Dana", "company": "Acme",
        "prompt_hash": "abc123abc123abc1",
        "research": "reliable",
        "outcome": "positive_reply",
        "replied": True,
    }]


def test_build_rows_uses_stripped_lowercase_cache_key():
    # Must match research._cache_key exactly, including .strip().
    rows = engagement_report.build_rows(
        [_draft(1)], [_contact(1, " Dana ", " Acme ")], {"dana|acme": True})
    assert rows[0]["research"] == "reliable"


def test_build_rows_renders_no_research_when_cache_row_missing():
    rows = engagement_report.build_rows([_draft(1)], [_contact(1)], {})
    assert rows[0]["research"] == "no research"


def test_build_rows_renders_no_reply_yet_for_null_classifier_status():
    rows = engagement_report.build_rows([_draft(1)], [_contact(1)], {})
    assert rows[0]["outcome"] == "no reply yet"
    assert rows[0]["replied"] is False


def test_build_rows_keeps_only_the_most_recent_draft_per_contact():
    rows = engagement_report.build_rows(
        [_draft(1, "newnewnewnewnew1", drafted_at="2026-08-20T00:00:00Z"),
         _draft(1, "oldoldoldoldold1", drafted_at="2026-08-01T00:00:00Z")],
        [_contact(1)], {},
    )
    assert len(rows) == 1
    assert rows[0]["prompt_hash"] == "newnewnewnewnew1"


def test_group_counts_n_is_distinct_contacts_not_draft_rows():
    # Regression: two draft rows for one contact must count once, or the rate
    # is wrong the first time anyone redrafts.
    rows = engagement_report.build_rows(
        [_draft(1), _draft(1, drafted_at="2026-08-01T00:00:00Z")],
        [_contact(1, classifier_status="positive_reply")], {},
    )
    groups = engagement_report.group_counts(rows)
    assert groups["aaaaaaaaaaaaaaaa"] == {"n": 1, "replies": 1}


def test_build_rows_skips_a_draft_with_no_matching_contact():
    assert engagement_report.build_rows([_draft(99)], [_contact(1)], {}) == []


# ── NULL handling ────────────────────────────────────────────────────────────

def test_null_decision_context_renders_unknown():
    rows = engagement_report.build_rows([_draft(1, None)], [_contact(1)], {})
    assert rows[0]["prompt_hash"] == "unknown"


def test_unknown_group_still_reports_its_real_reply_count():
    # NULL means "not instrumented", never zero. The unknown group must carry
    # its true counts, not be blanked or reported as 0 replies.
    rows = engagement_report.build_rows(
        [_draft(1, None), _draft(2, None)],
        [_contact(1, "Dana", "Acme", "positive_reply"), _contact(2, "Sam", "Beta")],
        {},
    )
    assert engagement_report.group_counts(rows)["unknown"] == {"n": 2, "replies": 1}


@pytest.mark.parametrize("ctx", [
    None, {}, {"prompt_hash": None}, {"prompt_hash": ""},
    "not-a-dict", ["also", "not"], 42, {"other_key": "x"},
])
def test_malformed_decision_context_renders_unknown(ctx):
    draft = _draft(1)
    draft["decision_context"] = ctx
    rows = engagement_report.build_rows([draft], [_contact(1)], {})
    assert rows[0]["prompt_hash"] == "unknown"


# ── Small-n suppression ──────────────────────────────────────────────────────

def _rows_for(n, replies, prompt_hash="aaaaaaaaaaaaaaaa"):
    drafts, contacts = [], []
    for i in range(n):
        drafts.append(_draft(i, prompt_hash))
        contacts.append(_contact(
            i, f"C{i}", f"Co{i}",
            "positive_reply" if i < replies else None))
    return engagement_report.build_rows(drafts, contacts, {})


def test_rate_line_suppressed_below_threshold(caplog):
    with caplog.at_level("INFO"):
        engagement_report.render(_rows_for(4, 2))
    assert "n too small for a rate" in caplog.text
    assert "%" not in caplog.text


def test_rate_line_printed_at_threshold(caplog):
    with caplog.at_level("INFO"):
        engagement_report.render(_rows_for(5, 2))
    assert "reply_rate=40.0%" in caplog.text
    assert "n too small" not in caplog.text


def test_raw_table_is_printed_even_when_every_group_is_small(caplog):
    with caplog.at_level("INFO"):
        engagement_report.render(_rows_for(2, 1))
    assert "C0" in caplog.text and "C1" in caplog.text


# ── Never raises ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("draft_rows,contacts", [
    ([{}], [_contact(1)]),
    ([{"contact_id": None}], [_contact(1)]),
    ([_draft(1)], [{"id": 1}]),
    ([_draft(1)], [{"id": 1, "name": None, "company": None}]),
    ([None], [_contact(1)]),
    ("not a list", [_contact(1)]),
])
def test_build_rows_never_raises_on_malformed_input(draft_rows, contacts):
    assert isinstance(engagement_report.build_rows(draft_rows, contacts, {}), list)


def test_main_never_raises_when_supabase_is_down(mocker):
    mocker.patch.object(engagement_report, "get_draft_history_by_stages",
                        side_effect=RuntimeError("db down"))
    engagement_report.main()  # must not raise


def test_main_never_writes(mocker):
    mocker.patch.object(engagement_report, "get_draft_history_by_stages",
                        return_value=[_draft(1)])
    mocker.patch.object(engagement_report, "get_all_contacts",
                        return_value=[_contact(1)])
    mocker.patch.object(engagement_report, "get_research_reliability_map",
                        return_value={})
    insert = mocker.patch.object(db, "log_drafted_email")
    update = mocker.patch.object(db, "update_contact")
    engagement_report.main()
    insert.assert_not_called()
    update.assert_not_called()
