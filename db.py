from datetime import date, datetime, timedelta, timezone
import logging
import re
import time
import uuid
import supabase._sync.client as _sc

log = logging.getLogger(__name__)

# Patch: Supabase updated their key format (sb_publishable_*) but the Python
# client still validates against JWT regex. Widen the check by monkeypatching
# re.match for the duration of create_client's internal validation call.
_original_create = _sc.create_client

def _create_patched(supabase_url, supabase_key, options=None):
    orig_re_match = re.match
    def lenient_match(pattern, string, *a, **kw):
        if "A-Za-z0-9-_=" in str(pattern) and string.startswith("sb_"):
            return True
        return orig_re_match(pattern, string, *a, **kw)
    re.match = lenient_match
    try:
        return _original_create(supabase_url, supabase_key, options)
    finally:
        re.match = orig_re_match

from supabase import create_client as _orig_create_client
import config
import job_identity
from config import SUPABASE_URL, SUPABASE_ANON_KEY

_client = None

# Retry wrapper for Supabase calls — same shape as emailer._call_claude.
# Network blips and 5xx are rare but kill the whole run when get_all_contacts
# is the first call out of the gate.
def _retry(fn, give_up=None):
    for attempt in range(3):
        try:
            return fn()
        except Exception as exc:
            if attempt < 2 and not (give_up and give_up(exc)):
                time.sleep(2 ** (attempt + 1))
                continue
            raise


def _is_unique_violation(exc):
    # A unique violation is an answer, not a blip: retrying the same insert only loses again.
    return getattr(exc, "code", None) == "23505" or "23505" in str(exc)

def get_client():
    global _client
    if _client is None:
        _client = _create_patched(SUPABASE_URL, SUPABASE_ANON_KEY)
    return _client

_CONTACT_PAGE = 1000


def get_all_contacts():
    """Fetch all live contacts, paged past PostgREST's row cap. Ordered by id so no row is skipped
    or repeated between pages (a repeated contact could be drafted twice in one run)."""
    rows = []
    start = 0
    while True:
        result = _retry(lambda: get_client().table("contacts").select("*").is_("deleted_at", "null")
                        .order("id").range(start, start + _CONTACT_PAGE - 1).execute())
        page = result.data or []
        rows.extend(page)
        if len(page) < _CONTACT_PAGE:
            return rows
        start += _CONTACT_PAGE

_APPLICATION_STATE_COLUMNS = "id,stage,role,company,applied_date,job_url,posting_snapshot"


def get_application_states(application_ids):
    """Stage and prompt fields of the job applications that contacts are linked to, keyed by id.
    Raises on failure: the agent treats an unreadable link as a reason to skip (warm paths I4)."""
    states = {}
    ids = list(application_ids)
    for start in range(0, len(ids), 200):
        chunk = ids[start:start + 200]
        result = _retry(lambda: get_client().table("job_applications").select(_APPLICATION_STATE_COLUMNS)
                        .in_("id", chunk).execute())
        for row in result.data or []:
            snapshot = row.get("posting_snapshot")
            description = snapshot.get("description") if isinstance(snapshot, dict) else None
            states[row["id"]] = {
                "id": row["id"], "stage": row.get("stage"), "role": row.get("role"),
                "company": row.get("company"), "applied_date": row.get("applied_date"),
                "job_url": row.get("job_url"),
                "posting_description": description if isinstance(description, str) else "",
            }
    return states


def update_contact(contact_id, stage, followup_days=None, template=None,
                   expected_stage=None, clear_followup_date=False):
    """Update contact stage, followup_date, and last_emailed after a draft is created."""
    updates = {
        "stage": stage,
        "last_emailed": str(date.today()),
    }
    if followup_days is not None:
        updates["followup_date"] = str(date.today() + timedelta(days=followup_days))
    elif clear_followup_date:
        updates["followup_date"] = None
    if template:
        updates["template_current"] = template

    def _do_update():
        q = get_client().table("contacts").update(updates).eq("id", contact_id)
        if expected_stage is not None:
            q = q.eq("stage", expected_stage)
        return q.execute()

    result = _retry(_do_update)
    if expected_stage is not None and not result.data:
        log.warning(f"Stage changed externally, skipping update for contact {contact_id}")

def close_contact(contact_id):
    """Mark a contact as closed — no more emails."""
    _retry(lambda: get_client().table("contacts").update({
        "stage": "closed",
        "last_emailed": str(date.today()),
    }).eq("id", contact_id).execute())

def get_sent_contacts():
    """Fetch contacts where an email was sent but no reply recorded yet."""
    result = _retry(lambda: (
        get_client()
        .table("contacts")
        .select("*")
        .is_("deleted_at", "null")
        .eq("reply_status", "no_reply")
        .like("stage", "%_sent%")
        .execute()
    ))
    return result.data or []

def get_drafted_contacts():
    """Fetch contacts whose stage is currently in a *_drafted state."""
    from constants import DRAFTED_STAGES
    result = _retry(lambda: (
        get_client()
        .table("contacts")
        .select("*")
        .is_("deleted_at", "null")
        .in_("stage", DRAFTED_STAGES)
        .execute()
    ))
    return result.data or []

def update_reply_status(contact_id, status):
    """Update reply_status for a single contact."""
    _retry(lambda: get_client().table("contacts").update({
        "reply_status": status,
    }).eq("id", contact_id).execute())

def save_thread_info(contact_id, message_id, subject, gmail_thread_id=None):
    """Save Message-ID, subject, and optionally X-GM-THRID of the first email."""
    row = {
        "message_id": message_id,
        "original_subject": subject,
        "latest_message_id": message_id,  # first-touch: both start equal
    }
    if gmail_thread_id is not None:
        row["gmail_thread_id"] = gmail_thread_id
    _retry(lambda: get_client().table("contacts").update(row).eq("id", contact_id).execute())

def update_message_id(contact_id, message_id):
    """Update message_id when the sent email's actual ID differs from the draft's."""
    _retry(lambda: get_client().table("contacts").update({
        "message_id": message_id,
    }).eq("id", contact_id).execute())

def update_latest_message_id(contact_id, message_id):
    """Update latest_message_id after each sent detection for sequential In-Reply-To chaining."""
    _retry(lambda: get_client().table("contacts").update({
        "latest_message_id": message_id,
    }).eq("id", contact_id).execute())

def update_gmail_thread_id(contact_id, gmail_thread_id):
    """Store the X-GM-THRID captured at draft creation for reliable sent detection."""
    _retry(lambda: get_client().table("contacts").update({
        "gmail_thread_id": gmail_thread_id,
    }).eq("id", contact_id).execute())

def get_thread_info(contact_id):
    """Return message_id, latest_message_id, and original_subject for a contact."""
    result = _retry(lambda: get_client().table("contacts").select(
        "message_id, latest_message_id, original_subject"
    ).eq("id", contact_id).execute())
    rows = result.data or []
    return rows[0] if rows else {}

def load_prompts():
    """Load all rows from the prompts table at agent startup."""
    result = _retry(lambda: get_client().table("prompts").select("key, value").execute())
    return {r["key"]: r["value"] for r in (result.data or [])}

def upsert_prompt(key, value):
    """Upsert a single prompts row. Best-effort: logs and returns False on error."""
    from datetime import datetime, timezone
    row = {"key": key, "value": value,
           "updated_at": datetime.now(timezone.utc).isoformat()}
    try:
        _retry(lambda: get_client().table("prompts").upsert(row, on_conflict="key").execute())
        return True
    except Exception as exc:
        log.warning(f"upsert_prompt failed | key={key} | {exc}")
        return False

def get_pause_scope():
    """Return the current pause_scope: 'none', 'agent', or 'all'. Defaults to 'none' on any error."""
    try:
        result = _retry(lambda: (
            get_client()
            .table("system_config")
            .select("value")
            .eq("key", "pause_scope")
            .single()
            .execute()
        ))
        return (result.data or {}).get("value", "none")
    except Exception:
        return "none"

def record_run(status, drafted, skipped, errors, elapsed, failure_reason=None, source="agent"):
    """Insert a row into agent_runs after every run, success or failure."""
    row = {
        "status": status,
        "drafted": drafted,
        "skipped": skipped,
        "errors": errors,
        "elapsed_seconds": elapsed,
        "source": source,
    }
    if failure_reason:
        row["failure_reason"] = failure_reason
    _retry(lambda: get_client().table("agent_runs").insert(row).execute())

# ── agent_events helpers ───────────────────────────────────────────────────────

def log_agent_event(event_type, contact_id=None, contact_name=None, status="success",
                    run_id=None, error_message=None, metadata=None, tokens_used=None,
                    completed_at=None):
    """Insert a row into agent_events. Best-effort — never raises."""
    from datetime import datetime, timezone
    row = {"event_type": event_type, "status": status}
    if contact_id is not None:
        row["contact_id"] = contact_id
    if contact_name is not None:
        row["contact_name"] = contact_name
    if run_id is not None:
        row["run_id"] = run_id
    if error_message:
        row["error_message"] = error_message
    if metadata is not None:
        row["metadata"] = metadata
    if tokens_used is not None:
        row["tokens_used"] = tokens_used
    if completed_at is not None:
        row["completed_at"] = completed_at
    else:
        row["completed_at"] = datetime.now(timezone.utc).isoformat()
    try:
        _retry(lambda: get_client().table("agent_events").insert(row).execute())
    except Exception as exc:
        log.warning(f"[agent_events] insert failed: {exc}")


def get_agent_events(limit=100):
    """Fetch recent agent_events ordered by started_at desc."""
    result = _retry(lambda: (
        get_client()
        .table("agent_events")
        .select("*")
        .order("started_at", desc=True)
        .limit(limit)
        .execute()
    ))
    return result.data or []


def update_classifier_status(contact_id, classifier_status):
    """Update classifier_status for a single contact."""
    _retry(lambda: get_client().table("contacts").update({
        "classifier_status": classifier_status,
    }).eq("id", contact_id).execute())


# ── email_messages helpers ─────────────────────────────────────────────────────

def insert_email_message(contact_id, direction, sent_at, subject=None, body=None,
                         message_id=None, in_reply_to=None, stage_at_send=None,
                         raw_headers=None):
    """Insert an outgoing or incoming message. Skips silently if message_id already exists."""
    from datetime import datetime, timezone
    row = {
        "contact_id": contact_id,
        "direction": direction,
        "sent_at": sent_at if isinstance(sent_at, str) else sent_at.isoformat(),
    }
    if subject is not None:
        row["subject"] = subject
    if body is not None:
        row["body"] = body
    if message_id is not None:
        row["message_id"] = message_id
    if in_reply_to is not None:
        row["in_reply_to"] = in_reply_to
    if stage_at_send is not None:
        row["stage_at_send"] = stage_at_send
    if raw_headers is not None:
        row["raw_headers"] = raw_headers
    try:
        # ON CONFLICT DO NOTHING via upsert — unique index on message_id (non-null)
        if message_id is not None:
            _retry(lambda: (
                get_client()
                .table("email_messages")
                .upsert(row, on_conflict="message_id", ignore_duplicates=True)
                .execute()
            ))
        else:
            _retry(lambda: get_client().table("email_messages").insert(row).execute())
    except Exception as exc:
        log.warning(f"[email_messages] insert failed for contact {contact_id}: {exc}")


def get_email_messages(contact_id):
    """Fetch all messages for a contact ordered by sent_at asc."""
    result = _retry(lambda: (
        get_client()
        .table("email_messages")
        .select("*")
        .eq("contact_id", contact_id)
        .order("sent_at", desc=False)
        .execute()
    ))
    return result.data or []


# ── draft_history helpers ──────────────────────────────────────────────────────

def log_drafted_email(contact_id, stage, subject, body,
                      message_id=None, gmail_draft_id=None, decision_context=None):
    """Insert a row into draft_history when a Gmail draft is created. Best-effort."""
    from datetime import datetime, timezone
    row = {
        "contact_id": contact_id,
        "stage": stage,
        "drafted_at": datetime.now(timezone.utc).isoformat(),
    }
    if subject is not None:
        row["subject"] = subject
    if body is not None:
        row["body"] = body
    if message_id is not None:
        row["message_id"] = message_id
    if gmail_draft_id is not None:
        row["gmail_draft_id"] = gmail_draft_id
    if decision_context is not None:
        row["decision_context"] = decision_context
    try:
        _retry(lambda: get_client().table("draft_history").insert(row).execute())
    except Exception as exc:
        log.warning(f"[draft_history] insert failed for contact {contact_id}: {exc}")


def get_draft_history_by_stages(stages):
    """
    Fetch draft_history rows whose stage is in `stages`, newest first.
    Raises on failure -- an empty report and a failed read must not look alike.
    """
    # No .range() paging: the first-touch slice is in the low hundreds. If it
    # ever crosses PostgREST's ~1000-row cap, page it like
    # get_employer_h1b_stats_corpus().
    result = _retry(lambda: (
        get_client()
        .table("draft_history")
        .select("contact_id, stage, decision_context, drafted_at")
        .in_("stage", list(stages))
        .order("drafted_at", desc=True)
        .execute()
    ))
    return result.data or []


# ── research_cache helpers ─────────────────────────────────────────────────────

def get_research_cache(cache_key):
    """
    Selects from research_cache by cache_key (the
    'name_lower|company_lower' string built by the caller).
    Returns dict with brief_text, brief_json, cached_at on hit,
    None on miss.
    """
    result = _retry(lambda: (
        get_client()
        .table("research_cache")
        .select("brief_text, brief_json, cached_at")
        .eq("cache_key", cache_key)
        .execute()
    ))
    rows = result.data or []
    return rows[0] if rows else None


def set_research_cache(cache_key, contact_name, contact_company,
                       brief_text, brief_json,
                       queries_generated=None, brief_reliable=None):
    """
    Upserts into research_cache. Best-effort: on error log
    warning, return False. Returns True on success.
    """
    from datetime import datetime, timezone
    row = {
        "cache_key": cache_key,
        "contact_name": contact_name,
        "contact_company": contact_company,
        "brief_text": brief_text,
        "brief_json": brief_json,
        "cached_at": datetime.now(timezone.utc).isoformat(),
    }
    if queries_generated is not None:
        row["queries_generated"] = queries_generated
    if brief_reliable is not None:
        row["brief_reliable"] = brief_reliable
    try:
        _retry(lambda: (
            get_client()
            .table("research_cache")
            .upsert(row, on_conflict="cache_key")
            .execute()
        ))
        return True
    except Exception as exc:
        log.warning(
            f"[RESEARCH] | {contact_name} | {contact_company} | "
            f"cache write failed: {exc}"
        )
        return False


def get_research_reliability_map():
    """
    Return {cache_key: brief_reliable} for every research_cache row.
    Read once for reporting; get_research_cache() is per-key and does not
    select brief_reliable. Raises on failure.
    """
    result = _retry(lambda: (
        get_client()
        .table("research_cache")
        .select("cache_key, brief_reliable")
        .execute()
    ))
    return {r["cache_key"]: r.get("brief_reliable")
            for r in (result.data or []) if r.get("cache_key")}


# ── company_intel / employer_h1b_stats helpers ──────────────────────────────────

def get_employer_h1b_stats_corpus():
    """Fetch every cached employer's id, normalized_name, and denormalizable
    stats, for entity-resolution matching and company_intel row-building.
    Paginates via .range() -- PostgREST caps a single request's rows
    (commonly 1000), and this table can hold up to MAX_EMPLOYER_ROWS."""
    page_size = 1000
    rows = []
    offset = 0
    while True:
        result = _retry(lambda offset=offset: (
            get_client()
            .table("employer_h1b_stats")
            .select("id, normalized_name, lca_recent_2fy, latest_filing_fy, approval_rate")
            .range(offset, offset + page_size - 1)
            .execute()
        ))
        page = result.data or []
        rows.extend(page)
        if len(page) < page_size:
            break
        offset += page_size
    return rows


def upsert_employer_h1b_stats(rows):
    """Batch upsert employer_h1b_stats rows (keyed by normalized_name). Best-effort."""
    if not rows:
        return True
    try:
        _retry(lambda: (
            get_client()
            .table("employer_h1b_stats")
            .upsert(rows, on_conflict="normalized_name")
            .execute()
        ))
        return True
    except Exception as exc:
        log.warning(f"[employer_h1b_stats] upsert failed for {len(rows)} rows: {exc}")
        return False


def get_company_intel_by_normalized_names(normalized_names):
    """Fetch existing company_intel rows for a list of normalized names."""
    if not normalized_names:
        return []
    result = _retry(lambda: (
        get_client()
        .table("company_intel")
        .select("*")
        .in_("normalized_name", normalized_names)
        .execute()
    ))
    return result.data or []


def upsert_company_intel(rows):
    """Batch upsert company_intel rows (keyed by normalized_name). Best-effort."""
    if not rows:
        return True
    try:
        _retry(lambda: (
            get_client()
            .table("company_intel")
            .upsert(rows, on_conflict="normalized_name")
            .execute()
        ))
        return True
    except Exception as exc:
        log.warning(f"[company_intel] upsert failed for {len(rows)} rows: {exc}")
        return False


def upsert_company_funding(rows):
    """
    Batch upsert Form D funding fields (last_funding_date/amount/source/
    checked_at) onto company_intel, keyed by normalized_name. Best-effort,
    same as upsert_company_intel. Callers must pass only the funding columns
    plus normalized_name -- PostgREST's upsert only overwrites columns present
    in the payload, so this never touches sponsors_h1b/match_status.
    """
    if not rows:
        return True
    try:
        _retry(lambda: (
            get_client()
            .table("company_intel")
            .upsert(rows, on_conflict="normalized_name")
            .execute()
        ))
        return True
    except Exception as exc:
        log.warning(f"[company_intel] funding upsert failed for {len(rows)} rows: {exc}")
        return False


def update_contact_company_intel_id(contact_id, company_intel_id):
    """Link a contact to its resolved company_intel row. Best-effort."""
    try:
        _retry(lambda: get_client().table("contacts").update({
            "company_intel_id": company_intel_id,
        }).eq("id", contact_id).execute())
        return True
    except Exception as exc:
        log.warning(f"[company_intel] contact {contact_id} link failed: {exc}")
        return False


def get_all_company_intel_names():
    """Flatten raw_company_names across every company_intel row into one list."""
    result = _retry(lambda: get_client().table("company_intel").select("raw_company_names").execute())
    names = []
    for row in (result.data or []):
        names.extend(row.get("raw_company_names") or [])
    return names


# ── Job application tracking (Phase 1 of full-fledged buildout) ─────────────────

# Rows that mean "already have this role": anything at the same company with the same title
# identity within this window, whatever its stage (a skipped role is not offered again).
DUPLICATE_ROLE_DAYS = 45
# A role this candidate applied to is not applied to again within this window when the posting is
# the same text (a repost), or when there is no text to tell.
REPOST_DAYS = 180
_APPLIED_STAGES = {"applied", "phone_screen", "onsite", "offer", "rejected", "accepted"}
_SENT_STATUSES = {"approved", "submitting", "submitted", "needs_confirmation"}
_DEDUP_COLUMNS = "id,job_url,job_key,stage,automation_status,created_at,jd_fingerprint"


def _identity_fields(company, role, job_url, posting_snapshot):
    ident = job_identity.identify(job_url) if job_url else {}
    description = posting_snapshot.get("description") if isinstance(posting_snapshot, dict) else None
    return {
        "job_key": ident.get("job_key"),
        "platform": ident.get("platform"),
        "company_key": job_identity.company_key(company) or None,
        "title_key": job_identity.title_key(role) or None,
        "jd_fingerprint": job_identity.fingerprint(description) or None,
    }


def _dedup_rows(fields, job_url):
    def query():
        return get_client().table("job_applications").select(_DEDUP_COLUMNS)
    rows = []
    if fields["job_key"]:
        rows += _retry(lambda: query().eq("job_key", fields["job_key"]).limit(5).execute()).data or []
    if job_url:
        rows += _retry(lambda: query().eq("job_url", job_url).limit(5).execute()).data or []
    if fields["company_key"] and fields["title_key"]:
        rows += _retry(lambda: query().eq("company_key", fields["company_key"])
                       .eq("title_key", fields["title_key"])
                       .order("created_at", desc=True).limit(50).execute()).data or []
    return rows


def _created(row):
    try:
        value = datetime.fromisoformat(str(row.get("created_at")).replace("Z", "+00:00"))
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def duplicate_reason(fields, job_url, rows, now=None):
    """Why a new posting is already covered by an existing row ('same_job', 'same_role',
    'repost_of_applied'), or None. Pure: the caller passes the candidate rows."""
    now = now or datetime.now(timezone.utc)
    for row in rows:
        if (fields.get("job_key") and row.get("job_key") == fields["job_key"]) or (
                job_url and row.get("job_url") == job_url):
            return "same_job"
    for row in rows:
        created = _created(row)
        age_days = (now - created).days if created else 0
        if age_days <= DUPLICATE_ROLE_DAYS:
            return "same_role"
        applied = row.get("stage") in _APPLIED_STAGES or row.get("automation_status") in _SENT_STATUSES
        if applied and age_days <= REPOST_DAYS:
            mine, theirs = fields.get("jd_fingerprint"), row.get("jd_fingerprint")
            if not mine or not theirs or job_identity.similarity(mine, theirs) >= job_identity.REPOST_SIMILARITY:
                return "repost_of_applied"
    return None


def save_job_application(company, role, job_url=None, source=None, contact_id=None,
                         applied_date=None, notes=None, posting_snapshot=None, location=None,
                         posted_at=None):
    """Insert a posting at stage 'saved' unless it duplicates an existing row. Returns
    (row, None) on insert, or (None, reason) -- reason is a duplicate_reason value, 'conflict'
    when another writer inserted the same job first, or 'no_row' when the insert returned nothing."""
    fields = _identity_fields(company, role, job_url, posting_snapshot)
    if job_url or (fields["company_key"] and fields["title_key"]):
        reason = duplicate_reason(fields, job_url, _dedup_rows(fields, job_url))
        if reason:
            return None, reason
    payload = {
        "company": company,
        "role": role,
        "job_url": job_url,
        "source": source,
        "contact_id": contact_id,
        "applied_date": applied_date,
        "notes": notes,
        "posting_snapshot": posting_snapshot,
        "stage": "saved",
        "location": location,
        "posted_at": posted_at,
        **fields,
    }
    try:
        result = _retry(lambda: get_client().table("job_applications").insert(payload).execute(),
                        give_up=_is_unique_violation)
    except Exception as exc:
        if _is_unique_violation(exc):
            return None, "conflict"
        raise
    return (result.data[0], None) if result.data else (None, "no_row")


def create_job_application(company, role, job_url=None, source=None, contact_id=None,
                           applied_date=None, notes=None, posting_snapshot=None, location=None,
                           posted_at=None):
    """Create a new job application row at stage 'saved'. Returns None when the posting duplicates
    an existing row (same job under any URL spelling, the same role at the same company recently,
    or a repost of one already applied to), when another writer inserted it first, or when the
    insert returns no row. See save_job_application for the reason."""
    return save_job_application(company, role, job_url=job_url, source=source, contact_id=contact_id,
                                applied_date=applied_date, notes=notes, posting_snapshot=posting_snapshot,
                                location=location, posted_at=posted_at)[0]


def get_preview_candidates(limit, exclude_platforms=()):
    """Saved rows with both documents built that a preview may claim (idle or failed_retryable,
    under the attempt limit), best pick_score first. The filters run in the database: reading
    every saved row let the 1000-row response cap hide ready rows behind newer saved ones.
    Rows whose platform is not known yet (saved before job identity) are included."""
    def query():
        q = (get_client().table("job_applications").select("*")
             .eq("stage", "saved").not_.is_("resume_file_ref", "null").not_.is_("cover_letter_file_ref", "null")
             .in_("automation_status", list(config.APPLY_AGENT_PREVIEW_ELIGIBLE_STATUSES))
             .lt("prepare_attempts", config.APPLY_PREPARE_MAX_ATTEMPTS))
        if exclude_platforms:
            q = q.or_(f"platform.is.null,platform.not.in.({','.join(exclude_platforms)})")
        return q.order("pick_score", desc=True, nullsfirst=False).order("created_at", desc=False).limit(limit)
    return _retry(lambda: query().execute()).data or []


# ── Job sourcing: board registry and identity backfill (fifty-a-day §3.3) ──────

def get_sourcing_boards(limit):
    """Enabled, live boards, least recently scanned first (never-scanned boards lead)."""
    result = _retry(lambda: get_client().table("job_boards").select("*")
                    .eq("enabled", True).is_("dead_at", "null")
                    .order("last_scanned_at", desc=False, nullsfirst=True).limit(limit).execute())
    return result.data or []


def add_job_boards(boards):
    """Insert boards not seen before; existing (platform, board) pairs are left untouched."""
    rows = [{"platform": b["platform"], "board": b["board"], "company": b.get("company"),
             "source": b.get("source")} for b in boards]
    for start in range(0, len(rows), 200):
        chunk = rows[start:start + 200]
        _retry(lambda: get_client().table("job_boards")
               .upsert(chunk, on_conflict="platform,board", ignore_duplicates=True).execute())
    return len(rows)


def record_board_scan(board, status, job_count, dead_after):
    """Record one sweep of a board. Only a board that says it does not exist ('missing') counts
    toward being marked dead; a transient error just rotates it to the back of the queue."""
    failures = (board.get("consecutive_failures") or 0) + 1 if status == "missing" else 0
    update = {"last_scanned_at": datetime.utcnow().isoformat(), "consecutive_failures": failures}
    if status == "ok":
        update["last_job_count"] = job_count
    if failures >= dead_after:
        update["dead_at"] = datetime.utcnow().isoformat()
    _retry(lambda: get_client().table("job_boards").update(update).eq("id", board["id"]).execute())


def get_rows_missing_identity(limit):
    """Rows saved before job identity existed (job_key NULL) that have a URL to derive it from."""
    result = _retry(lambda: get_client().table("job_applications")
                    .select("id,company,role,job_url,stage,automation_status,posting_snapshot,created_at")
                    .is_("job_key", "null").not_.is_("job_url", "null")
                    .order("created_at", desc=False).limit(limit).execute())
    return result.data or []


def backfill_identity(row):
    """Write job_identity's fields onto a legacy row. When another row already owns the key, this
    row is a duplicate: an untouched one (saved, idle) is withdrawn with a pointer to the original;
    one already in flight keeps a NULL key and is reported. Returns 'set', 'withdrawn' or 'kept'."""
    fields = _identity_fields(row.get("company"), row.get("role"), row.get("job_url"), row.get("posting_snapshot"))
    owner = None
    if fields["job_key"]:
        found = _retry(lambda: get_client().table("job_applications").select("id")
                       .eq("job_key", fields["job_key"]).neq("id", row["id"]).limit(1).execute())
        owner = (found.data or [None])[0]
    if owner is None:
        try:
            _retry(lambda: get_client().table("job_applications").update(fields).eq("id", row["id"]).execute(),
                   give_up=_is_unique_violation)
            return "set"
        except Exception as exc:
            if not _is_unique_violation(exc):
                raise
    no_key = dict(fields, job_key=None)
    if row.get("stage") == "saved" and (row.get("automation_status") or "idle") == "idle":
        reason = f"Duplicate of application #{owner['id']}" if owner else "Duplicate of another application"
        _retry(lambda: get_client().table("job_applications")
               .update({**no_key, "stage": "withdrawn", "apply_blocked_reason": reason})
               .eq("id", row["id"]).execute())
        return "withdrawn"
    _retry(lambda: get_client().table("job_applications").update(no_key).eq("id", row["id"]).execute())
    return "kept"


def get_all_job_applications(columns="*", page_size=1000):
    """Every job_applications row (the given columns), read in pages: one request is capped at
    1000 rows by PostgREST, so a plain select silently undercounts past that."""
    rows, offset = [], 0
    while True:
        result = _retry(lambda offset=offset: get_client().table("job_applications").select(columns)
                        .order("id").range(offset, offset + page_size - 1).execute())
        page = result.data or []
        rows.extend(page)
        if len(page) < page_size:
            return rows
        offset += page_size


def get_job_applications(stage=None):
    """Fetch job applications, optionally filtered by stage, newest first."""
    query = get_client().table("job_applications").select("*")
    if stage is not None:
        query = query.eq("stage", stage)
    result = _retry(lambda: query.order("created_at", desc=True).execute())
    return result.data or []


def update_job_application_stage(application_id, stage):
    """Update a job application's stage."""
    result = _retry(lambda: get_client().table("job_applications")
                     .update({"stage": stage, "updated_at": datetime.utcnow().isoformat()})
                     .eq("id", application_id).execute())
    return result.data[0] if result.data else None


def get_job_application(application_id):
    """Fetch a single job application by id."""
    result = _retry(lambda: get_client().table("job_applications")
                     .select("*").eq("id", application_id).single().execute())
    return result.data


def get_unscored_saved_applications(limit=None):
    """Fetch job_applications rows at stage='saved' that job_pick.py hasn't scored yet, newest first."""
    def query():
        q = (get_client().table("job_applications").select("*").eq("stage", "saved")
             .is_("pick_verdict", "null").order("created_at", desc=True))
        return q.limit(limit) if limit else q
    result = _retry(lambda: query().execute())
    return result.data or []


def set_pick_attempts(application_id, attempts):
    """Record a failed fit judgment, so the row is retried a bounded number of times."""
    _retry(lambda: get_client().table("job_applications")
           .update({"pick_attempts": attempts, "updated_at": datetime.utcnow().isoformat()})
           .eq("id", application_id).execute())


def set_pick_verdict(application_id, verdict, score, reasoning):
    """Write job_pick.py's three-stage scoring output onto a row. Written once; never rescored."""
    result = _retry(lambda: get_client().table("job_applications")
                     .update({"pick_verdict": verdict, "pick_score": score, "pick_reasoning": reasoning,
                              "updated_at": datetime.utcnow().isoformat()})
                     .eq("id", application_id).execute())
    return result.data[0] if result.data else None


# The preview pass now writes apply_preview via release_application (under its lease); this
# unleased writer is kept for compatibility.
def set_apply_preview(application_id, preview):
    """Write apply_agent.py --preview's filled values and flip the row to ready_to_submit."""
    result = _retry(lambda: get_client().table("job_applications")
                     .update({"apply_preview": preview, "stage": "ready_to_submit",
                              "updated_at": datetime.utcnow().isoformat()})
                     .eq("id", application_id).execute())
    return result.data[0] if result.data else None


def set_apply_blocked(application_id, reason):
    """Flag a row as blocked (Workday, aggregator link, CAPTCHA, unrecognized field). Stage stays
    'saved' -- a blocked row is still eligible for the user to apply to by hand."""
    result = _retry(lambda: get_client().table("job_applications")
                     .update({"apply_blocked_reason": reason, "updated_at": datetime.utcnow().isoformat()})
                     .eq("id", application_id).execute())
    return result.data[0] if result.data else None


# ── Application worker leases ──────────────────────────────────────────────────

def _check_status(status):
    if status not in config.AUTOMATION_STATUSES:
        raise ValueError(f"unknown automation_status: {status}")


def _rpc(name, params):
    return get_client().rpc(name, params).execute().data


def claim_application(application_id, to_status):
    """Move a row into 'preparing' or 'submitting' under a fresh worker lease via the
    claim_application RPC (the server enforces which source states are legal). Returns the
    lease id, or None if the row isn't claimable. Deliberately NOT wrapped in _retry: a retry
    after a committed-but-unacknowledged claim would report a lost claim -- so on a False or
    failed response, re-read and compare lease ids."""
    _check_status(to_status)
    lease_id = str(uuid.uuid4())
    try:
        if _rpc("claim_application", {"p_id": application_id, "p_lease": lease_id, "p_to": to_status}):
            return lease_id
    except Exception as exc:
        log.warning(f"claim_application | {application_id} | claim call failed, re-reading: {exc}")
    row = _retry(lambda: get_client().table("job_applications").select("worker_lease_id")
                 .eq("id", application_id).execute())
    if row.data and row.data[0].get("worker_lease_id") == lease_id:
        return lease_id
    return None


def heartbeat_application(application_id, lease_id):
    """Refresh a held lease's heartbeat. Best-effort: never raises."""
    try:
        _rpc("heartbeat_application", {"p_id": application_id, "p_lease": lease_id})
    except Exception as exc:
        log.warning(f"heartbeat_application | {application_id} | {exc}")


def renew_submission_lease(application_id, lease_id, revision_hash):
    """Fail closed before Submit unless this worker still owns the approved revision.

    Unlike progress heartbeats, this conditional renewal must succeed. The RPC also records
    submit_attempted_at, so intent is durable before the external click. Network errors propagate.
    """
    if not revision_hash:
        return False
    return bool(_retry(lambda: _rpc("renew_submission_lease", {
        "p_id": application_id, "p_lease": lease_id, "p_revision_hash": revision_hash})))


def complete_preview(application_id, lease_id, preview, form_signature):
    """Finish a preview pass: store the preview and form signature, move to ready_for_review."""
    return bool(_retry(lambda: _rpc("complete_preview", {
        "p_id": application_id, "p_lease": lease_id, "p_preview": preview,
        "p_form_signature": form_signature})))


def release_application(application_id, lease_id, to_status, reason=None):
    """Drop the lease into to_status -- only if we still hold it. The server rejects illegal pairs."""
    _check_status(to_status)
    return bool(_retry(lambda: _rpc("release_application", {
        "p_id": application_id, "p_lease": lease_id, "p_to": to_status, "p_reason": reason})))


def record_submission(application_id, lease_id, source_channel, applied_date, evidence=None):
    """Atomically flip a row to 'applied'/'submitted' and record how/when it was actually filed,
    in one RPC -- a partial failure between two writes would leave a half-recorded row.
    `evidence` (confirmation URL, text, screenshot path) is merged under the server's own keys."""
    params = {"p_id": application_id, "p_lease": lease_id, "p_source_channel": source_channel,
              "p_applied_date": applied_date}
    # Omitted rather than sent as null, so the call also resolves before 20261008000000 is live.
    if evidence is not None:
        params["p_evidence"] = evidence
    return bool(_retry(lambda: _rpc("record_submission", params)))


# ── Run log and takeover (migration 20261008000000) ───────────────────────────

def _clip(text, limit):
    return text[:limit] if isinstance(text, str) else text


def log_application_run(application_id, kind, adapter, host, started_at, outcome, stop_reason=None,
                        fields_filled=None, fields_missing=None, takeovers=0, model_calls=0,
                        error_class=None, details=None, run_id=None):
    """Append one row to application_runs. Best-effort: returns the row id, or None on any
    failure -- a lost log row must never fail the run it describes."""
    if kind not in config.APPLICATION_RUN_KINDS or outcome not in config.APPLICATION_RUN_OUTCOMES:
        log.warning(f"log_application_run | {application_id} | unknown kind/outcome {kind}/{outcome}")
        return None
    params = {
        "p_run_id": run_id or str(uuid.uuid4()), "p_application_id": application_id,
        "p_kind": kind, "p_adapter": _clip(adapter, 64), "p_host": _clip(host, 255),
        "p_started_at": started_at.isoformat(), "p_outcome": outcome,
        "p_stop_reason": _clip(stop_reason, 500), "p_fields_filled": fields_filled,
        "p_fields_missing": fields_missing, "p_takeovers": takeovers, "p_model_calls": model_calls,
        "p_error_class": _clip(error_class, 128), "p_details": details,
    }
    try:
        return _retry(lambda: _rpc("log_application_run", params))
    except Exception as exc:
        log.warning(f"log_application_run | {application_id} | {exc}")
        return None


def get_recent_application_runs(limit=50):
    """The newest application_runs rows (read-only), for the apply_status report."""
    result = _retry(lambda: get_client().table("application_runs").select("*")
                    .order("ended_at", desc=True).limit(limit).execute())
    return result.data or []


def request_takeover(application_id, lease_id, kind, reason):
    """Ask a human to take over the browser. Only the live lease of a preparing/submitting row
    can ask; returns False otherwise."""
    if kind not in config.TAKEOVER_KINDS:
        raise ValueError(f"unknown takeover kind: {kind}")
    return bool(_retry(lambda: _rpc("request_takeover", {
        "p_id": application_id, "p_lease": lease_id, "p_kind": kind, "p_reason": _clip(reason, 500)})))


def clear_takeover(application_id, lease_id):
    """Close this lease's takeover request (resolved, or the worker gave up waiting)."""
    return bool(_retry(lambda: _rpc("clear_takeover", {"p_id": application_id, "p_lease": lease_id})))


def takeover_state(application_id, lease_id):
    """'continued' once the human pressed I'm done, 'waiting' while the request is open, 'none'
    with no request, 'lost' when this lease no longer owns the row."""
    result = _retry(lambda: get_client().table("job_applications").select("worker_lease_id,takeover")
                    .eq("id", application_id).execute())
    row = (result.data or [None])[0]
    if not row or row.get("worker_lease_id") != lease_id:
        return "lost"
    takeover = row.get("takeover")
    if not takeover or takeover.get("lease") != lease_id:
        return "none"
    return "continued" if takeover.get("continue_at") else "waiting"


def get_approved_application_ids():
    """Unleased rows the operator approved, oldest approval first: the Beelink submit queue."""
    result = _retry(lambda: get_client().table("job_applications").select("id")
                    .eq("automation_status", "approved").is_("worker_lease_id", "null")
                    .order("approved_at", desc=False).execute())
    return [row["id"] for row in result.data or []]


def count_submit_attempts_since(since):
    """Rows whose Submit was attempted at or after `since` (UTC): what the daily cap counts."""
    result = _retry(lambda: get_client().table("job_applications").select("id", count="exact")
                    .gte("submit_attempted_at", since.isoformat()).execute())
    return result.count or 0


def get_answer_bank_rows(limit=200):
    """Previews the operator approved, newest approval first: the source of the answer bank."""
    result = _retry(lambda: get_client().table("job_applications")
                    .select("company,automation_status,approved_at,apply_preview")
                    .not_.is_("approved_at", "null")
                    .order("approved_at", desc=True).limit(limit).execute())
    return result.data or []


def mark_unsupported(application_id, reason):
    """Unleased 'unsupported' write for rows that can never be applied to automatically."""
    return bool(_retry(lambda: _rpc("mark_application_unsupported", {
        "p_id": application_id, "p_reason": reason})))


def get_applications_needing_confirmation():
    """Unleased rows whose Submit click may have landed but was never confirmed on the page."""
    result = _retry(lambda: get_client().table("job_applications")
                    .select("id,company,role,apply_blocked_reason,submit_attempted_at,approved_at,updated_at,apply_preview")
                    .eq("automation_status", "needs_confirmation")
                    .is_("worker_lease_id", "null").execute())
    return result.data or []


def record_receipt_evidence(application_id, evidence):
    """needs_confirmation -> submitted on Gmail receipt evidence. The RPC refuses any other source state."""
    return bool(_retry(lambda: _rpc("record_receipt_evidence", {
        "p_id": application_id, "p_evidence": evidence})))


def recover_stale_leases(stale_after_seconds):
    """Release leases whose heartbeat is stale. The RPC decides the target: a stale 'submitting'
    lease past the click boundary becomes needs_confirmation, never retryable."""
    return int(_retry(lambda: _rpc("recover_stale_leases", {"p_stale_seconds": stale_after_seconds})) or 0)


def set_resume_strategy(application_id, strategy):
    """Write stage-4 strategy output onto a job_applications row. Builds nothing."""
    result = _retry(lambda: get_client().table("job_applications")
                     .update({"resume_strategy": strategy, "updated_at": datetime.utcnow().isoformat()})
                     .eq("id", application_id).execute())
    return result.data[0] if result.data else None


def set_resume_files(application_id, resume_file_ref=None, cover_letter_file_ref=None, resume_variant=None):
    """Write built-file references onto a job_applications row after a successful build."""
    # Fixed storage paths mean refs alone don't change on a rebuild; a fresh version changes the
    # trigger-owned preview hash, so a rebuild after approval invalidates it.
    payload = {"updated_at": datetime.utcnow().isoformat(), "documents_version": str(uuid.uuid4())}
    if resume_file_ref is not None:
        payload["resume_file_ref"] = resume_file_ref
    if cover_letter_file_ref is not None:
        payload["cover_letter_file_ref"] = cover_letter_file_ref
    if resume_variant is not None:
        payload["resume_variant"] = resume_variant
    result = _retry(lambda: get_client().table("job_applications")
                     .update(payload).eq("id", application_id).execute())
    return result.data[0] if result.data else None


def _strong_queue(query, exclude_platforms):
    query = (query.eq("pick_verdict", "strong").eq("stage", "saved")
             .is_("resume_file_ref", "null").is_("resume_error", "null"))
    if exclude_platforms:
        query = query.or_(f"platform.is.null,platform.not.in.({','.join(exclude_platforms)})")
    return query


def get_strong_applications_without_resume(limit, exclude_platforms=()):
    """Rows at stage='saved' that job_pick.py scored 'strong', with no built resume and no
    recorded resume_error -- the Beelink resume worker's queue (resume_agent.py --drain). Best
    pick_score first, then oldest; rows on platforms this host cannot submit are left out.
    Applied/rejected/withdrawn rows are never rebuilt."""
    result = _retry(lambda: _strong_queue(get_client().table("job_applications").select("*"), exclude_platforms)
                    .order("pick_score", desc=True, nullsfirst=False)
                    .order("created_at", desc=False).limit(limit).execute())
    return result.data or []


def count_stale_strong_without_resume(hours, exclude_platforms=()):
    """Count queued strong rows (same filters as get_strong_applications_without_resume) not
    touched for over `hours` -- the alarm that the Beelink resume worker is not consuming."""
    cutoff = (datetime.utcnow() - timedelta(hours=hours)).isoformat()
    result = _retry(lambda: _strong_queue(get_client().table("job_applications").select("id", count="exact"),
                                          exclude_platforms)
                    .lt("updated_at", cutoff).execute())
    return result.count or 0


def count_company_applications(company_key, days):
    """Rows at this company in the last `days` that got documents built or went toward a
    submission: what the per-company cap counts."""
    cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
    result = _retry(lambda: get_client().table("job_applications").select("id", count="exact")
                    .eq("company_key", company_key).gte("created_at", cutoff)
                    .or_("resume_file_ref.not.is.null,automation_status.in.(approved,submitting,submitted,needs_confirmation)")
                    .execute())
    return result.count or 0


def set_resume_error(application_id, message):
    """Record why the resume worker gave up on a row, so it isn't retried every run. Clearing
    the column re-queues the row."""
    result = _retry(lambda: get_client().table("job_applications")
                     .update({"resume_error": str(message)[:1000],
                              "updated_at": datetime.utcnow().isoformat()})
                     .eq("id", application_id).execute())
    return result.data[0] if result.data else None


def upload_resume_file(storage_path, file_bytes, content_type):
    """Upload a built file to the resumes Storage bucket. Returns storage_path. Raises on failure --
    unlike the rest of this module's best-effort accessors, a failed upload must not look like success."""
    get_client().storage.from_(config.RESUME_STORAGE_BUCKET).upload(
        storage_path, file_bytes, {"content-type": content_type, "upsert": "true"},
    )
    return storage_path


def upload_evidence(storage_path, file_bytes, content_type):
    """Upload submission proof to the private application-evidence bucket (anon may only insert,
    so never upsert: each run writes under its own path). Raises on failure."""
    get_client().storage.from_(config.APPLY_EVIDENCE_BUCKET).upload(
        storage_path, file_bytes, {"content-type": content_type, "upsert": "false"},
    )
    return storage_path


def record_resume_usage(application_id, tokens_input, tokens_output, cost_usd):
    """Accumulate Claude token/cost usage onto a job_applications row's running totals across
    every resume_agent.py call for that row (propose's strategy call, build's cover-letter call
    and any retry). Read-then-write, not atomic -- acceptable for this manual, single-user CLI."""
    current = _retry(lambda: get_client().table("job_applications")
                      .select("resume_tokens_input,resume_tokens_output,resume_cost_usd")
                      .eq("id", application_id).single().execute())
    row = current.data or {}
    new_tokens_input = (row.get("resume_tokens_input") or 0) + tokens_input
    new_tokens_output = (row.get("resume_tokens_output") or 0) + tokens_output
    new_cost = round((row.get("resume_cost_usd") or 0) + cost_usd, 6)
    result = _retry(lambda: get_client().table("job_applications")
                     .update({"resume_tokens_input": new_tokens_input,
                              "resume_tokens_output": new_tokens_output,
                              "resume_cost_usd": new_cost,
                              "updated_at": datetime.utcnow().isoformat()})
                     .eq("id", application_id).execute())
    return result.data[0] if result.data else None


def log_api_usage(module, action, model, input_tokens, output_tokens, cost_usd,
                   contact_id=None, job_application_id=None, billing="api"):
    """Insert one row into the system-wide api_usage_log ledger. Raises on failure -- callers
    (usage_tracking.log_usage) are responsible for the best-effort wrapping, since this accessor
    follows the rest of db.py's pattern of surfacing real failures rather than swallowing them."""
    payload = {
        "module": module, "action": action, "model": model,
        "input_tokens": input_tokens, "output_tokens": output_tokens, "cost_usd": cost_usd,
        "contact_id": contact_id, "job_application_id": job_application_id,
    }
    # 'api' is the column default: leaving it out keeps every existing writer working even before
    # migration 20261005000000 lands.
    if billing != "api":
        payload["billing"] = billing
    result = _retry(lambda: get_client().table("api_usage_log").insert(payload).execute())
    return result.data[0] if result.data else None
