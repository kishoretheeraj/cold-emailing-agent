#!/usr/bin/env bash
# Runs Playwright e2e tests before every git push.
# Redirects test output to stderr so stdout is clean for the JSON decision.
# Playwright's webServer config auto-manages the dev server.

cd /Users/kishoretheeraj/Documents/cold-email-agent/contact-manager

if npm run test:e2e 1>&2; then
  exit 0
else
  printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"Playwright e2e tests failed — fix the failures before pushing."}}\n'
  exit 0
fi
