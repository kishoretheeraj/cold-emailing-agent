import json
import sys

sys.path.insert(0, "scripts")
import seed_applicant_eligibility  # noqa: E402


def test_seed_writes_full_row_when_absent(mocker):
    mocker.patch("db.load_prompts", return_value={})
    mock_table = mocker.MagicMock()
    mock_client = mocker.MagicMock()
    mock_client.table.return_value = mock_table
    mocker.patch("db.get_client", return_value=mock_client)

    result = seed_applicant_eligibility.seed()

    assert result is True
    mock_client.table.assert_called_once_with("prompts")
    (row,), kwargs = mock_table.upsert.call_args
    assert kwargs == {"on_conflict": "key"}
    # The bug this regresses: upsert_prompt only ever set key/value/updated_at, which
    # violates prompts.display_title's NOT NULL constraint on a brand-new key.
    assert row["key"] == "applicant_eligibility"
    assert row["display_title"]
    assert row["sort_order"] == 66
    assert row["description"]
    assert "updated_at" in row
    parsed = json.loads(row["value"])
    assert parsed == seed_applicant_eligibility._DEFAULT


def test_seed_accepts_custom_default(mocker):
    mocker.patch("db.load_prompts", return_value={})
    mock_table = mocker.MagicMock()
    mock_client = mocker.MagicMock()
    mock_client.table.return_value = mock_table
    mocker.patch("db.get_client", return_value=mock_client)

    custom = {"work_authorized_us": "Yes", "requires_visa_sponsorship": "Yes"}
    seed_applicant_eligibility.seed(default=custom)

    (row,), _ = mock_table.upsert.call_args
    assert json.loads(row["value"]) == custom


def test_seed_never_overwrites_existing_value(mocker):
    mocker.patch("db.load_prompts", return_value={"applicant_eligibility": '{"work_authorized_us": "Yes"}'})
    mock_client = mocker.MagicMock()
    mocker.patch("db.get_client", return_value=mock_client)

    result = seed_applicant_eligibility.seed()

    assert result is False
    mock_client.table.assert_not_called()
