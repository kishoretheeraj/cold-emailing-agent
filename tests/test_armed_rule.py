"""APPLY_AGENT_ARMED=1 may be set in exactly two tracked files: the GitHub submit workflow (being
retired) and the Beelink's apply-submit.service. Docs (.md) and tests (which set it transiently with
mocker.patch.dict) are exempt; anything else that sets it could cause an unapproved real submission."""

import re
import subprocess
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_ASSIGN = re.compile(r"""APPLY_AGENT_ARMED["']?\s*[:=]\s*["']?1\b""")
_ALLOWED = {".github/workflows/apply_agent_submit.yml", "deploy/beelink/systemd/apply-submit.service"}


def test_armed_is_set_only_in_the_two_submit_runners():
    files = subprocess.run(["git", "ls-files"], cwd=_ROOT, capture_output=True, text=True,
                           check=True).stdout.splitlines()
    setters = set()
    for name in files:
        if name.startswith("tests/") or name.endswith((".md", ".png", ".jpg", ".pdf", ".docx", ".ttf")):
            continue
        path = _ROOT / name
        if not path.is_file():
            continue
        try:
            text = path.read_text()
        except (UnicodeDecodeError, OSError):
            continue
        code = "\n".join(l for l in text.splitlines() if not l.lstrip().startswith(("#", "//", "--")))
        if _ASSIGN.search(code):
            setters.add(name)
    assert setters == _ALLOWED
