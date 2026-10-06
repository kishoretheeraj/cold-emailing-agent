"""db.py accessors for the Beelink resume worker's queue (mock pattern from test_db_draft_history)."""

import db


def _client(mocker, data):
    client = mocker.MagicMock()
    chain = client.table.return_value
    for name in ("select", "eq", "is_", "order", "limit", "update"):
        getattr(chain, name).return_value = chain
    chain.execute.return_value = mocker.MagicMock(data=data)
    mocker.patch.object(db, "get_client", return_value=client)
    return chain


def test_get_strong_applications_without_resume_filters_and_limits(mocker):
    chain = _client(mocker, [{"id": 3}])
    assert db.get_strong_applications_without_resume(2) == [{"id": 3}]
    eq_calls = {c.args for c in chain.eq.call_args_list}
    assert ("pick_verdict", "strong") in eq_calls and ("stage", "saved") in eq_calls
    is_calls = {c.args for c in chain.is_.call_args_list}
    assert ("resume_file_ref", "null") in is_calls and ("resume_error", "null") in is_calls
    chain.order.assert_called_with("created_at", desc=False)
    chain.limit.assert_called_with(2)


def test_set_resume_error_truncates(mocker):
    chain = _client(mocker, [{"id": 3}])
    db.set_resume_error(3, "x" * 5000)
    payload = chain.update.call_args.args[0]
    assert len(payload["resume_error"]) == 1000
    chain.eq.assert_called_with("id", 3)


def test_count_stale_strong_without_resume_filters_and_counts(mocker):
    chain = _client(mocker, [])
    chain.lt.return_value = chain
    chain.execute.return_value = mocker.MagicMock(data=[], count=3)
    assert db.count_stale_strong_without_resume(24) == 3
    chain.select.assert_called_with("id", count="exact")
    eq_calls = {c.args for c in chain.eq.call_args_list}
    assert ("pick_verdict", "strong") in eq_calls and ("stage", "saved") in eq_calls
    is_calls = {c.args for c in chain.is_.call_args_list}
    assert ("resume_file_ref", "null") in is_calls and ("resume_error", "null") in is_calls
    column, cutoff = chain.lt.call_args.args
    assert column == "updated_at" and "T" in cutoff


def test_count_stale_strong_without_resume_none_count_is_zero(mocker):
    chain = _client(mocker, [])
    chain.lt.return_value = chain
    chain.execute.return_value = mocker.MagicMock(data=[], count=None)
    assert db.count_stale_strong_without_resume(24) == 0
