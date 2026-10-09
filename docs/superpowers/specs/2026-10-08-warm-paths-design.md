# Warm paths: connecting the cold-email agent to the application pipeline

Date: 2026-10-08. Status: revised after advisor review (APPROVE WITH CHANGES, 19 points, all addressed; §7 maps each one).

## 1. Problem

At about 50 applications a day, the pipeline now finds roles, builds the documents and submits them. Every one of those applications still goes in cold. The cold-email agent (`agent.py`, modes `outreach` / `applied` / `networking`) is a separate system. It drafts Gmail emails to contacts that the user types in, and it knows nothing about which jobs were applied to.

The user's request: use what works for people who apply by hand, such as referrals, alumni, people in the same role, hiring managers and department leads. Connect the two systems so that each application can grow a warm path. The cold-email agent must keep working on its own.

## 2. What the evidence says

Rigorous data on outreach after applying is thin. Most "3x/10x" figures come from vendor blogs, are unsourced and contradict each other. The design uses only the findings below, each labeled with how strong it is.

1. **Referrals: measured, peer-reviewed.**
   - Burks, Cowgill, Hoffman and Housman (QJE 2015) looked at nine large firms. Referred applicants were more likely to be hired and to accept offers, and they quit 10–30% less.
   - Brown, Setren and Topa (JOLE 2016) found the same for hiring and tenure.
2. **Referral timing: vendor summary plus inference.**
   - A vendor blog (Metaview) summarizes tech-company data in which referred applicants got a recruiter's first detailed review more than three times as often.
   - *Inference, not measured:* a referral helps most if it is registered before that first review. This is the reason for the referral-first hold (§3.5). The hold is optional and never blocks anything.
3. **Weak ties: measured, plausibly analogous.**
   - Rajkumar et al. (Science 2022) ran randomized experiments on 20M LinkedIn users. Moderately weak ties produced the most job mobility, especially in digital industries.
   - Those were ties inside users' existing networks. A stranger who shares a school is a non-tie, so applying the result to alumni is plausible, not shown.
4. **Practitioner method: The 2-Hour Job Search** (Dalton; used by Duke Fuqua and Wharton career services).
   - Rank employers by alumni, motivation and postings.
   - Send a short email that asks for insight, not a job.
   - Follow up on a fixed clock.
   - Referrals come from the "boosters" who reply.
   - The `networking` mode already has this shape. The step that turns a reply into a referral is the reply draft (§3.7).
5. **Hiring-manager email after applying: anecdotal** (Blind and Levels.fyi threads).
   - One credible counterpoint: new-grad hiring is often run by a central team.
   - So the hiring manager is one target among several. The `applied` mode (2 emails max) fits.

Sources:
- https://experts.umn.edu/en/publications/the-value-of-hiring-through-employee-referrals/
- https://www.iza.org/publications/dp/8175/imprint
- https://www.metaview.ai/resources/blog/do-referred-candidates-interview-better
- https://digitaleconomy.stanford.edu/publication/a-causal-test-of-the-strength-of-weak-ties
- https://www.imperial.ac.uk/business-school/blogs/careers/the-2-hour-job-search-key-takeaways-steve-daltons-imperial-masterclass
- https://alumni.stanford.edu/career-connections/cold-contacting-an-alum-here-s-what-you-should-do
- https://www.tryexponent.com/blog/guide-to-tech-job-employee-referral
- https://www.levels.fyi/community/thread/h5DgGo/should-i-email-a-recruiter-after-applying

## 3. Design

### 3.1 Data model (migration `20261010000000`)

- `contacts.job_application_id BIGINT NULL REFERENCES job_applications(id) ON DELETE SET NULL`, indexed.
  - One person is one thread, so a contact links to at most one application.
  - The legacy `job_applications.contact_id` is left alone.
- `contacts.relationship TEXT NULL CHECK (relationship IN ('hiring_manager','leader','recruiter','alum','team_member','other'))`. Every existing row stays NULL.
- Trigger `contacts_link_guard` (`BEFORE INSERT OR UPDATE OF job_application_id`). It enforces the link rules in the database, because `contacts` has RLS disabled and several anon-key paths write to it. When `job_application_id` is newly set (changed and not NULL):
  - the contact must be at `stage='new'` with `reply_status='no_reply'` (no mid-thread re-gating; advisor point 4);
  - the application must not already have 3 linked, non-deleted contacts (`WARM_MAX_PEOPLE_PER_APPLICATION`, mirrored in TypeScript and Python and checked by a static test);
  - the application must not be `rejected` or `withdrawn`.
  - Unlinking (setting NULL) is always allowed.
- `job_applications.referral_hold_until TIMESTAMPTZ NULL`.
  - Anon and authenticated have no column UPDATE or INSERT grant on it. It is written only by `hold_for_referral(p_id, p_days)` and `release_referral_hold(p_id)`, both `SECURITY DEFINER` and EXECUTE-granted to anon and authenticated.
  - `hold_for_referral` clamps `p_days` to 1..14. It only acts on a row whose `automation_status` is `ready_for_review` and whose `approved_at` IS NULL, and it sets `now() + p_days`. Calling it again replaces the date (an extension, still at most 14 days from now).
  - The column is outside `preview_revision_hash`. The advisor verified the hash covers only the preview, the document refs and `documents_version`, and the dry run asserts it.

There are no new tables, and nothing new on `contacts.stage`.

### 3.2 Invariants

- **I1. The cold-email agent is unchanged for unlinked contacts.** `decide_action` and `_skip_reason` return exactly today's results when `job_application_id` is absent or NULL. A parametrized sweep covers every mode × stage × reply × follow-up date.
- **I2. "I applied" is never claimed early.** An `applied`-mode linked contact is skipped until the application's `stage` is `applied`, `phone_screen`, `onsite`, `offer` or `accepted`. Skip reason: "application not submitted yet".
- **I3. No mail into a closed application.** An `applied`-mode linked contact whose application is `rejected` or `withdrawn` is skipped ("application closed").
  - `networking` contacts are not gated, because the relationship outlives the opening.
  - Their hooks never mention the role (advisor point 1), so nothing goes stale.
- **I4. Fail closed on the link.** A failed lookup, or a non-NULL id missing from the response, skips that `applied`-mode contact ("linked application unreadable"). `ON DELETE SET NULL` already nulls the column on a real delete. Ids are read in chunks of 200.
- **I5. Drafts only.** There is no send path, no LinkedIn automation and no people scraping.
- **I6. Bounded.**
  - At most 3 linked people per application (database trigger).
  - At most `outreach_per_company_30d` people created per company in 30 days (default 5, from `job_search_preferences`). This one is a route-level guardrail; it is not a database invariant.
- **I7. Duplicates are refused, never merged.**
  - `email` is UNIQUE, soft-deleted rows included. The add-person route checks first.
  - A live match returns 409 with "Link instead" (when its stage allows it). A soft-deleted match returns 409 with the existing restore message.
  - Every read and cap filters `deleted_at IS NULL`.
- **I8. Contacts are read in full.** `db.get_all_contacts` pages with `.range()` in steps of 1000 (advisor point 3). Before this change it was silently capped at PostgREST's row limit.

### 3.3 Who, and in which mode

The add-person route builds the contact from the user's choices. The mapping is in `src/lib/warmPaths.ts`, a pure module.

| relationship | mode | fields written |
|---|---|---|
| `hiring_manager`, `leader`, `recruiter` | `applied` | `role` = their title (optional field, default ""); `job_title` = the application's role; `job_description` = the posting description cut to 1500 chars. These are persisted at creation so the Prompt Lab preview matches. `applied_date` is filled by the agent at draft time from the application (I2 makes it exist). |
| `alum`, `team_member`, `other` | `networking` | `connection_context` = what the user typed. The form offers a suggestion as a placeholder plus a "Use suggestion" button, and never auto-fills the field (contact-manager rule). Suggestions never name the role: alum gives "Fellow {school} alum"; team_member gives "Works in {function} at {company}". `dartmouth` = true when the chosen school is Dartmouth, Thayer or Tuck. |

Every created contact gets the following values.
- `tier` comes from the form, default 2, the same as any contact. Tier 1 turns on the critic, which costs extra API calls, so it is the user's choice (advisor point 9).
- `stage='new'`, `reply_status='no_reply'`, `company` = the application's company, and `notes` holds the LinkedIn URL if one was given.

Schools are an explicit list in `warmPaths.ts`: Dartmouth College (Thayer, Tuck), and Anna University. `master.json` names only the institutions.

**Agent-side fill** (`agent._with_application_fields`). For a linked contact whose `job_title`, `applied_date` or `job_description` is empty, the agent fills it in memory from the application, which is defensive for contacts linked rather than created. Nothing is persisted, and the contact's own values win. The Prompt Lab does not mirror this. For a linked contact it shows its usual fallback for `applied_date`, which is documented next to the Voice DNA mirror note.

### 3.4 Finding the people (the human picks; the system narrows)

`ApplicationDetailSheet` gets a **People** section on every application past `saved`, including `ready_for_review` (advisor point 5). It contains:

1. **Linked:** each linked contact with relationship, stage, reply status and an Unlink button.
2. **Already in your contacts:** live, unlinked contacts at `stage='new'` whose `companyKey` matches, each with a one-tap Link.
   - `companyKey` mirrors `job_identity.company_key`. Both sides test it against `tests/fixtures/company_keys.json`.
   - Contacts already mid-thread at the company are listed as "already in touch" with no Link button.
3. **Search:** deterministic links that open the user's own browser:
   - LinkedIn people search for "{company} Dartmouth", "{company} Anna University" and "{company} {function}";
   - a Google `site:linkedin.com/in` search for each of the same.
4. **Named in the posting:** emails found in `posting_snapshot` text. Generic inboxes (`careers@`, `jobs@`, `noreply@`, `recruiting@`, `talent@`, `hr@`, `privacy@`, `accommodations@`) are labeled as inboxes.
5. **Add person:** name, email, relationship, their title, school (alum only), tier, LinkedIn URL.
   - An email guess is offered only when at least 2 known addresses at the company share a domain and a pattern (advisor point 17).
   - The guess is labeled and never auto-filled.

Routes (all behind the operator session proxy, numeric ids validated):
- `GET /api/applications/[id]/people`: the linked contacts, suggestions, posting emails, known patterns and the remaining cap.
- `POST /api/applications/[id]/people`: create.
- `POST /api/applications/[id]/people/link` and `.../unlink` with `{ contact_id }`.
  - These are plain anon updates of `job_application_id`.
  - The trigger is the backstop; its error comes back as 409.

### 3.5 Referral first (the hold)

A Ready card gets **"Ask for a referral first"**, which calls `POST /api/applications/[id]/hold`.

- `hold_for_referral` runs with `referral_hold_days` from `job_search_preferences` (default 10, clamped to 14).
- The People sheet then opens.
- Held cards stay in the Ready list, sorted last, with a "Waiting on a referral until {date}" badge and a "Stop waiting" link. Nothing is hidden (advisor point 18), so an abusive hold cannot make a row disappear.
- Submit stays available on a held card. Approving it is the normal signed approval, and the hold is simply ignored from then on.
- Ten days covers the networking cadence: the draft the next weekday morning, the user's send, and the 6-day follow-up.

### 3.6 Agent (Python)

These are implemented test-first:
- `db.get_application_states(ids)`: chunked, retried, and raises on failure.
- `agent._attach_applications`: one lookup per run; `_application_error` on failure; a missing id gives `None`, which reads as unreadable.
- `_application_gate` sits in `_decide_applied` and `_skip_reason`.
- `_with_application_fields`.
- The `outreach` and `networking` branches are byte-identical.

### 3.7 Reply drafts carry the ask (advisor point 15)

`reply_drafter.draft_reply` handles a positive reply from a linked contact.
- The application is looked up with `get_application_states`.
- When it reads, an `APPLICATION CONTEXT` block is appended to the reply prompt after formatting, the same way the research block is added. The block gives the role, the company, the posting URL and whether the application has been submitted yet.
- It adds one instruction: if the reply is warm, close with one specific, low-pressure ask. That is a referral if it isn't submitted yet, or passing it to the hiring manager if it is.
- A failed lookup only drops the block and never blocks the reply draft.
- Templates and prompt keys are unchanged, so nothing new needs mirroring in the Lab (the Lab does not preview replies).

### 3.8 Prioritizing scarce warm paths (advisor point 16)

Ready cards show "You know N people here", from the live contacts whose `companyKey` matches, next to the existing pick badge. The sponsorship signal on cards is a follow-up: matching `company_intel` would need a TypeScript copy of `entity_resolution.normalize()` plus alias groups, and the governance rules for that signal argue for doing it deliberately.

### 3.9 Learning what works for this user

`engagement_report.py` gets two sections (read-only, small-`n` rule):

- **Applications with vs without outreach.** "Reached out" means a linked contact with `latest_message_id` set (a send was detected) or `classifier_status` set (they replied), which counts contacts that replied and moved to reply stages (advisor point 12). The interview rate is the share at `phone_screen`, `onsite`, `offer` or `accepted`. The report prints two caveats: warm paths go to the roles the user cares most about, so the comparison is confounded by selection; and interview counts depend on the user updating `stage`.
- **Reply rate by relationship**, over linked contacts who were reached out to.

## 4. Non-goals (and why)

- **Automated people discovery** (LinkedIn scraping, Insider Connections, paid enrichment). It puts the real account at risk, stores third-party personal data, and needs paid keys.
- **Creating contacts without the user picking the person.** A wrong-person email to a hiring manager cannot be unsent.
- **LinkedIn DMs or connection notes.** The stack is email-only and `contacts.email` is NOT NULL. This is a possible later addition.
- **Changing an existing contact's mode on link.** Linking sets only the link.

## 5. Testing

- **Python.**
  - The I1 sweep, the I2/I3 matrix, and I4 (failed lookup, missing id).
  - `get_application_states`: chunking, odd snapshots, and raising.
  - `get_all_contacts` paging.
  - `agent.run()` end to end at each application stage.
  - The reply drafter with and without application context, with lookup failure, and the block only for linked contacts.
  - Engagement report cohorts, caveats, small `n`, and a never-raises sweep.
  - `company_key` on the shared fixture.
  - The networking suggestion never contains the role.
- **SQL.**
  - Static migration tests.
  - `supabase/tests/warm_paths_dryrun.sql` (`BEGIN`/`ROLLBACK`) covers:
    - every trigger rule (mid-thread refused; 4th person refused; closed application refused; unlink allowed; a soft-deleted contact doesn't count);
    - FK `SET NULL`;
    - the CHECK;
    - anon and authenticated cannot UPDATE `referral_hold_until`;
    - the RPC clamp (0 days and 99 days become 1..14) and its source-state rules;
    - the hold leaves `preview_revision_hash` unchanged and survives `requeue_preview`.
  - Each test is mutation-checked.
- **TypeScript.**
  - `warmPaths.ts`: mapping, `companyKey` fixture, email guess (needs 2 agreeing), search links, posting emails, school-to-Dartmouth.
  - The people routes: 400s, 409 for live and soft-deleted duplicates, caps, link/unlink, the trigger error mapped to 409.
  - The hold route.
  - Queue ordering and the badge.
  - The detail-sheet People section.
- **Playwright.** Add a person, link a known contact, hold a card and stop waiting. `helpers.ts` gets mocks for `job_applications` and the RPCs. Screenshots are reviewed.
- **Simulation** (local Postgres + PostgREST, 1000-row cap):
  - 1,200 contacts (paging past the cap) and 50 applications with 3 people each;
  - the trigger under real writes;
  - the agent decision pass over real rows asserting I2 to I4, with drafting mocked.

## 6. Rollout

- The migration is additive. Old rows are NULL, which I1 makes inert.
- The daily agent needs no new secret.
- The contact-manager deploys with the merge.
- The Beelink is untouched.

## 7. Advisor findings → changes

| # | Finding | Change |
|---|---|---|
| 1 | Hooks contradict "never mention a role"; the UI must not auto-write `connection_context` | Role-free suggestions as a placeholder plus "Use suggestion" (§3.3) |
| 2 | A missing id must fail closed; chunk `in_()` | I4; chunks of 200 (§3.2, §3.6) |
| 3 | `get_all_contacts` is not paginated | I8 |
| 4 | Linking a mid-thread contact re-gates it | Database trigger: link only at `new`/`no_reply` (§3.1) |
| 5 | People must be available before approval | People section on every row past `saved` (§3.4) |
| 6 | Unbounded anon-writable hold | `SECURITY DEFINER` RPC clamped to 14 days; no column grant; held rows stay visible (§3.1, §3.5) |
| 7 | 5 days is shorter than the cadence | 10 days, configurable, extendable to 14 (§3.5) |
| 8 | `company_applied` unused; title missing; schools | Dropped; title field; explicit school list (§3.3) |
| 9 | Tier 1 turns on research and the critic | Tier from the form, default 2 (§3.3) |
| 10 | Prompt Lab divergence | Fields persisted at creation; the `applied_date` gap documented (§3.3) |
| 11 | I6/I7 are not invariants; soft deletes | Trigger for link rules; route guardrails named as such; `deleted_at` handling (§3.1, §3.2) |
| 12 | Wrong report cohort; confounding | `latest_message_id` or `classifier_status`; caveats printed (§3.9) |
| 13–14 | Evidence overclaims | Strength labels in §2 |
| 15 | The reply step lacks context | `APPLICATION CONTEXT` block in the reply draft (§3.7) |
| 16 | No prioritization | "You know N people here"; sponsorship deferred with a reason (§3.8) |
| 17 | Email guess quality | Requires 2 agreeing addresses (§3.4) |
| 18 | Simpler hold UI | Adopted (§3.5) |
| 19 | Test holes | All listed in §5 |

## 8. Second advisor pass → changes

Verdict: APPROVE WITH CHANGES. 15 of the 19 points were closed; the rest:

| # | Finding | Change |
|---|---|---|
| 1 | The fill ran for every mode; an outreach subject reads `job_title` | Fill only in `applied` mode. The trigger refuses links for `outreach` contacts. A test shows the prompts of linked outreach and networking contacts are byte-identical to unlinked ones |
| 2 | The fill wrote `company_applied` | Removed |
| 3 | I8 not implemented | `get_all_contacts` pages `.order("id").range()` |
| 4 | Cap race; restore bypass | The trigger is `SECURITY DEFINER`, locks the application row `FOR UPDATE` before counting, and also fires on `deleted_at` (restoring re-checks the cap). The race is exercised on the stress stack with concurrent links |
| 5 | The reply context was dropped on the preflight retry; asked about closed applications | The block is appended on both the first attempt and the retry. A closed application says so and makes no ask |
| 6 | `applied_date` falls back to today | The PATCH route sets `applied_date` when `stage` becomes `applied` and it is empty |
| 7 | Hold should report a no-op | The RPC raises when no row matches, so the route returns 409 |
| 8 | Python constant; docs | `config.WARM_MAX_PEOPLE_PER_APPLICATION` with a static mirror test. CLAUDE.md documents the columns, trigger, RPCs, `[WARM]` marker and Lab gap |
