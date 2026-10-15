"""stdin-JSON -> stdout-JSON shim for the paraphrase stage.

Wired by the operator (not by model briefs): RESUME_PARAPHRASE_CMD points at this script.
Contract: reads {"texts": [str, ...], "banned": [str, ...]} from stdin, writes
{"texts": [str, ...]} to stdout. Rewriting goes through the Codex CLI. Any failure -> exit 1 with only
the exception class name on stderr (never a traceback or message, which could carry resume text); the
caller retries once, then fails the stage.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from resume_rewrite import RewriteError, rewrite_bullets


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read())
        texts = payload["texts"]
        banned = payload.get("banned") or []
        if not isinstance(texts, list) or not all(isinstance(t, str) for t in texts):
            raise ValueError("stdin JSON must be {\"texts\": [str, ...]}")
        if not isinstance(banned, list):
            raise ValueError("\"banned\" must be a list of str")
        out = rewrite_bullets(texts, [str(b) for b in banned])
        if len(out) != len(texts):
            raise RewriteError("rewriter returned the wrong number of texts")
    except Exception as exc:
        sys.stderr.write(f"shim failed: {type(exc).__name__}\n")
        return 1
    sys.stdout.write(json.dumps({"texts": out}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
