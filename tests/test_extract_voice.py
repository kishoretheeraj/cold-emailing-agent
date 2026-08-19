"""Tests for extract_voice -- Voice DNA extraction from real sent mail."""

import pytest

import extract_voice


_SAMPLES = [f"Hey, quick note about the thing. Body number {i}. Thanks!" for i in range(8)]


def test_writes_voice_dna_row_on_success(mocker):
    mocker.patch.object(extract_voice.gmail, "fetch_recent_sent", return_value=_SAMPLES)
    mocker.patch.object(extract_voice, "_call_claude", return_value="## Writing Style\nShort.")
    upsert = mocker.patch.object(extract_voice.db, "upsert_prompt", return_value=True)
    assert extract_voice.run() is True
    assert upsert.call_args.args[0] == "voice_dna"
    assert "Writing Style" in upsert.call_args.args[1]


def test_no_op_when_too_few_samples(mocker):
    mocker.patch.object(extract_voice.gmail, "fetch_recent_sent", return_value=["only one"])
    claude = mocker.patch.object(extract_voice, "_call_claude")
    upsert = mocker.patch.object(extract_voice.db, "upsert_prompt")
    assert extract_voice.run(min_samples=5) is False
    claude.assert_not_called()
    upsert.assert_not_called()


def test_claude_failure_leaves_existing_row_untouched(mocker):
    mocker.patch.object(extract_voice.gmail, "fetch_recent_sent", return_value=_SAMPLES)
    mocker.patch.object(extract_voice, "_call_claude", side_effect=RuntimeError("api down"))
    upsert = mocker.patch.object(extract_voice.db, "upsert_prompt")
    assert extract_voice.run() is False
    upsert.assert_not_called()


def test_empty_claude_output_is_not_written(mocker):
    mocker.patch.object(extract_voice.gmail, "fetch_recent_sent", return_value=_SAMPLES)
    mocker.patch.object(extract_voice, "_call_claude", return_value="   ")
    upsert = mocker.patch.object(extract_voice.db, "upsert_prompt")
    assert extract_voice.run() is False
    upsert.assert_not_called()


def test_imap_failure_is_a_no_op(mocker):
    mocker.patch.object(extract_voice.gmail, "fetch_recent_sent", return_value=[])
    upsert = mocker.patch.object(extract_voice.db, "upsert_prompt")
    assert extract_voice.run() is False
    upsert.assert_not_called()


def test_samples_are_truncated_before_prompting(mocker):
    long_body = "x" * 5000
    mocker.patch.object(extract_voice.gmail, "fetch_recent_sent",
                        return_value=[long_body] * 6)
    claude = mocker.patch.object(extract_voice, "_call_claude", return_value="## Writing Style\nS.")
    mocker.patch.object(extract_voice.db, "upsert_prompt", return_value=True)
    extract_voice.run()
    sent_prompt = claude.call_args.args[0]
    assert "x" * (extract_voice.MAX_SAMPLE_CHARS + 1) not in sent_prompt
