"""stdin-JSON -> stdout-JSON shim for the dewatermark stage.

Wired by the operator (not by model briefs): RESUME_DEWATERMARK_CMD points at this script.
Contract: reads {"texts": [str, ...]} from stdin, writes {"texts": [str, ...]} to stdout.
Any failure -> exit 1 with only the exception class name on stderr (never a traceback or message, which
could carry resume text); the caller retries once, then fails the stage. A missing/failing shim never
silently passes text through.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from dewatermark import dewatermark


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read())
        texts = payload["texts"]
        if not isinstance(texts, list) or not all(isinstance(t, str) for t in texts):
            raise ValueError("stdin JSON must be {\"texts\": [str, ...]}")
        out = [dewatermark(t) for t in texts]
    except Exception as exc:
        sys.stderr.write(f"shim failed: {type(exc).__name__}\n")
        return 1
    sys.stdout.write(json.dumps({"texts": out}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
