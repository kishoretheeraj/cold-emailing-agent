"""Rewrite tailored resume bullets through the Codex CLI."""

from __future__ import annotations

import re
import shutil
import string
import subprocess


class RewriteError(Exception):
    """Raised when resume bullets cannot be rewritten through Codex."""


REWRITE_TIMEOUT = 180
OVERLAP_RETRY_THRESHOLD = 0.5

_NUMBERED_BULLET_RE = re.compile(r"^\s*\d+[.)]\s+(.+?)\s*$")


def codex_available() -> bool:
    """Return whether the Codex CLI is available on PATH."""
    return shutil.which("codex") is not None


def build_rewrite_prompt(
    bullets: list[str],
    banned: list[str],
    aggressive: bool = False,
) -> str:
    """Build the prompt used to rewrite resume bullets."""
    numbered_bullets = "\n".join(
        f"{index}. {bullet}" for index, bullet in enumerate(bullets, start=1)
    )
    banned_words = ", ".join(banned) if banned else "(none)"

    aggressive_instruction = ""
    if aggressive:
        aggressive_instruction = (
            "\nRestructure aggressively: lead with the outcome instead of the action, "
            "or with the action instead of the outcome. Aim for under 30% word "
            "overlap with each original bullet."
        )

    return (
        "Rewrite every resume bullet below as a full structural paraphrase.\n"
        "\n"
        "Rules:\n"
        "- Change clause order, connectors, and transition words.\n"
        "- Move the metric to a different position where natural.\n"
        "- Replace both content words and function words where meaning allows.\n"
        "- Preserve EVERY fact, number, percentage, dollar amount, date, "
        "company name, product name, and technical term exactly.\n"
        "- Do not add, remove, weaken, strengthen, or alter any claim.\n"
        "- Keep each rewritten bullet at 20 words or fewer.\n"
        "- Use plain, direct language.\n"
        f"- Never use these banned words: {banned_words}.\n"
        "- Keep the bullets in their original order.\n"
        "- Output ONLY a numbered list in the form `1. ...`, with one item per "
        "line. Do not include commentary or headers."
        f"{aggressive_instruction}\n"
        "\n"
        "Bullets:\n"
        f"{numbered_bullets}"
    )


def _call_codex_rewriter(prompt: str, timeout: int) -> str:
    """Run the Codex CLI and return its standard output."""
    codex_bin = shutil.which("codex")
    if codex_bin is None:
        raise RewriteError("codex CLI not found")
    try:
        result = subprocess.run(
            [codex_bin, "exec", "--skip-git-repo-check", prompt],
            capture_output=True,
            text=True,
            timeout=timeout,
            # codex exec reads "additional input" from stdin; without DEVNULL
            # it blocks forever on an inherited open pipe.
            stdin=subprocess.DEVNULL,
        )
    except FileNotFoundError as exc:
        raise RewriteError("codex CLI not found") from exc
    except subprocess.TimeoutExpired as exc:
        raise RewriteError(
            f"codex rewrite timed out after {timeout} seconds"
        ) from exc

    if result.returncode != 0:
        stderr = result.stderr or ""
        detail = stderr[-500:]
        message = f"codex rewrite failed with exit code {result.returncode}"
        if detail:
            message = f"{message}: {detail}"
        raise RewriteError(message)

    return result.stdout


def rewrite_bullets(
    bullets: list[str],
    banned: list[str],
    aggressive: bool = False,
    timeout: int = REWRITE_TIMEOUT,
) -> list[str]:
    """Rewrite bullets through Codex and parse the numbered response."""
    prompt = build_rewrite_prompt(bullets, banned, aggressive=aggressive)
    output = _call_codex_rewriter(prompt, timeout)
    rewritten = parse_rewritten_bullets(output)

    if not rewritten:
        raise RewriteError("codex returned no parseable rewritten bullets")

    return rewritten


def parse_rewritten_bullets(text: str) -> list[str]:
    """Extract numbered bullets while ignoring unrelated output lines.

    Takes the first occurrence of each bullet number: `codex exec` prints
    the final message twice (before and after the token-usage summary),
    so without dedup every bullet would be counted twice.
    """
    numbered: dict[int, str] = {}

    for line in text.splitlines():
        match = _NUMBERED_BULLET_RE.match(line)
        if not match:
            continue
        num = int(re.match(r"^\s*(\d+)[.)]", line).group(1))
        bullet = match.group(1).strip()
        if bullet and num not in numbered:
            numbered[num] = bullet

    return [numbered[num] for num in sorted(numbered)]


def shared_4gram_ratio(before: list[str], after: list[str]) -> float:
    """Return the fraction of source four-grams shared by rewritten text."""

    def fourgrams(bullets: list[str]) -> set[tuple[str, str, str, str]]:
        tokens = [
            token.lower().strip(string.punctuation)
            for token in " ".join(bullets).split()
        ]
        tokens = [token for token in tokens if token]
        return {
            tuple(tokens[index : index + 4])
            for index in range(len(tokens) - 3)
        }

    before_grams = fourgrams(before)
    if not before_grams:
        return 0.0

    after_grams = fourgrams(after)
    return len(before_grams & after_grams) / len(before_grams)


def swap_bullets(
    md: str,
    old_bullets: list[str],
    new_bullets: list[str],
) -> str:
    """Replace matching Markdown bullets sequentially."""
    trailing_newline = md.endswith("\n")
    lines = md.splitlines()
    expected_index = 0

    for line_index, line in enumerate(lines):
        if expected_index >= len(old_bullets):
            break

        if not line.startswith("- "):
            continue

        if line[2:].strip() != old_bullets[expected_index]:
            continue

        lines[line_index] = "- " + new_bullets[expected_index]
        expected_index += 1

    result = "\n".join(lines)
    if trailing_newline:
        result += "\n"
    return result
