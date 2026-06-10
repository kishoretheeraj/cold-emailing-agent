"""Tests for the pause guard in agent.run() and monitor.run()."""
import pytest


# ── db.get_pause_scope ─────────────────────────────────────────────────────────

def test_get_pause_scope_returns_none_on_db_error(mocker):
    mocker.patch("db.get_client", side_effect=Exception("connection refused"))
    import db
    assert db.get_pause_scope() == "none"


def test_get_pause_scope_returns_value_from_supabase(mocker):
    mock_result = mocker.MagicMock()
    mock_result.data = {"value": "all"}
    chain = mocker.MagicMock()
    chain.execute.return_value = mock_result
    chain.single.return_value = chain
    chain.eq.return_value = chain
    chain.select.return_value = chain
    mock_table = mocker.MagicMock()
    mock_table.table.return_value = chain
    mocker.patch("db.get_client", return_value=mock_table)
    import db
    assert db.get_pause_scope() == "all"


def test_get_pause_scope_missing_row_returns_none(mocker):
    mock_result = mocker.MagicMock()
    mock_result.data = None
    chain = mocker.MagicMock()
    chain.execute.return_value = mock_result
    chain.single.return_value = chain
    chain.eq.return_value = chain
    chain.select.return_value = chain
    mock_table = mocker.MagicMock()
    mock_table.table.return_value = chain
    mocker.patch("db.get_client", return_value=mock_table)
    import db
    assert db.get_pause_scope() == "none"


# ── agent.run() pause guard ────────────────────────────────────────────────────

@pytest.mark.parametrize("scope", ["agent", "all"])
def test_agent_run_paused_exits_without_contacts(mocker, scope):
    import agent
    mocker.patch.object(agent, "get_pause_scope", return_value=scope)
    mock_contacts = mocker.patch.object(agent, "get_all_contacts")
    agent.run()
    mock_contacts.assert_not_called()


def test_agent_run_not_paused_proceeds(mocker):
    import agent
    mocker.patch("db.get_pause_scope", return_value="none")
    mock_contacts = mocker.patch.object(agent, "get_all_contacts", return_value=[])
    mocker.patch.object(agent, "load_prompts", return_value={})
    mocker.patch("agent._validate_prompts", return_value=[])
    mocker.patch("agent._validate_prompt_output_schemas", return_value=[])
    mocker.patch.object(agent, "record_run")
    agent.run()
    mock_contacts.assert_called_once()


# ── monitor.run() pause guard ──────────────────────────────────────────────────

def test_monitor_run_paused_all_exits_without_scanning(mocker):
    import monitor
    mocker.patch.object(monitor, "get_pause_scope", return_value="all")
    mocker.patch("monitor.detect_sent_drafts")
    mocker.patch("monitor.detect_replies", return_value=[])
    mocker.patch("monitor._draft_reply_responses")
    monitor.run()
    monitor.detect_sent_drafts.assert_not_called()  # type: ignore[attr-defined]


def test_monitor_run_paused_agent_only_does_not_stop_monitor(mocker):
    """scope='agent' pauses outbound drafts but monitor keeps running."""
    import monitor
    mocker.patch.object(monitor, "get_pause_scope", return_value="agent")
    mocker.patch.object(monitor, "load_prompts", return_value={})
    mock_detect_sent = mocker.patch("monitor.detect_sent_drafts")
    mocker.patch("monitor.detect_replies", return_value=[])
    mocker.patch("monitor._draft_reply_responses")
    mocker.patch.object(monitor, "record_run")
    monitor.run()
    mock_detect_sent.assert_called_once()
