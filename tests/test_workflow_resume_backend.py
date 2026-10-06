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


def test_jobright_scoring_step_queues_for_the_beelink():
    workflow = _read("jobright_pull.yml")
    step = workflow.split("- name: Score newly-discovered jobs", 1)[1].split("- name:", 1)[0]
    assert "run: python job_pick.py" in step
    assert "RESUME_CLAUDE_BACKEND: subscription" in step


def test_no_workflow_holds_a_subscription_token():
    # The subscription token lives only in the Beelink's /etc/job-agent/claude.env. A workflow
    # holding it would bill GitHub-run work against the same subscription window.
    for path in glob.glob(os.path.join(_WORKFLOWS, "*.yml")):
        with open(path) as f:
            assert "CLAUDE_CODE_OAUTH_TOKEN" not in f.read(), path
