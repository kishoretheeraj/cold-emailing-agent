"""
The universal filler: fills an application form on any site from the form inventory, with no model
in the loop for navigation (spec docs/superpowers/specs/2026-10-09-every-site-design.md §3.1).

  contact_key   which candidate contact detail a field asks for, or None. Anything about another
                person (a referrer, an emergency contact, a manager) is None: a wrong key would type
                the operator's details into someone else's field.

  button_role   what pressing a visible control would do: entry, next, submit, third_party, None.
  page_state    auth / email_code / posting / form / unknown.
  allowed_host  the form may only be filled on the posting's own site or a known ATS host.
  SUBMIT_GUARD  an init script that stops every native form submission and counts it.
  target        the page (or the one iframe) holding the application, scoped to its <form>.
  final_control the single strict submit control; anything ambiguous raises Stop.

Phase 1 handles one-page forms: nothing here presses Next or Submit. The entry control is pressed
once by apply_agent on the landing page; the final click is apply_agent.submit()'s, armed, after
the signed approval (spec §7).
"""

import re
from urllib.parse import urlsplit

import job_identity

# ── Contact fields ─────────────────────────────────────────────────────────────

_AUTOCOMPLETE = {
    "given-name": "first_name", "family-name": "last_name", "name": "name", "email": "email",
    "tel": "phone", "tel-national": "phone", "address-level2": "city",
}

# Fields about another person, or about something that is not the candidate's contact detail.
_NOT_THE_CANDIDATE = re.compile(
    r"\b(refer(r?er|red|ral|ence)s?|emergency|manager|recruiter|supervisor|spouse|parent|guardian"
    r"|company|employer|school|university|college|institution|previous|former|employee"
    r"|job title|pronouns?|search|cover letter|country code|extension|ext|phone type|device type"
    r"|how did you hear)\b",
    re.IGNORECASE,
)

_RULES = (
    ("linkedin", re.compile(r"\blinked ?in\b", re.I)),
    ("github", re.compile(r"\bgit ?hub\b", re.I)),
    ("website", re.compile(r"\b(website|portfolio|personal (site|url|page))\b", re.I)),
    ("email", re.compile(r"\be ?mail\b", re.I)),
    ("phone", re.compile(r"\b(phone|mobile|telephone|cell)\b", re.I)),
    ("first_name", re.compile(r"\b(first|given|fore) ?names?\b|\bfname\b", re.I)),
    ("last_name", re.compile(r"\b(last|family|sur) ?names?\b|\blname\b", re.I)),
    ("name", re.compile(r"^(full |your |legal )?name$|\byour name\b|\bfull name\b", re.I)),
    ("city", re.compile(r"^city$|\bcity of residence\b|\bcurrent city\b", re.I)),
    ("location", re.compile(r"\blocation\b|\bwhere are you (based|located)\b", re.I)),
)


# ── Buttons ────────────────────────────────────────────────────────────────────

_THIRD_PARTY = re.compile(
    r"\b(with|using|via) (linked ?in|indeed|google|seek|facebook|apple|microsoft|github)\b"
    r"|\beasy apply\b|\bautofill\b",
    re.IGNORECASE,
)
# Anything that could send the application. Checked before Next, so "Submit & Continue" is submit.
_SUBMIT = re.compile(r"\b(submit|send|apply|finish|complete|done|confirm)\b", re.IGNORECASE)
_NEXT = re.compile(r"^(next( step)?|continue|save (and|&) continue|proceed)\W*$", re.IGNORECASE)
_ENTRY = re.compile(
    r"^(apply( now)?|apply for this (job|position|role)|i'?m interested|start (your )?application)\W*$",
    re.IGNORECASE,
)


def button_role(label, on_form=True):
    """'third_party', 'entry' (on a posting page), 'submit', 'next', or None for anything else."""
    text = re.sub(r"\s+", " ", str(label or "")).strip()
    if not text:
        return None
    if _THIRD_PARTY.search(text):
        return "third_party"
    if not on_form and _ENTRY.match(text):
        return "entry"
    if _SUBMIT.search(text):
        return "submit"
    if _NEXT.match(text):
        return "next"
    return None


# ── Page reading ───────────────────────────────────────────────────────────────

# Tags every visible, enabled control with data-uf-ctl=<n> (fresh on each scan) and returns its
# label, so a chosen control is located by that tag and nothing else.
_CONTROLS_JS = r"""() => {
  for (const el of document.querySelectorAll('[data-uf-ctl]')) el.removeAttribute('data-uf-ctl');
  const visible = (el) => !!(el.offsetParent || el.getClientRects().length);
  const out = [];
  const els = document.querySelectorAll(
    'button, a[href], input[type=submit], input[type=button], [role=button]');
  for (const el of els) {
    if (!visible(el) || el.disabled || el.getAttribute('aria-disabled') === 'true') continue;
    if (el.closest('[role=search], form[role=search]')) continue;
    const label = (el.innerText || el.value || el.getAttribute('aria-label') || '').replace(/\s+/g, ' ').trim();
    if (!label || label.length > 60) continue;
    el.setAttribute('data-uf-ctl', String(out.length));
    out.push({n: out.length, label, tag: el.tagName.toLowerCase(), type: (el.type || '').toLowerCase()});
  }
  return out;
}"""

# Fields that belong to an application: visible (or file) inputs outside site search boxes.
_STATE_JS = r"""() => {
  const visible = (el) => !!(el.offsetParent || el.getClientRects().length);
  const skip = ['hidden', 'submit', 'button', 'reset', 'image', 'search'];
  let fields = 0;
  for (const el of document.querySelectorAll('input, select, textarea')) {
    const type = (el.type || '').toLowerCase();
    if (skip.includes(type) || el.disabled) continue;
    if (el.closest('[role=search], form[role=search]')) continue;
    if (/captcha/i.test((el.name || '') + ' ' + (el.id || ''))) continue;
    if (type !== 'file' && !visible(el)) continue;
    fields += 1;
  }
  const shown = (sel) => Array.from(document.querySelectorAll(sel)).some(visible);
  const codeLabel = Array.from(document.querySelectorAll('label')).some((l) => visible(l)
      && /verification code|one.?time (pass)?code|security code|\bpin\b|enter the code/i.test(l.innerText));
  return {fields, password: shown('input[type=password]'),
          code: shown('input[autocomplete=one-time-code]') || codeLabel};
}"""


def controls(page):
    """[(n, label)] for every visible, enabled control, each tagged data-uf-ctl=n on the page."""
    try:
        found = page.evaluate(_CONTROLS_JS) or []
    except Exception:
        return []
    return [(c["n"], c["label"]) for c in found if isinstance(c, dict)]


def control(page, n):
    return page.locator(f"[data-uf-ctl='{int(n)}']").first


def _single(page, role, on_form):
    matches = [n for n, label in controls(page) if button_role(label, on_form=on_form) == role]
    return control(page, matches[0]) if len(matches) == 1 else None


def entry_control(page):
    """The one entry control on a posting page ("Apply now"), or None (none, or ambiguous)."""
    return _single(page, "entry", on_form=False)


def page_state(page):
    """'auth' (a password wall), 'email_code' (a one-time code field), 'posting' (an entry control
    and no application form), 'form', or 'unknown'."""
    try:
        seen = page.evaluate(_STATE_JS) or {}
    except Exception:
        return "unknown"
    if seen.get("password"):
        return "auth"
    if seen.get("code"):
        return "email_code"
    fields = seen.get("fields", 0)
    labels = [label for _, label in controls(page)]
    has_entry = any(button_role(label, on_form=False) == "entry" for label in labels)
    if has_entry and fields < 2:
        return "posting"
    if fields:
        return "form"
    return "posting" if has_entry else "unknown"


def _words(*parts):
    text = " ".join(p for p in parts if p)
    text = re.sub(r"([a-z])([A-Z])", r"\1 \2", text)
    text = re.sub(r"[\[\]_\-.:/()?*]+", " ", text)
    return re.sub(r"\s+", " ", text).strip().lower()


def contact_key(field):
    """The candidate contact key a text field asks for ('first_name', 'last_name', 'name', 'email',
    'phone', 'linkedin', 'github', 'website', 'city', 'location'), or None."""
    if field.get("kind") != "input":
        return None
    label = _words(field.get("label"))
    name = _words(field.get("name"))
    if _NOT_THE_CANDIDATE.search(label) or _NOT_THE_CANDIDATE.search(name):
        return None
    auto = (field.get("autocomplete") or "").strip().lower()
    if auto in _AUTOCOMPLETE:
        return _AUTOCOMPLETE[auto]
    for text in (label, name):
        if not text:
            continue
        for key, rule in _RULES:
            if rule.search(text):
                return key
    input_type = (field.get("input_type") or "").lower()
    return {"email": "email", "tel": "phone"}.get(input_type)


# ── Where the operator's details may go ────────────────────────────────────────

# ATS vendors that host many employers' forms but that job_identity files under 'generic'.
ATS_HOST_SUFFIXES = (
    "greenhouse.io", "lever.co", "ashbyhq.com", "myworkdayjobs.com", "myworkdaysite.com",
    "jobvite.com", "applytojob.com", "bamboohr.com", "recruitee.com", "rippling-ats.com", "ats.rippling.com",
    "eightfold.ai", "taleo.net", "successfactors.com", "successfactors.eu", "breezy.hr", "teamtailor.com",
    "pinpointhq.com", "jazzhr.com", "paylocity.com", "ultipro.com", "dayforcehcm.com", "avature.net",
)
_KNOWN_ATS_PLATFORMS = ("greenhouse", "lever", "ashby", "workday", "smartrecruiters", "workable", "oracle", "icims")
_TWO_LEVEL_SUFFIXES = ("co", "com", "org", "net", "ac", "gov", "edu")


def _host(url):
    try:
        return (urlsplit(str(url or "")).hostname or "").lower().rstrip(".")
    except ValueError:
        return ""


def site_of(host):
    """The registrable part of a host: careers.tesla.com -> tesla.com, jobs.foo.co.uk -> foo.co.uk."""
    labels = [p for p in (host or "").split(".") if p]
    if len(labels) >= 3 and len(labels[-1]) == 2 and labels[-2] in _TWO_LEVEL_SUFFIXES:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def _on(host, suffix):
    return host == suffix or host.endswith("." + suffix)


def allowed_host(job_url, url):
    """True when the operator's details may be typed on `url`: the posting's own site, or a host
    known to be an applicant-tracking system. Anything else (a redirect, a tab opened elsewhere)
    is refused before a field is touched."""
    host = _host(url)
    if not host or urlsplit(str(url)).scheme not in ("https", "http"):
        return False
    if host == "localhost" or re.fullmatch(r"[\d.]+|\[?[0-9a-f:]+\]?", host):
        return host == _host(job_url)
    if job_identity.identify(url)["platform"] in _KNOWN_ATS_PLATFORMS:
        return True
    if any(_on(host, suffix) for suffix in ATS_HOST_SUFFIXES):
        return True
    own = site_of(_host(job_url))
    return bool(own) and _on(host, own)


# ── Submit guard ───────────────────────────────────────────────────────────────

# Installed before any page script runs. Every native submission (a submit button, Enter in a
# field, form.submit(), form.requestSubmit()) and every network submission (fetch, XMLHttpRequest
# and navigator.sendBeacon with any method but GET/HEAD) is stopped and counted until
# window.__ufGuardOff is set, which only apply_agent.submit() does, right before the one approved
# click. The network wrappers cover a button whose handler posts directly with no <form> involved.
SUBMIT_GUARD = r"""(() => {
  window.__ufBlocked = 0;
  const blocked = () => !window.__ufGuardOff;
  const readOnly = (m) => { m = String(m || 'GET').toUpperCase(); return m === 'GET' || m === 'HEAD'; };
  window.addEventListener('submit', (e) => {
    if (blocked()) { e.preventDefault(); e.stopImmediatePropagation(); window.__ufBlocked += 1; }
  }, true);
  const proto = HTMLFormElement.prototype;
  const submit = proto.submit, requestSubmit = proto.requestSubmit;
  proto.submit = function () { if (blocked()) { window.__ufBlocked += 1; return; } return submit.call(this); };
  proto.requestSubmit = function (b) {
    if (blocked()) { window.__ufBlocked += 1; return; } return requestSubmit.call(this, b);
  };
  const realFetch = window.fetch;
  if (realFetch) {
    window.fetch = function (input, init) {
      const method = (init && init.method) || (typeof Request !== 'undefined' && input instanceof Request ? input.method : 'GET');
      if (blocked() && !readOnly(method)) {
        window.__ufBlocked += 1;
        return Promise.reject(new TypeError('blocked by submit guard'));
      }
      return realFetch.apply(this, arguments);
    };
  }
  const xhr = window.XMLHttpRequest && window.XMLHttpRequest.prototype;
  if (xhr) {
    const realOpen = xhr.open, realSend = xhr.send;
    xhr.open = function (method) { this.__ufMethod = method; return realOpen.apply(this, arguments); };
    xhr.send = function () {
      if (blocked() && !readOnly(this.__ufMethod)) { window.__ufBlocked += 1; return; }
      return realSend.apply(this, arguments);
    };
  }
  if (navigator.sendBeacon) {
    const realBeacon = navigator.sendBeacon;
    navigator.sendBeacon = function () {
      if (blocked()) { window.__ufBlocked += 1; return false; }
      return realBeacon.apply(this, arguments);
    };
  }
})();"""


def guard_count(page):
    """Submissions the guard stopped on this page (native form submits plus non-GET/HEAD
    fetch, XMLHttpRequest and sendBeacon calls), or None when the guard is not installed."""
    try:
        value = page.evaluate("() => (typeof window.__ufBlocked === 'number' ? window.__ufBlocked : null)")
    except Exception:
        return None
    return value if isinstance(value, int) else None


def lift_guard(page):
    page.evaluate("() => { window.__ufGuardOff = true; }")


# ── The application's own page, frame and form ─────────────────────────────────

class Stop(Exception):
    """The universal filler cannot go on; `kind` names why (multi_step, no_submit, host, ...)."""

    def __init__(self, kind, message):
        super().__init__(message)
        self.kind = kind


class View:
    """A page or frame as the apply helpers see it: locators and evaluate from the target, the
    keyboard and mouse from its page, Enter never pressed (no_enter), inventory scoped to the
    application's <form> (form_scope)."""

    no_enter = True

    def __init__(self, target, form_scope=None):
        self._target = target
        self.form_scope = form_scope

    @property
    def target(self):
        return self._target

    def __getattr__(self, name):
        if name in ("keyboard", "mouse", "context"):
            page = getattr(self._target, "page", None)
            owner = page if page is not None and not callable(page) else self._target
            return getattr(owner, name)
        return getattr(self._target, name)


def _app_fields(target):
    try:
        return int((target.evaluate(_STATE_JS) or {}).get("fields", 0))
    except Exception:
        return 0


def application_target(page, job_url):
    """The page, or the one child frame on an allowed host, that holds the application fields.
    Raises Stop when the fields are nowhere, or in more than one frame."""
    if _app_fields(page):
        return page
    framed = [f for f in page.frames[1:] if allowed_host(job_url, f.url) and _app_fields(f)]
    if len(framed) == 1:
        return framed[0]
    if framed:
        raise Stop("ambiguous", "The application fields are split across several embedded frames")
    raise Stop("no_form", "No application form on this page")


_STRICT_SUBMIT = re.compile(r"^(submit|submit (my |your )?application|send (my )?application|apply|apply now)\W*$",
                            re.IGNORECASE)

_FORM_OF_CONTROLS_JS = r"""() => Array.from(document.querySelectorAll('[data-uf-ctl]')).map((el) => {
  const f = el.form || el.closest('form');
  return [Number(el.getAttribute('data-uf-ctl')), f ? Array.from(document.forms).indexOf(f) : -1];
})"""


def final_control(target):
    """(n, label, form_index) of the one strict submit control. Raises Stop when the page offers a
    Next-class control (a multi-step form), or no single strict submit control."""
    found = controls(target)
    roles = [(n, label, button_role(label, on_form=True)) for n, label in found]
    if any(role == "next" for _, _, role in roles):
        raise Stop("multi_step", "This is a multi-step form; the universal filler handles one-page forms for now")
    strict = [(n, label) for n, label, role in roles if role == "submit" and _STRICT_SUBMIT.match(label)]
    if len(strict) != 1:
        raise Stop("no_submit", f"Expected one Submit button on the form, found {len(strict)}")
    try:
        forms = dict((int(a), int(b)) for a, b in (target.evaluate(_FORM_OF_CONTROLS_JS) or []))
    except Exception:
        forms = {}
    n, label = strict[0]
    return n, label, forms.get(n, -1)


# ── Contact fields and files ───────────────────────────────────────────────────

_CONFIRM = re.compile(r"\b(confirm|re[- ]?enter|re[- ]?type|verify|repeat)\b", re.IGNORECASE)


def contact_plan(inventory):
    """{selector: key} for the candidate's contact fields. A key that two fields claim fills
    neither (an email and a 'confirm email' field are the one allowed pair)."""
    claims = {}
    for field in inventory or []:
        key = contact_key(field)
        if key and field.get("selector"):
            claims.setdefault(key, []).append(field)
    plan = {}
    for key, fields in claims.items():
        if len(fields) == 1:
            plan[fields[0]["selector"]] = key
        elif key == "email" and len(fields) == 2 and sum(bool(_CONFIRM.search(f.get("label") or "")) for f in fields) == 1:
            for f in fields:
                plan[f["selector"]] = key
    return plan


_RESUME_FILE = re.compile(r"\b(resume|résumé|cv|curriculum vitae)\b", re.IGNORECASE)
_COVER_FILE = re.compile(r"\bcover( letter)?\b", re.IGNORECASE)
_OTHER_FILE = re.compile(r"\b(transcript|photo|picture|headshot|writing sample|portfolio|certificat|id card|"
                         r"passport|license|other|additional)\b", re.IGNORECASE)


def file_targets(inventory):
    """{'resume': selector, 'cover_letter': selector} chosen by each file input's label. A single
    file input with a neutral label ('Attach', 'Upload file') counts as the resume."""
    files = [f for f in inventory or [] if f.get("kind") == "file" and f.get("selector")]
    out = {}
    for key, rule in (("resume", _RESUME_FILE), ("cover_letter", _COVER_FILE)):
        matches = [f for f in files if rule.search(f.get("label") or "") and not
                   (key == "resume" and _COVER_FILE.search(f.get("label") or ""))]
        if len(matches) == 1:
            out[key] = matches[0]["selector"]
    if "resume" not in out and len(files) == 1:
        label = files[0].get("label") or ""
        if not (_COVER_FILE.search(label) or _OTHER_FILE.search(label)):
            out["resume"] = files[0]["selector"]
    return out
