"""
Send a self-email when a GitHub Actions workflow step fails.
Invoked from `if: failure()` steps in daily_agent.yml and monitor.yml.
Uses GMAIL_APP_PASSWORD (already a repo secret) so no new credentials.
"""

import os
import smtplib
from email.message import EmailMessage


def main():
    gmail = os.environ["GMAIL_ADDRESS"]
    password = os.environ["GMAIL_APP_PASSWORD"]
    workflow = os.environ.get("GITHUB_WORKFLOW", "Cold Email Agent")
    run_id = os.environ.get("GITHUB_RUN_ID", "?")
    repo = os.environ.get("GITHUB_REPOSITORY", "?")
    server = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
    branch = os.environ.get("GITHUB_REF_NAME", "?")
    run_url = f"{server}/{repo}/actions/runs/{run_id}"

    msg = EmailMessage()
    msg["Subject"] = f"[FAILED] {workflow} | run {run_id}"
    msg["From"] = gmail
    msg["To"] = gmail
    msg.set_content(
        f"Workflow {workflow} failed.\n\n"
        f"Repo:   {repo}\n"
        f"Branch: {branch}\n"
        f"Run:    {run_url}\n"
    )

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
        s.login(gmail, password)
        s.send_message(msg)


if __name__ == "__main__":
    main()
