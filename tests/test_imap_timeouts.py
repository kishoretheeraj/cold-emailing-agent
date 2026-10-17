"""Every IMAP connection carries a socket timeout. Without one, a stalled Gmail connection blocks
until the workflow's own timeout kills the run (15 min for monitor.yml, 30 for daily_agent.yml),
taking the rest of that pass with it."""

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SOURCES = sorted(p for p in ROOT.glob("*.py")) + sorted((ROOT / "scripts").glob("*.py"))


def _imap_calls():
    for path in SOURCES:
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr in ("IMAP4_SSL", "IMAP4")):
                yield f"{path.name}:{node.lineno}", node


CALLS = list(_imap_calls())


def test_the_scan_finds_the_known_connections():
    assert {where.split(":")[0] for where, _ in CALLS} >= {"gmail.py", "monitor.py"}


@pytest.mark.parametrize("where,node", CALLS, ids=[w for w, _ in CALLS])
def test_imap_connection_has_a_timeout(where, node):
    assert any(k.arg == "timeout" for k in node.keywords), f"{where} opens IMAP with no timeout"
