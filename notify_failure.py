"""
Send a self-email when a workflow step or systemd unit fails.
Invoked from `if: failure()` steps in the GitHub Actions workflows, and from
the Beelink's `notify-failure@.service` (OnFailure= target, FAILED_UNIT=%i).
Uses GMAIL_APP_PASSWORD (already a repo secret) so no new credentials.
"""

import os
import shlex
import smtplib
import socket
import sys
from email.message import EmailMessage


def _github_actions_failure():
    workflow = os.environ.get("GITHUB_WORKFLOW", "Cold Email Agent")
    run_id = os.environ.get("GITHUB_RUN_ID", "?")
    repo = os.environ.get("GITHUB_REPOSITORY", "?")
    server = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
    branch = os.environ.get("GITHUB_REF_NAME", "?")
    run_url = f"{server}/{repo}/actions/runs/{run_id}"

    subject = f"[FAILED] {workflow} | run {run_id}"
    body = (
        f"Workflow {workflow} failed.\n\n"
        f"Repo:   {repo}\n"
        f"Branch: {branch}\n"
        f"Run:    {run_url}\n"
    )
    return subject, body


def _systemd_unit_failure(failed_unit):
    hostname = socket.gethostname()
    subject = f"[FAILED] {failed_unit} on {hostname}"
    body = (
        f"systemd unit {failed_unit} failed on {hostname}.\n\n"
        f"Logs: journalctl -u {shlex.quote(failed_unit)}\n"
    )
    return subject, body


def _no_context_notice():
    # Nothing failed here: with no GITHUB_RUN_ID and no FAILED_UNIT there is no
    # run to link to, and a [FAILED] subject would be a false alarm.
    hostname = socket.gethostname()
    subject = f"[NOTICE] notify_failure.py invoked without failure context on {hostname}"
    body = (
        "This email was sent because notify_failure.py ran with neither GitHub "
        "Actions context (GITHUB_RUN_ID) nor systemd context (FAILED_UNIT). "
        "This is usually a manual test invocation, not a real failure.\n\n"
        f"Host:   {hostname}\n"
        f"Cwd:    {os.getcwd()}\n"
        f"Argv:   {sys.argv}\n"
    )
    return subject, body


def main():
    gmail = os.environ["GMAIL_ADDRESS"]
    password = os.environ["GMAIL_APP_PASSWORD"]

    failed_unit = os.environ.get("FAILED_UNIT")
    if failed_unit:
        subject, body = _systemd_unit_failure(failed_unit)
    elif os.environ.get("GITHUB_RUN_ID"):
        subject, body = _github_actions_failure()
    else:
        subject, body = _no_context_notice()

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = gmail
    msg["To"] = gmail
    msg.set_content(body)

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
        s.login(gmail, password)
        s.send_message(msg)


if __name__ == "__main__":
    main()
