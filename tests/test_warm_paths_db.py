"""db.get_application_states (spec 2026-10-08-warm-paths-design §3.6): one query for every linked
application, returning only what the agent's gate and prompts need."""

from unittest.mock import MagicMock

import pytest

import db


@pytest.fixture
def fake_client(mocker):
    client = MagicMock(name="supabase_client")
    mocker.patch.object(db, "_client", client)
    mocker.patch.object(db, "get_client", return_value=client)
    mocker.patch.object(db.time, "sleep")
    return client


def _query(client):
    return client.table.return_value.select.return_value.in_.return_value


def test_states_are_keyed_by_id_with_the_posting_description(fake_client):
    _query(fake_client).execute.return_value.data = [
        {"id": 7, "stage": "applied", "role": "APM", "company": "Acme", "applied_date": "2026-10-08",
         "job_url": "https://jobs.acme/1", "posting_snapshot": {"description": "Own the roadmap."}},
        {"id": 8, "stage": "saved", "role": "PM", "company": "Beta", "applied_date": None, "posting_snapshot": None},
    ]
    states = db.get_application_states([7, 8])
    fake_client.table.assert_called_with("job_applications")
    columns = fake_client.table.return_value.select.call_args.args[0]
    assert set(columns.split(",")) == {"id", "stage", "role", "company", "applied_date", "job_url", "posting_snapshot"}
    assert fake_client.table.return_value.select.return_value.in_.call_args.args == ("id", [7, 8])
    assert states[7] == {"id": 7, "stage": "applied", "role": "APM", "company": "Acme",
                         "applied_date": "2026-10-08", "job_url": "https://jobs.acme/1",
                         "posting_description": "Own the roadmap."}
    assert states[8]["posting_description"] == ""


@pytest.mark.parametrize("snapshot", [None, "not a dict", {"description": None}, {"other": 1}])
def test_odd_snapshots_give_an_empty_description(fake_client, snapshot):
    _query(fake_client).execute.return_value.data = [{"id": 1, "stage": "applied", "posting_snapshot": snapshot}]
    assert db.get_application_states([1])[1]["posting_description"] == ""


def test_no_ids_means_no_query(fake_client):
    assert db.get_application_states([]) == {}
    fake_client.table.assert_not_called()


def test_large_id_lists_are_chunked(fake_client):
    _query(fake_client).execute.return_value.data = []
    db.get_application_states(list(range(450)))
    chunks = [c.args[1] for c in fake_client.table.return_value.select.return_value.in_.call_args_list]
    assert [len(c) for c in chunks] == [200, 200, 50]


def test_a_failure_raises_after_retries(fake_client):
    _query(fake_client).execute.side_effect = RuntimeError("down")
    with pytest.raises(RuntimeError):
        db.get_application_states([1])


# ── get_all_contacts pages past the row cap (warm paths I8) ────────────────────

def _contacts_query(client):
    return client.table.return_value.select.return_value.is_.return_value.order.return_value.range.return_value


def test_all_contacts_are_read_page_by_page(fake_client, mocker):
    mocker.patch.object(db, "_CONTACT_PAGE", 2)
    _contacts_query(fake_client).execute.side_effect = [
        mocker.Mock(data=[{"id": 1}, {"id": 2}]), mocker.Mock(data=[{"id": 3}, {"id": 4}]), mocker.Mock(data=[{"id": 5}])]
    assert [c["id"] for c in db.get_all_contacts()] == [1, 2, 3, 4, 5]
    chain = fake_client.table.return_value.select.return_value.is_.return_value
    assert chain.order.call_args.args == ("id",)
    assert [c.args for c in chain.order.return_value.range.call_args_list] == [(0, 1), (2, 3), (4, 5)]


def test_an_exact_multiple_of_the_page_size_ends_on_an_empty_page(fake_client, mocker):
    mocker.patch.object(db, "_CONTACT_PAGE", 2)
    _contacts_query(fake_client).execute.side_effect = [mocker.Mock(data=[{"id": 1}, {"id": 2}]), mocker.Mock(data=[])]
    assert len(db.get_all_contacts()) == 2


def test_no_contacts(fake_client):
    _contacts_query(fake_client).execute.return_value.data = None
    assert db.get_all_contacts() == []
