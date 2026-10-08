"""Static checks over which GitHub Actions workflows opt into the subscription resume backend.
GitHub's runners have no LibreOffice, so resume builds must never run there; jobright_pull.yml
only scores and queues strong verdicts for the Beelink's resume-worker."""

import glob
import os

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_WORKFLOWS = os.path.join(_ROOT, ".github", "workflows")


def _read(name):
    with open(os.path.join(_WORKFLOWS, name)) as f:
        return f.read()


def test_jobright_pull_no_longer_scores_on_the_paid_api():
    # fifty-a-day F10: job_pick's judge billed the API from this workflow. Scoring now runs on the
    # Beelink's job-pick.service on the Claude subscription; this workflow only pulls.
    workflow = _read("jobright_pull.yml")
    assert "job_pick.py" not in workflow
    assert "requirements-jobs.txt" not in workflow


def test_no_workflow_holds_a_subscription_token():
    # The subscription token lives only in the Beelink's /etc/job-agent/claude.env. A workflow
    # holding it would bill GitHub-run work against the same subscription window.
    for path in glob.glob(os.path.join(_WORKFLOWS, "*.yml")):
        with open(path) as f:
            assert "CLAUDE_CODE_OAUTH_TOKEN" not in f.read(), path
