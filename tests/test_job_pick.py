"""Tests for job_pick.py. Every Claude call goes through emailer._call_claude (mocked); every
embedding call goes through job_pick._embed (mocked, never loads the real sentence-transformers
model) -- tests stay fast and fully offline."""

import config
import job_pick


# ── Stage 1: structured filters ─────────────────────────────────────────────────

def test_structured_filters_pass_matching_title():
    job = {"role": "Product Manager", "posting_snapshot": {"location": "Remote", "employment_type": "Full-time"}}
    assert job_pick._passes_structured_filters(job) is True


def test_structured_filters_reject_clearly_unrelated_title():
    job = {"role": "Senior Java Backend Engineer", "posting_snapshot": {}}
    assert job_pick._passes_structured_filters(job) is False


# ── Stage 2: embedding similarity ────────────────────────────────────────────────

def test_embedding_similarity_returns_cosine_score(mocker):
    mocker.patch("job_pick._embed", side_effect=[[1.0, 0.0], [1.0, 0.0]])
    score = job_pick._embedding_similarity("job description text", "profile text")
    assert score == 1.0


def test_embedding_similarity_orthogonal_vectors_score_zero(mocker):
    mocker.patch("job_pick._embed", side_effect=[[1.0, 0.0], [0.0, 1.0]])
    score = job_pick._embedding_similarity("job description text", "profile text")
    assert score == 0.0


# ── Stage 3: LLM judge ───────────────────────────────────────────────────────────

def test_llm_judge_parses_verdict_and_reasoning(mocker):
    mocker.patch("job_pick._call_claude",
                 return_value='{"verdict": "strong", "reasoning": "Direct title and skill match."}')
    result = job_pick._llm_judge(
        {"company": "Acme", "role": "PM", "posting_snapshot": {"description": "..."}}, "Associate PM at Protium."
    )
    assert result == {"verdict": "strong", "reasoning": "Direct title and skill match."}


def test_llm_judge_strips_json_fence(mocker):
    mocker.patch("job_pick._call_claude",
                 return_value='```json\n{"verdict": "no", "reasoning": "Wrong seniority."}\n```')
    result = job_pick._llm_judge({"company": "Acme", "role": "PM", "posting_snapshot": {}}, "Associate PM at Protium.")
    assert result["verdict"] == "no"


def test_llm_judge_never_raises_on_malformed_json(mocker):
    # fifty-a-day F10: an unparseable judgment used to be recorded as "no", dropping the job for
    # good. It now comes back without a verdict, so the row stays unscored and is retried.
    mocker.patch("job_pick._call_claude", return_value="not json at all")
    result = job_pick._llm_judge({"company": "Acme", "role": "PM", "posting_snapshot": {}}, "Associate PM at Protium.")
    assert result["verdict"] is None
    assert "parse" in result["reasoning"].lower()


# Merge review 2026-09-28, finding 3: the judge prompt used to ask for a fit verdict against "a
# real candidate" while supplying only the employer/role/description -- no candidate facts ever
# reached this prompt, even though _profile_text() (used for the embedding stage) had them.
def test_llm_judge_grounds_the_prompt_in_the_real_candidate_profile(mocker):
    call_claude_mock = mocker.patch(
        "job_pick._call_claude", return_value='{"verdict": "strong", "reasoning": "Good fit."}'
    )
    job_pick._llm_judge({"company": "Acme", "role": "PM", "posting_snapshot": {"description": "..."}}, "Associate PM at Protium Finance.")

    prompt = call_claude_mock.call_args[0][0]
    assert "Associate PM at Protium Finance." in prompt


def test_llm_judge_degrades_to_maybe_when_no_profile_text_available(mocker):
    call_claude_mock = mocker.patch("job_pick._call_claude")
    result = job_pick._llm_judge({"company": "Acme", "role": "PM", "posting_snapshot": {}}, "")
    assert result["verdict"] == "maybe"
    assert "review" in result["reasoning"].lower()
    call_claude_mock.assert_not_called()


# ── score_job: orchestration ─────────────────────────────────────────────────────

import json  # noqa: E402

import pytest  # noqa: E402

import claude_subscription  # noqa: E402
import job_filters  # noqa: E402


@pytest.fixture(autouse=True)
def _offline(mocker):
    mocker.patch("job_pick.job_sourcing.load_search_settings", return_value=(job_filters.load_preferences({}), {}))
    mocker.patch("job_pick._embed", return_value=[1.0, 0.0])
    mocker.patch("job_pick.db.set_pick_attempts")


def test_score_job_short_circuits_at_the_filters(mocker):
    embed_mock = mocker.patch("job_pick._embedding_similarity")
    job = {"role": "Senior Java Backend Engineer", "posting_snapshot": {}}
    result = job_pick.score_job(job)
    assert result["verdict"] == "no"
    assert result["score"] is None
    assert result["reasoning"] == "filter: seniority"
    embed_mock.assert_not_called()


def test_score_job_rejects_a_foreign_location_before_any_model_call(mocker):
    judge = mocker.patch("job_pick._llm_judge")
    result = job_pick.score_job({"role": "Product Manager", "location": "London, UK", "posting_snapshot": {}})
    assert result["reasoning"] == "filter: location"
    judge.assert_not_called()


def test_score_job_short_circuits_below_embedding_threshold(mocker):
    mocker.patch("job_pick._embedding_similarity", return_value=0.1)
    judge_mock = mocker.patch("job_pick._llm_judge")
    job = {"role": "Product Manager", "posting_snapshot": {"description": "..."}}
    result = job_pick.score_job(job)
    assert result["verdict"] == "no"
    assert result["score"] == 0.1
    judge_mock.assert_not_called()


def test_score_job_calls_judge_when_embedding_survives(mocker):
    mocker.patch("job_pick._embedding_similarity", return_value=0.8)
    mocker.patch("job_pick._llm_judge", return_value={"verdict": "strong", "reasoning": "Good fit."})
    job = {"role": "Product Manager", "posting_snapshot": {"description": "..."}}
    result = job_pick.score_job(job)
    assert result == {"verdict": "strong", "score": 0.8, "reasoning": "Good fit."}


def test_a_null_description_falls_back_to_the_title(mocker):
    similarity = mocker.patch("job_pick._embedding_similarity", return_value=0.1)
    job_pick.score_job({"role": "Product Manager", "posting_snapshot": {"description": None}})
    assert similarity.call_args.args[0] == "Product Manager"


# ── _profile_text: real profile data, not a vague summary ───────────────────────

def test_profile_text_includes_real_role_and_project_bullets():
    text = job_pick._profile_text()
    assert "Protium Finance" in text or "Associate Product Manager" in text
    assert len(text) > 50


# ── _judge_batch ────────────────────────────────────────────────────────────────

def _jobs(*ids):
    return [{"id": i, "company": f"Co{i}", "role": "Product Manager", "posting_snapshot": {"description": "d"}}
            for i in ids]


def test_judge_batch_maps_answers_back_by_position(mocker):
    call = mocker.patch("job_pick._call_claude", return_value=json.dumps([
        {"id": 1, "verdict": "maybe", "reasoning": "b"}, {"id": 0, "verdict": "strong", "reasoning": "a"},
        {"id": 7, "verdict": "strong", "reasoning": "out of range"}, {"id": 2, "verdict": "great"}]))
    result = job_pick._judge_batch(_jobs(10, 20, 30), "Associate PM at Protium.")
    assert result == {0: {"verdict": "strong", "reasoning": "a"}, 1: {"verdict": "maybe", "reasoning": "b"}}
    prompt = call.call_args.args[0]
    assert "[id 0] Co10" in prompt and "[id 2] Co30" in prompt and "Associate PM at Protium." in prompt


@pytest.mark.parametrize("raw", ["not json", "{}", "[1, 2]", '{"verdict": "strong"}'])
def test_judge_batch_returns_nothing_it_cannot_read(mocker, raw):
    mocker.patch("job_pick._call_claude", return_value=raw)
    assert job_pick._judge_batch(_jobs(1, 2), "profile") == {}


def test_judge_batch_runs_on_the_subscription_when_configured(mocker):
    mocker.patch.object(config, "JOB_PICK_BACKEND", "subscription")
    complete = mocker.patch("job_pick.claude_subscription.complete",
                            return_value=('[{"id": 0, "verdict": "no", "reasoning": "x"}]', {"input_tokens": 1, "output_tokens": 1}))
    log_usage = mocker.patch("job_pick.usage_tracking.log_usage")
    api = mocker.patch("job_pick._call_claude")
    assert job_pick._judge_batch(_jobs(1), "profile") == {0: {"verdict": "no", "reasoning": "x"}}
    complete.assert_called_once()
    assert log_usage.call_args.kwargs["billing"] == "subscription"
    api.assert_not_called()


def test_judge_batch_lets_a_subscription_failure_through(mocker):
    mocker.patch.object(config, "JOB_PICK_BACKEND", "subscription")
    mocker.patch("job_pick.claude_subscription.complete", side_effect=claude_subscription.ClaudeUsageLimitError("limit"))
    with pytest.raises(claude_subscription.ClaudeUsageLimitError):
        job_pick._judge_batch(_jobs(1), "profile")


# ── run(): batch orchestration, zero-tap resume trigger ──────────────────────────

@pytest.fixture
def scoring(mocker):
    mocker.patch("job_pick.db.count_stale_strong_without_resume", return_value=0)
    return {
        "rows": mocker.patch("job_pick.db.get_unscored_saved_applications", return_value=_jobs(1)),
        "prefilter": mocker.patch("job_pick._prefilter", return_value=(None, 0.8)),
        "judge": mocker.patch("job_pick._judge_batch", return_value={0: {"verdict": "strong", "reasoning": "x"}}),
        "verdict": mocker.patch("job_pick.db.set_pick_verdict"),
        "propose": mocker.patch("job_pick.resume_agent.propose"),
        "build": mocker.patch("job_pick.resume_agent.build"),
    }


def test_run_triggers_resume_pipeline_only_on_strong_verdict(mocker, scoring):
    mocker.patch.object(config, "RESUME_CLAUDE_BACKEND", "api")
    job_pick.run()
    scoring["verdict"].assert_called_once_with(1, "strong", 0.8, "x")
    scoring["propose"].assert_called_once_with(1)
    scoring["build"].assert_called_once_with(1)


def test_run_does_not_trigger_resume_pipeline_on_maybe_or_no(mocker, scoring):
    mocker.patch.object(config, "RESUME_CLAUDE_BACKEND", "api")
    scoring["judge"].return_value = {0: {"verdict": "maybe", "reasoning": "x"}}
    job_pick.run()
    scoring["propose"].assert_not_called()


def test_run_writes_prefilter_verdicts_without_a_judge_call(mocker, scoring):
    scoring["prefilter"].return_value = ({"verdict": "no", "score": None, "reasoning": "filter: location"}, None)
    job_pick.run()
    scoring["verdict"].assert_called_once_with(1, "no", None, "filter: location")
    scoring["judge"].assert_not_called()


def test_run_judges_in_batches(mocker, scoring):
    mocker.patch.object(config, "JOB_PICK_JUDGE_BATCH", 2)
    scoring["rows"].return_value = _jobs(1, 2, 3, 4, 5)
    scoring["judge"].side_effect = lambda chunk, profile: {i: {"verdict": "no", "reasoning": "x"} for i in range(len(chunk))}
    job_pick.run()
    assert [len(c.args[0]) for c in scoring["judge"].call_args_list] == [2, 2, 1]
    assert scoring["verdict"].call_count == 5


def test_an_unjudged_row_is_retried_then_parked_as_maybe(mocker, scoring):
    # fifty-a-day F10: a failed judgment is not a "no".
    scoring["rows"].return_value = [dict(_jobs(1)[0], pick_attempts=0), dict(_jobs(2)[0], pick_attempts=2)]
    scoring["judge"].return_value = {}
    job_pick.run()
    job_pick.db.set_pick_attempts.assert_called_once_with(1, 1)
    scoring["verdict"].assert_called_once()
    args = scoring["verdict"].call_args.args
    assert args[0] == 2 and args[1] == "maybe" and "human review" in args[3]


def test_run_stops_judging_when_the_subscription_is_out(mocker, scoring):
    mocker.patch.object(config, "JOB_PICK_JUDGE_BATCH", 1)
    scoring["rows"].return_value = _jobs(1, 2)
    scoring["judge"].side_effect = claude_subscription.ClaudeUsageLimitError("limit")
    job_pick.run()
    assert scoring["judge"].call_count == 1
    scoring["verdict"].assert_not_called()
    job_pick.db.set_pick_attempts.assert_not_called()


def test_run_isolates_one_row_failure_from_the_rest(mocker, scoring):
    scoring["rows"].return_value = _jobs(1, 2)
    scoring["prefilter"].side_effect = [RuntimeError("boom"), ({"verdict": "no", "score": 0.1, "reasoning": "x"}, None)]
    job_pick.run()  # must not raise
    scoring["verdict"].assert_called_once_with(2, "no", 0.1, "x")


def test_run_never_raises_when_resume_pipeline_fails(mocker, scoring):
    mocker.patch.object(config, "RESUME_CLAUDE_BACKEND", "api")
    scoring["propose"].side_effect = RuntimeError("claude down")
    job_pick.run()  # must not raise


def test_run_without_a_profile_marks_maybe_and_calls_nothing(mocker, scoring):
    mocker.patch("job_pick._profile_text", return_value="  ")
    job_pick.run()
    scoring["prefilter"].assert_not_called()
    scoring["judge"].assert_not_called()
    assert scoring["verdict"].call_args.args[1] == "maybe"


def test_run_queues_strong_verdict_for_the_beelink_on_subscription_backend(mocker, scoring):
    mocker.patch.object(config, "RESUME_CLAUDE_BACKEND", "subscription")
    job_pick.run()
    scoring["verdict"].assert_called_once_with(1, "strong", 0.8, "x")
    scoring["propose"].assert_not_called()
    scoring["build"].assert_not_called()


def test_run_returns_stale_count_on_subscription_backend(mocker, scoring):
    mocker.patch.object(config, "RESUME_CLAUDE_BACKEND", "subscription")
    scoring["rows"].return_value = []
    count = mocker.patch("job_pick.db.count_stale_strong_without_resume", return_value=4)
    assert job_pick.run() == 4
    count.assert_called_once_with(config.RESUME_QUEUE_STALE_HOURS,
                                  exclude_platforms=job_pick.ats_platform.unpreparable_platforms())


def test_run_returns_zero_without_db_call_on_api_backend(mocker, scoring):
    mocker.patch.object(config, "RESUME_CLAUDE_BACKEND", "api")
    scoring["rows"].return_value = []
    count = mocker.patch("job_pick.db.count_stale_strong_without_resume")
    assert job_pick.run() == 0
    count.assert_not_called()


def test_run_fails_closed_when_stale_queue_check_raises(mocker, scoring):
    mocker.patch.object(config, "RESUME_CLAUDE_BACKEND", "subscription")
    scoring["rows"].return_value = []
    mocker.patch("job_pick.db.count_stale_strong_without_resume", side_effect=RuntimeError("bad column"))
    assert job_pick.run() != 0


def test_run_reads_a_bounded_batch(mocker, scoring):
    scoring["rows"].return_value = []
    job_pick.run()
    scoring["rows"].assert_called_once_with(config.JOB_PICK_MAX_PER_RUN)
