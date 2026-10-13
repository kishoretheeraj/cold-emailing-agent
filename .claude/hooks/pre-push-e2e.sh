#!/usr/bin/env bash
# Runs Playwright e2e tests before every git push.
# Redirects test output to stderr so stdout is clean for the JSON decision.
# Playwright's webServer config auto-manages the dev server.

cd /Users/kishoretheeraj/Documents/cold-email-agent/contact-manager

# A dev server killed mid-run leaves .next/dev/lock behind, and the next run's server then refuses to
# start, so every other push failed. Remove the lock only when the process it names is gone.
LOCK=.next/dev/lock
if [ -f "$LOCK" ]; then
  pid=$(sed -n 's/.*"pid":\([0-9][0-9]*\).*/\1/p' "$LOCK")
  if [ -n "$pid" ] && ! kill -0 "$pid" 2>/dev/null; then
    rm -f "$LOCK"
  fi
fi

if npm run test:e2e 1>&2; then
  exit 0
else
  printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"Playwright e2e tests failed — fix the failures before pushing."}}\n'
  exit 0
fi
