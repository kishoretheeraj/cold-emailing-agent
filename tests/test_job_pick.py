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
    mocker.patch("job_pick._call_claude", return_value="not json at all")
    result = job_pick._llm_judge({"company": "Acme", "role": "PM", "posting_snapshot": {}}, "Associate PM at Protium.")
    assert result["verdict"] == "no"
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

def test_score_job_short_circuits_at_structured_filters(mocker):
    embed_mock = mocker.patch("job_pick._embedding_similarity")
    job = {"role": "Senior Java Backend Engineer", "posting_snapshot": {}}
    result = job_pick.score_job(job)
    assert result["verdict"] == "no"
    assert result["score"] is None
    embed_mock.assert_not_called()


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


# ── _profile_text: real profile data, not a vague summary ───────────────────────

def test_profile_text_includes_real_role_and_project_bullets():
    text = job_pick._profile_text()
    assert "Protium Finance" in text or "Associate Product Manager" in text
    assert len(text) > 50


# ── run(): batch orchestration, zero-tap resume trigger ──────────────────────────

def test_run_triggers_resume_pipeline_only_on_strong_verdict(mocker):
    mocker.patch.object(config, "RESUME_CLAUDE_BACKEND", "api")
    mocker.patch("job_pick.db.get_unscored_saved_applications",
                 return_value=[{"id": 1, "role": "PM", "posting_snapshot": {}}])
    mocker.patch("job_pick.score_job", return_value={"verdict": "strong", "score": 0.9, "reasoning": "x"})
    set_verdict_mock = mocker.patch("job_pick.db.set_pick_verdict")
    propose_mock = mocker.patch("job_pick.resume_agent.propose")
    build_mock = mocker.patch("job_pick.resume_agent.build")

    job_pick.run()

    set_verdict_mock.assert_called_once_with(1, "strong", 0.9, "x")
    propose_mock.assert_called_once_with(1)
    build_mock.assert_called_once_with(1)


def test_run_does_not_trigger_resume_pipeline_on_maybe_or_no(mocker):
    mocker.patch.object(config, "RESUME_CLAUDE_BACKEND", "api")
    mocker.patch("job_pick.db.get_unscored_saved_applications",
                 return_value=[{"id": 1, "role": "PM", "posting_snapshot": {}}])
    mocker.patch("job_pick.score_job", return_value={"verdict": "maybe", "score": 0.4, "reasoning": "x"})
    mocker.patch("job_pick.db.set_pick_verdict")
    propose_mock = mocker.patch("job_pick.resume_agent.propose")

    job_pick.run()

    propose_mock.assert_not_called()


def test_run_isolates_one_row_failure_from_the_rest(mocker):
    mocker.patch("job_pick.db.get_unscored_saved_applications", return_value=[
        {"id": 1, "role": "PM", "posting_snapshot": {}},
        {"id": 2, "role": "PM", "posting_snapshot": {}},
    ])
    mocker.patch("job_pick.score_job", side_effect=[RuntimeError("boom"), {"verdict": "no", "score": 0.1, "reasoning": "x"}])
    set_verdict_mock = mocker.patch("job_pick.db.set_pick_verdict")

    job_pick.run()  # must not raise

    set_verdict_mock.assert_called_once_with(2, "no", 0.1, "x")


def test_run_never_raises_when_resume_pipeline_fails(mocker):
    mocker.patch.object(config, "RESUME_CLAUDE_BACKEND", "api")
    mocker.patch("job_pick.db.get_unscored_saved_applications",
                 return_value=[{"id": 1, "role": "PM", "posting_snapshot": {}}])
    mocker.patch("job_pick.score_job", return_value={"verdict": "strong", "score": 0.9, "reasoning": "x"})
    mocker.patch("job_pick.db.set_pick_verdict")
    mocker.patch("job_pick.resume_agent.propose", side_effect=RuntimeError("claude down"))

    job_pick.run()  # must not raise


def test_run_queues_strong_verdict_for_the_beelink_on_subscription_backend(mocker):
    mocker.patch.object(config, "RESUME_CLAUDE_BACKEND", "subscription")
    mocker.patch("job_pick.db.get_unscored_saved_applications",
                 return_value=[{"id": 1, "company": "Acme", "role": "PM", "posting_snapshot": {}}])
    mocker.patch("job_pick.score_job", return_value={"verdict": "strong", "score": 0.9, "reasoning": "x"})
    set_verdict = mocker.patch("job_pick.db.set_pick_verdict")
    mocker.patch("job_pick.db.count_stale_strong_without_resume", return_value=0)
    propose = mocker.patch("job_pick.resume_agent.propose")
    build = mocker.patch("job_pick.resume_agent.build")

    job_pick.run()

    set_verdict.assert_called_once_with(1, "strong", 0.9, "x")
    propose.assert_not_called()
    build.assert_not_called()


def test_run_returns_stale_count_on_subscription_backend(mocker):
    mocker.patch.object(config, "RESUME_CLAUDE_BACKEND", "subscription")
    mocker.patch("job_pick.db.get_unscored_saved_applications", return_value=[])
    count = mocker.patch("job_pick.db.count_stale_strong_without_resume", return_value=4)

    assert job_pick.run() == 4
    count.assert_called_once_with(config.RESUME_QUEUE_STALE_HOURS)


def test_run_returns_zero_without_db_call_on_api_backend(mocker):
    mocker.patch.object(config, "RESUME_CLAUDE_BACKEND", "api")
    mocker.patch("job_pick.db.get_unscored_saved_applications", return_value=[])
    count = mocker.patch("job_pick.db.count_stale_strong_without_resume")

    assert job_pick.run() == 0
    count.assert_not_called()


def test_run_fails_closed_when_stale_queue_check_raises(mocker):
    mocker.patch.object(config, "RESUME_CLAUDE_BACKEND", "subscription")
    mocker.patch("job_pick.db.get_unscored_saved_applications", return_value=[])
    mocker.patch("job_pick.db.count_stale_strong_without_resume", side_effect=RuntimeError("bad column"))

    assert job_pick.run() != 0
