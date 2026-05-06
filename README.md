# Cold Email Agent

Runs daily on GitHub Actions. Reads contacts from Supabase, generates
personalized emails via Claude API, and creates Gmail drafts for review.

Two modes in one pipeline:
- **Mode A (outreach):** Cold intro emails, 4-email sequence
- **Mode B (applied):** Hiring manager emails after applying, 2-email max

---

## Prerequisites

- Python 3.11+
- GitHub account (free)
- Supabase account (free) — supabase.com
- Gmail account with 2FA enabled
- Anthropic API key — console.anthropic.com

---

## Step 1 — Supabase Setup

1. Go to supabase.com → New project
2. Once created: SQL Editor → paste contents of `setup_supabase.sql` → Run
3. Go to Settings → API → copy **Project URL** and **anon public** key

---

## Step 2 — Gmail App Password

1. Enable 2-Factor Authentication on your Google account
2. Go to myaccount.google.com/apppasswords
3. Generate a new app password for "Mail"
4. Copy the 16-character password

---

## Step 3 — Local Setup

```bash
cd cold-email-agent
pip install -r requirements.txt
cp .env.example .env
# Edit .env and fill in all 5 values
```

Test locally before pushing to GitHub:

```bash
python agent.py
```

Check Gmail Drafts — you should see a draft for the sample contacts.
Check Supabase — stage should have updated from `new` to `*_drafted`.

---

## Step 4 — GitHub Setup

```bash
git init
git add .
git commit -m "initial build"
```

Create a **private** repo on github.com, then:

```bash
git remote add origin https://github.com/YOUR_USERNAME/cold-email-agent.git
git push -u origin main
```

---

## Step 5 — Add Secrets to GitHub

GitHub repo → Settings → Secrets and variables → Actions → New repository secret

Add all 5 secrets:

| Name | Value |
|---|---|
| `ANTHROPIC_API_KEY` | Your Anthropic key |
| `GMAIL_ADDRESS` | your@gmail.com |
| `GMAIL_APP_PASSWORD` | 16-char app password |
| `SUPABASE_URL` | From Supabase Settings → API |
| `SUPABASE_ANON_KEY` | From Supabase Settings → API |

---

## Step 6 — Test the GitHub Actions Run

GitHub → Actions tab → Cold Email Agent → Run workflow → Run workflow

Watch the run. If it passes, you're live. Agent runs every weekday at **5:37am EST / 6:37am EDT** (10:37 UTC — off-peak to avoid GitHub Actions queue delays).

---

## Daily Workflow

### Adding a Mode A contact (outreach)

Supabase → Table Editor → contacts → Insert row:

| Field | Value |
|---|---|
| name | Dana Ehrlich |
| email | dana@clearbond.com |
| company | Clearbond |
| role | CEO |
| detail | customs bond SaaS, Tuck MBA background |
| tier | 1 |
| mode | outreach |

Leave all other fields empty. Agent picks it up next morning.

> **Threading:** Follow-up emails automatically appear in the same Gmail thread as the original. The agent saves a `Message-ID` after the first draft and passes it as `In-Reply-To` on every follow-up — no setup needed.

### Adding a Mode B contact (applied to a job)

Supabase → Table Editor → contacts → Insert row:

| Field | Value |
|---|---|
| name | Sarah Kim |
| email | sarah@stripe.com |
| company | Stripe |
| role | Group PM, Financial Infrastructure |
| mode | applied |
| job_title | Senior PM, Financial Infrastructure |
| applied_date | 2026-04-21 |
| job_description | (paste full job description here) |

Agent picks it up next morning, reads the JD, generates 3 matched bullets.

### After you send a draft

In Supabase, update the stage:

| You sent | Update stage to |
|---|---|
| First Touch | `first_touch_sent` |
| Follow-up #1 | `followup1_sent` |
| Follow-up #2 | `followup2_sent` |
| Break-up | `breakup_sent` |
| Applied Intro | `applied_intro_sent` |
| Applied Follow-up | `applied_followup_sent` |

### When someone replies

In Supabase, update `reply_status`:

| Outcome | reply_status |
|---|---|
| They replied | `replied` |
| They're interested | `interested` |
| Call booked | `call_scheduled` |
| Not interested | `dead` |

Agent stops emailing them permanently once any of these are set.

---

## Stage Reference

### Mode A (outreach)
```
new → first_touch_drafted → first_touch_sent
    → followup1_drafted   → followup1_sent
    → followup2_drafted   → followup2_sent
    → breakup_drafted     → breakup_sent → closed
```

### Mode B (applied)
```
new → applied_intro_drafted    → applied_intro_sent
    → applied_followup_drafted → applied_followup_sent → closed
```

---

## Failure Notifications

If the agent or monitor job fails on GitHub Actions, `notify_failure.py` sends an email to your Gmail address with a direct link to the failed run. No additional setup — it uses your existing `GMAIL_APP_PASSWORD` secret.

---

## Auto Reply Monitoring

`monitor.py` runs every 2 hours Monday-Friday at :23 past the hour (off-peak) via GitHub Actions. No manual steps needed.

**How it works:**

1. Fetches all contacts where `reply_status = 'no_reply'` AND `stage` contains `_sent` (email was already sent)
2. For each contact, searches the Gmail INBOX for any email FROM that contact's address
3. If a reply is found, sets `reply_status = 'replied'` in Supabase automatically
4. Labels the reply in Gmail under **Cold Outreach/Replied** so all replies are visible in one place
5. The next morning, `agent.py` sees `reply_status = 'replied'` and skips that contact automatically — no more emails sent

**Gmail labels applied automatically:**

| Action | Gmail label |
|---|---|
| First Touch draft created | Cold Outreach/First Touch |
| Follow-up #1 draft created | Cold Outreach/Follow-up #1 |
| Follow-up #2 draft created | Cold Outreach/Follow-up #2 |
| Break-up draft created | Cold Outreach/Break-up |
| Applied Intro draft created | Cold Outreach/Applied Intro |
| Applied Follow-up draft created | Cold Outreach/Applied Follow-up |
| Reply detected by monitor | Cold Outreach/Replied |

Labels are created automatically if they don't exist — nothing to set up in Gmail.

**You never need to manually update `reply_status` for standard replies.** Only use the manual update (see Daily Workflow above) when you want to mark a contact as `interested`, `call_scheduled`, or `dead`.

---

## Reading Logs

GitHub → Actions tab → click any run → download artifact `agent-log-*`

Sample log output:
```
2026-04-21 08:00 EST | START | 12 contacts | 8 outreach | 4 applied
2026-04-21 08:00 EST | [OUTREACH] Dana Ehrlich | Clearbond | send_first_touch | DRAFTED | followup: 2026-04-26
2026-04-21 08:00 EST | [APPLIED]  Sarah Kim    | Stripe    | send_applied_intro | DRAFTED | job: Senior PM
2026-04-21 08:00 EST | DONE | 2 drafted | 10 skipped | 0 errors | 48s
```
