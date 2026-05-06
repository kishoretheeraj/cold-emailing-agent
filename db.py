from datetime import date, timedelta
import re
import supabase._sync.client as _sc

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

def get_client():
    global _client
    if _client is None:
        _client = _create_patched(SUPABASE_URL, SUPABASE_ANON_KEY)
    return _client

def get_all_contacts():
    """Fetch all contacts from Supabase."""
    result = get_client().table("contacts").select("*").execute()
    return result.data or []

def update_contact(contact_id, stage, followup_days=None, template=None):
    """Update contact stage, followup_date, and last_emailed after a draft is created."""
    updates = {
        "stage": stage,
        "last_emailed": str(date.today()),
    }
    if followup_days is not None:
        updates["followup_date"] = str(date.today() + timedelta(days=followup_days))
    if template:
        updates["template_current"] = template
    get_client().table("contacts").update(updates).eq("id", contact_id).execute()

def close_contact(contact_id):
    """Mark a contact as closed — no more emails."""
    get_client().table("contacts").update({
        "stage": "closed",
        "last_emailed": str(date.today()),
    }).eq("id", contact_id).execute()

def get_sent_contacts():
    """Fetch contacts where an email was sent but no reply recorded yet."""
    result = (
        get_client()
        .table("contacts")
        .select("*")
        .eq("reply_status", "no_reply")
        .like("stage", "%_sent%")
        .execute()
    )
    return result.data or []

def update_reply_status(contact_id, status):
    """Update reply_status for a single contact."""
    get_client().table("contacts").update({
        "reply_status": status,
    }).eq("id", contact_id).execute()
