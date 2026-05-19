from datetime import date, timedelta
import logging
import re
import time
import supabase._sync.client as _sc

log = logging.getLogger(__name__)

# Patch: Supabase updated their key format (sb_publishable_*) but the Python
# client still validates against JWT regex. Widen the check.
_orig_init = _sc.SyncClient.__init__

def _patched_init(self, supabase_url, supabase_key, options=None):
    if not re.match(r"^(https?)://.+", supabase_url):
        raise _sc.SupabaseException("Invalid URL")
    if not (re.match(r"^[A-Za-z0-9-_=]+\.[A-Za-z0-9-_=]+\.?[A-Za-z0-9-_.+/=]*$", supabase_key)
            or supabase_key.startswith("sb_")):
        raise _sc.SupabaseException("Invalid API key")
    # Skip the original __init__ validation by calling the rest directly
    _orig_init.__wrapped__(self, supabase_url, supabase_key, options)

# Simpler approach: just monkeypatch the regex check
import supabase._sync.client
_original_create = supabase._sync.client.create_client

def _create_patched(supabase_url, supabase_key, options=None):
    import supabase._sync.client as mod
    orig_re_match = re.match
    def lenient_match(pattern, string, *a, **kw):
        if "A-Za-z0-9-_=" in str(pattern) and string.startswith("sb_"):
            return True
        return orig_re_match(pattern, string, *a, **kw)
    old = re.match
    re.match = lenient_match
    try:
        return _original_create(supabase_url, supabase_key, options)
    finally:
        re.match = old

from supabase import create_client as _orig_create_client
from config import SUPABASE_URL, SUPABASE_ANON_KEY

_client = None

# Retry wrapper for Supabase calls — same shape as emailer._call_claude.
# Network blips and 5xx are rare but kill the whole run when get_all_contacts
# is the first call out of the gate.
def _retry(fn):
    for attempt in range(3):
        try:
            return fn()
        except Exception:
            if attempt < 2:
                time.sleep(2 ** (attempt + 1))
                continue
            raise

def get_client():
    global _client
    if _client is None:
        _client = _create_patched(SUPABASE_URL, SUPABASE_ANON_KEY)
    return _client

def get_all_contacts():
    """Fetch all contacts from Supabase."""
    result = _retry(lambda: get_client().table("contacts").select("*").is_("deleted_at", "null").execute())
    return result.data or []

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
    row = {"message_id": message_id, "original_subject": subject}
    if gmail_thread_id is not None:
        row["gmail_thread_id"] = gmail_thread_id
    _retry(lambda: get_client().table("contacts").update(row).eq("id", contact_id).execute())

def update_message_id(contact_id, message_id):
    """Update message_id when the sent email's actual ID differs from the draft's."""
    _retry(lambda: get_client().table("contacts").update({
        "message_id": message_id,
    }).eq("id", contact_id).execute())

def update_gmail_thread_id(contact_id, gmail_thread_id):
    """Store the X-GM-THRID captured at draft creation for reliable sent detection."""
    _retry(lambda: get_client().table("contacts").update({
        "gmail_thread_id": gmail_thread_id,
    }).eq("id", contact_id).execute())

def get_thread_info(contact_id):
    """Return message_id and original_subject for a contact."""
    result = _retry(lambda: get_client().table("contacts").select(
        "message_id, original_subject"
    ).eq("id", contact_id).execute())
    rows = result.data or []
    return rows[0] if rows else {}

def load_prompts():
    """Load all rows from the prompts table at agent startup."""
    result = _retry(lambda: get_client().table("prompts").select("key, value").execute())
    return {r["key"]: r["value"] for r in (result.data or [])}

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
