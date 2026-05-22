#!/usr/bin/env bash
# Post-commit: remind Claude to update CLAUDE.md and memory when code changes.
# Reads the tool-use JSON from stdin to confirm this was a git commit command.

INPUT=$(cat)
CMD=$(echo "$INPUT" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('tool_input',{}).get('command',''))" 2>/dev/null || echo "")

# Only proceed if the bash call included a git commit
echo "$CMD" | grep -qE "git commit" || exit 0

CHANGED=$(git diff HEAD~1 --name-only 2>/dev/null || echo "")
CODE_COUNT=$(echo "$CHANGED" | grep -cE "\.(py|ts|tsx|js)$" 2>/dev/null || echo "0")

# Nothing code-related changed — skip all reminders
[ "$CODE_COUNT" -gt 0 ] || exit 0

CLAUDE_CHANGED=$(echo "$CHANGED" | grep -c "^CLAUDE\.md$" 2>/dev/null || echo "0")

if [ "$CLAUDE_CHANGED" -eq 0 ]; then
  echo "POST-COMMIT: $CODE_COUNT code file(s) committed but CLAUDE.md was not updated."
  echo "  -> Check if this commit changed: resilience patterns, decision invariants, module layout, prompt keys, DB schema, or test patterns. Update CLAUDE.md then add a follow-up commit."
fi

echo "POST-COMMIT: Verify memory is current — update ~/.claude/projects/-Users-kishoretheeraj-Documents-cold-email-agent/memory/ if this commit ships a new feature, changes a pattern, or fixes a non-obvious bug."
