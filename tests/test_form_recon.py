"""scripts/form_recon.py: the structure report holds labels, kinds and roles, never a value, and
recon presses only the entry control."""

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import form_recon  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "universal"


def test_summarize_keeps_structure_and_drops_values():
    inventory = [{"label": "Email", "kind": "input", "required": True, "options": [], "value": "secret@x.com",
                  "name": "email", "autocomplete": "", "input_type": "email", "selector": "#e"},
                 {"label": "Referrer's email", "kind": "input", "required": False, "options": []}]
    report = form_recon.summarize("https://careers.fixtureco.com/apply", "form", inventory,
                                  ["Submit application", "Back", "Next", "Apply with LinkedIn"], ["boards.greenhouse.io"])
    assert report["host"] == "careers.fixtureco.com"
    assert report["fields"][0] == {"label": "Email", "kind": "input", "required": True, "contact": "email", "options": 0}
    assert report["fields"][1]["contact"] is None
    assert report["required"] == 1
    assert report["button_roles"] == {"submit": 1, "other": 1, "next": 1, "third_party": 1}
    assert "secret@x.com" not in json.dumps(report)


def test_recon_presses_only_the_entry_control(tmp_path):
    sync_api = pytest.importorskip("playwright.sync_api")
    executable = "/opt/pw-browsers/chromium" if os.path.exists("/opt/pw-browsers/chromium") else None
    with sync_api.sync_playwright() as p:
        try:
            browser = p.chromium.launch(executable_path=executable)
        except Exception as exc:
            pytest.skip(f"no launchable Chromium here: {exc}")
        report = form_recon.recon((FIXTURES / "posting.html").as_uri(), browser)
        browser.close()
    assert report["landing"]["state"] == "posting"
    after = report["after_entry"]
    assert after["state"] == "form"
    assert {f["contact"] for f in after["fields"]} >= {"first_name", "last_name", "email"}
    assert "Submit application" in after["buttons"]
