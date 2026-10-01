# Email Verification Pre-flight Implementation Plan

**Goal:** Add a bounce-risk gate that runs before a first-touch draft is generated — syntax + MX/A DNS check on `contact["email"]` — so the agent never spends a Claude call drafting an email to an address that cannot possibly receive mail.

**Architecture:** A new self-contained `email_verify.py` (only outside dependency: `dnspython`) exposes `verify(email) -> EmailVerifyResult(status, reason)` with `status` in `{"valid", "invalid", "unknown"}` and never raises. `agent.run()`'s Phase 1 loop calls it for `_FIRST_TOUCH_ACTIONS` contacts only, before the batch request is built; `"invalid"` skips the contact (same as an existing `decide_action == "skip"` outcome), `"unknown"` and `"valid"` both proceed unchanged.

**Tech Stack:** Python 3.11 (plain, no type annotations), `dnspython` (new dependency), Supabase (`agent_events` only — no new table/column), pytest + pytest-mock. No migration, no new secret, no TypeScript change.

**Spec:** `docs/superpowers/specs/2026-09-05-email-verification-preflight-design.md`

## Global Constraints

- **No type annotations.** Plain Python. No `typing` imports.
- **No docstrings on `_`-prefixed helpers.** Public functions (`verify`) get one short docstring.
- **Section banners:** `# ── Section name ─────...` (16+ box-drawing chars).
- **Log format:** `f"{marker} | {name} | {company} | event | extra"`, pipe-separated. New marker: `[EMAIL-VERIFY]`.
- **All outbound calls mocked in tests.** Tests never travel. Every DNS lookup in this plan is mocked at `dns.resolver.resolve`.
- **Never-block-on-uncertainty rule:** `email_verify.verify` must never raise, and any resolver failure that isn't a conclusive "this domain cannot receive mail" (timeout, no nameservers, unexpected exception) must degrade to `"unknown"`, which the caller treats as "draft anyway" — same governance shape as the visa gate's NULL-never-a-false-negative rule, applied to a blocking gate instead of a tagging one.
- **Only `"invalid"` blocks.** `agent.py` must never skip a contact on `"unknown"`.
- **No new table, no new column, no caching.** Matches the ATS/JobRight "no persisted volatile signal" restraint — see spec's Non-goals.
- **Test command in this environment:** `python3 -m pytest` after `pip install -r requirements.txt` plus `pip install pytest pytest-mock`. `tests/conftest.py` sets fake env vars, so no real secrets are needed. Run targeted files during development.

---

## File Structure

**Create:**
- `email_verify.py` — syntax check, MX/A DNS check, `verify()` public function.
- `tests/test_email_verify.py` — module unit tests, all DNS resolution mocked.
- `tests/test_agent_email_verify.py` — wiring tests for `agent.run()`'s Phase 1 loop.

**Modify:**
- `requirements.txt` — add `dnspython`.
- `config.py` — `EMAIL_VERIFY_*` constants beside the `ATS_*` block.
- `agent.py` — import `email_verify`, gate in Phase 1 loop, `log_agent_event` import.
- `CLAUDE.md` — module layout entry, new `[EMAIL-VERIFY]` marker note, new section.

---

## Task 1: Dependency + config constants

**Files:**
- Modify: `requirements.txt`, `config.py`

**Interfaces:**
- Produces: `EMAIL_VERIFY_ENABLED`, `EMAIL_VERIFY_DNS_TIMEOUT_SECONDS`.

- [x] **Step 1: Add `dnspython` to `requirements.txt`**, alphabetically-adjacent is not this repo's convention (it's append-ordered by feature, see `pikepdf`/`pypdf` at the bottom for Phase 3) — append it at the end:

```
dnspython>=2.6.0
```

- [x] **Step 2: Install locally** — `pip install -r requirements.txt` and confirm `import dns.resolver` works.

- [x] **Step 3: Add the config block** directly after the `ATS_*` block in `config.py`:

```python
# ── Email verification pre-flight ────────────────────────────────────────────

EMAIL_VERIFY_ENABLED = True
# Short on purpose. This runs inside a per-contact loop in a cron job, so a
# blackholed or slow resolver must never stall a run.
EMAIL_VERIFY_DNS_TIMEOUT_SECONDS = 5
```

- [x] **Step 4: Commit** (folded into Task 2's commit — nothing imports these yet)

---

## Task 2: `email_verify.py`

**Files:**
- Create: `email_verify.py`
- Test: `tests/test_email_verify.py`

**Interfaces:**
- Produces: `EmailVerifyResult` namedtuple, `verify(email) -> EmailVerifyResult`. Internal: `_check_syntax(email)`, `_check_domain(domain)`.

- [x] **Step 1: Write the failing tests**

```python
"""Tests for email_verify -- bounce-risk pre-flight check. All DNS is mocked."""

import dns.exception
import dns.resolver
import pytest

import email_verify


# ── Syntax ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("email", [
    "dana@example.com",
    "first.last@sub.example.co",
    "a+tag@example.io",
])
def test_verify_valid_syntax_reaches_dns(mocker, email):
    mock_answer = [mocker.MagicMock()]
    mocker.patch("dns.resolver.resolve", return_value=mock_answer)
    result = email_verify.verify(email)
    assert result.status == "valid"


@pytest.mark.parametrize("email", [
    "",
    None,
    "no-at-sign",
    "@no-local-part.com",
    "trailing@dot.",
    "spaces in@email.com",
    "two@@signs.com",
    123,
])
def test_verify_malformed_syntax_is_invalid_without_dns(mocker, email):
    resolve = mocker.patch("dns.resolver.resolve")
    result = email_verify.verify(email)
    assert result.status == "invalid"
    resolve.assert_not_called()


# ── Domain / DNS ────────────────────────────────────────────────────────────

def test_verify_mx_hit_is_valid(mocker):
    mocker.patch("dns.resolver.resolve", return_value=[mocker.MagicMock()])
    result = email_verify.verify("dana@example.com")
    assert result.status == "valid"


def test_verify_no_mx_but_a_record_falls_back_to_valid(mocker):
    def resolve_side_effect(domain, rdtype, **kwargs):
        if rdtype == "MX":
            raise dns.resolver.NoAnswer()
        return [mocker.MagicMock()]
    mocker.patch("dns.resolver.resolve", side_effect=resolve_side_effect)
    result = email_verify.verify("dana@example.com")
    assert result.status == "valid"


def test_verify_no_mx_and_no_a_record_is_invalid(mocker):
    def resolve_side_effect(domain, rdtype, **kwargs):
        if rdtype == "MX":
            raise dns.resolver.NoAnswer()
        raise dns.resolver.NXDOMAIN()
    mocker.patch("dns.resolver.resolve", side_effect=resolve_side_effect)
    result = email_verify.verify("dana@example.com")
    assert result.status == "invalid"


def test_verify_nxdomain_on_mx_is_invalid(mocker):
    mocker.patch("dns.resolver.resolve", side_effect=dns.resolver.NXDOMAIN())
    result = email_verify.verify("dana@nonexistent-domain-xyz.test")
    assert result.status == "invalid"


@pytest.mark.parametrize("exc", [
    dns.exception.Timeout(),
    dns.resolver.NoNameservers(),
    RuntimeError("resolver blew up"),
])
def test_verify_resolver_failure_is_unknown(mocker, exc):
    mocker.patch("dns.resolver.resolve", side_effect=exc)
    result = email_verify.verify("dana@example.com")
    assert result.status == "unknown"


def test_verify_never_raises(mocker):
    mocker.patch("dns.resolver.resolve", side_effect=Exception("anything"))
    result = email_verify.verify("dana@example.com")
    assert result.status == "unknown"
```

- [x] **Step 2: Run tests to verify they fail** — `python3 -m pytest tests/test_email_verify.py -v` — fails on `ModuleNotFoundError: No module named 'email_verify'`.

- [x] **Step 3: Implement `email_verify.py`**

```python
from collections import namedtuple

import dns.exception
import dns.resolver

from config import EMAIL_VERIFY_DNS_TIMEOUT_SECONDS

EmailVerifyResult = namedtuple("EmailVerifyResult", ["status", "reason"])

# ── Syntax ──────────────────────────────────────────────────────────────────

_EMAIL_RE_LOCAL = r"[A-Za-z0-9][A-Za-z0-9._%+-]*"
_EMAIL_RE_DOMAIN = r"[A-Za-z0-9][A-Za-z0-9.-]*\.[A-Za-z]{2,}"
import re
_EMAIL_RE = re.compile(rf"^{_EMAIL_RE_LOCAL}@{_EMAIL_RE_DOMAIN}$")

def _check_syntax(email):
    if not isinstance(email, str) or not email:
        return False
    return bool(_EMAIL_RE.match(email.strip()))


# ── Domain / DNS ────────────────────────────────────────────────────────────

def _resolve(domain, rdtype):
    return dns.resolver.resolve(domain, rdtype, lifetime=EMAIL_VERIFY_DNS_TIMEOUT_SECONDS)

def _check_domain(domain):
    """
    Returns "valid" (MX or fallback A/AAAA resolves), "invalid" (conclusively
    cannot receive mail), or "unknown" (lookup itself failed).
    """
    try:
        _resolve(domain, "MX")
        return "valid"
    except dns.resolver.NXDOMAIN:
        return "invalid"
    except dns.resolver.NoAnswer:
        pass
    except (dns.exception.Timeout, dns.resolver.NoNameservers):
        return "unknown"
    except Exception:
        return "unknown"

    # No MX record published — RFC 5321 fallback: mail can still route to an
    # A/AAAA record. Try both; either resolving counts as reachable.
    for rdtype in ("A", "AAAA"):
        try:
            _resolve(domain, rdtype)
            return "valid"
        except dns.resolver.NXDOMAIN:
            continue
        except dns.resolver.NoAnswer:
            continue
        except (dns.exception.Timeout, dns.resolver.NoNameservers):
            return "unknown"
        except Exception:
            return "unknown"
    return "invalid"


# ── Public interface ────────────────────────────────────────────────────────

def verify(email):
    """Bounce-risk check: syntax + MX/A DNS lookup. Never raises."""
    try:
        if not _check_syntax(email):
            return EmailVerifyResult("invalid", f"malformed address: {email!r}")
        domain = email.strip().rsplit("@", 1)[1]
        status = _check_domain(domain)
        if status == "valid":
            return EmailVerifyResult("valid", None)
        if status == "invalid":
            return EmailVerifyResult("invalid", f"domain has no mail route: {domain}")
        return EmailVerifyResult("unknown", f"DNS lookup failed for {domain}")
    except Exception as exc:
        return EmailVerifyResult("unknown", f"verify() error: {exc}")
```

Note the outer `try/except` in `verify()` is defense in depth on top of
`_check_domain`'s own exhaustive handling — e.g. a malformed `domain` string
after the `@` split, or a `dnspython` internal error not in the caught set,
still degrades to `"unknown"` rather than raising into `agent.py`.

- [x] **Step 4: Run tests to verify they pass** — `python3 -m pytest tests/test_email_verify.py -v`

- [x] **Step 5: Run the full test suite** — `python3 -m pytest` — must stay green.

- [x] **Step 6: Commit**

```
feat: add email_verify.py bounce-risk DNS check (Phase 5, not yet wired)
```

---

## Task 3: Wire into `agent.py`

**Files:**
- Modify: `agent.py`
- Test: `tests/test_agent_email_verify.py`

**Interfaces:**
- Consumes: `email_verify.verify`, `config.EMAIL_VERIFY_ENABLED`, `db.log_agent_event`.

- [x] **Step 1: Write the failing tests**

```python
"""Wiring tests for the email-verify pre-flight gate in agent.run()'s Phase 1 loop."""

from unittest.mock import MagicMock

import agent
import config
from email_verify import EmailVerifyResult


def _build_contact(**overrides):
    contact = {
        "id": 1, "name": "Dana", "email": "dana@example.com",
        "company": "Clearbond", "mode": "outreach", "stage": "new",
        "reply_status": "no_reply", "tier": 2,
    }
    contact.update(overrides)
    return contact


def test_invalid_email_skips_before_batch_is_built(mocker):
    contact = _build_contact()
    mocker.patch("agent.get_all_contacts", return_value=[contact])
    mocker.patch("agent.email_verify.verify",
                 return_value=EmailVerifyResult("invalid", "domain has no mail route"))
    log_event = mocker.patch("agent.log_agent_event")
    prepare = mocker.patch("agent.prepare_email")
    batch_client = MagicMock()
    mocker.patch("agent.anthropic.Anthropic", return_value=batch_client)
    record_run = mocker.patch("agent.record_run")

    agent.run()

    prepare.assert_not_called()
    batch_client.messages.batches.create.assert_not_called()
    record_run.assert_called_once_with("success", 0, 1, 0, mocker.ANY)
    assert log_event.call_args.kwargs.get("status") == "blocked_invalid_email"
    assert log_event.call_args.args[0] == "email_verify"


def test_unknown_email_status_proceeds_to_batch(mocker):
    contact = _build_contact()
    mocker.patch("agent.get_all_contacts", return_value=[contact])
    mocker.patch("agent.email_verify.verify",
                 return_value=EmailVerifyResult("unknown", "DNS timeout"))
    mocker.patch("agent.prepare_email", return_value=("prompt", "system", {}))
    batch_client = MagicMock()
    batch_client.messages.batches.create.side_effect = RuntimeError("stop here")
    mocker.patch("agent.anthropic.Anthropic", return_value=batch_client)
    mocker.patch("agent.record_run")

    agent.run()

    batch_client.messages.batches.create.assert_called_once()


def test_valid_email_proceeds_to_batch(mocker):
    contact = _build_contact()
    mocker.patch("agent.get_all_contacts", return_value=[contact])
    mocker.patch("agent.email_verify.verify",
                 return_value=EmailVerifyResult("valid", None))
    mocker.patch("agent.prepare_email", return_value=("prompt", "system", {}))
    batch_client = MagicMock()
    batch_client.messages.batches.create.side_effect = RuntimeError("stop here")
    mocker.patch("agent.anthropic.Anthropic", return_value=batch_client)
    mocker.patch("agent.record_run")

    agent.run()

    batch_client.messages.batches.create.assert_called_once()


def test_followup_action_never_calls_verify(mocker):
    contact = _build_contact(stage="first_touch_sent",
                             followup_date="2020-01-01")
    mocker.patch("agent.get_all_contacts", return_value=[contact])
    mocker.patch("agent.get_thread_info", return_value={})
    verify = mocker.patch("agent.email_verify.verify")
    mocker.patch("agent.prepare_email", return_value=("prompt", "system", {}))
    batch_client = MagicMock()
    batch_client.messages.batches.create.side_effect = RuntimeError("stop here")
    mocker.patch("agent.anthropic.Anthropic", return_value=batch_client)
    mocker.patch("agent.record_run")

    agent.run()

    verify.assert_not_called()


def test_email_verify_disabled_skips_the_check_entirely(mocker):
    contact = _build_contact()
    mocker.patch.object(config, "EMAIL_VERIFY_ENABLED", False)
    mocker.patch("agent.get_all_contacts", return_value=[contact])
    verify = mocker.patch("agent.email_verify.verify")
    mocker.patch("agent.prepare_email", return_value=("prompt", "system", {}))
    batch_client = MagicMock()
    batch_client.messages.batches.create.side_effect = RuntimeError("stop here")
    mocker.patch("agent.anthropic.Anthropic", return_value=batch_client)
    mocker.patch("agent.record_run")

    agent.run()

    verify.assert_not_called()
    batch_client.messages.batches.create.assert_called_once()


def test_verify_raising_does_not_break_the_run(mocker):
    contact = _build_contact()
    mocker.patch("agent.get_all_contacts", return_value=[contact])
    mocker.patch("agent.email_verify.verify", side_effect=RuntimeError("boom"))
    mocker.patch("agent.prepare_email", return_value=("prompt", "system", {}))
    mocker.patch("agent.record_run")

    agent.run()  # must not raise
```

Note: `agent.py` reads `config.EMAIL_VERIFY_ENABLED` at call time (not
imported by value) so `mocker.patch.object(config, ...)` in the disabled
test actually takes effect — same reason `ATS_ENABLED` is read as
`config.ATS_ENABLED` inside `research.py` rather than imported directly.

- [x] **Step 2: Run tests to verify they fail** — `python3 -m pytest tests/test_agent_email_verify.py -v` — fails, `agent.email_verify` doesn't exist yet.

- [x] **Step 3: Implement** — in `agent.py`:

Add the import (alongside the existing `db` import line) and module import:

```python
from db import get_all_contacts, update_contact, close_contact, save_thread_info, get_thread_info, load_prompts, get_pause_scope, record_run, insert_email_message, log_drafted_email, update_message_id, update_latest_message_id, log_agent_event
import config
import email_verify
```

In the Phase 1 loop, right after the existing `action == "skip"` block and
before the `thread_message_id` / `prepare_email` block:

```python
        if action in _FIRST_TOUCH_ACTIONS and config.EMAIL_VERIFY_ENABLED:
            try:
                verify_result = email_verify.verify(contact.get("email", ""))
            except Exception as exc:
                log.warning(f"{mode_tag} {name} | {company} | email-verify raised ({exc}) — allowing")
                verify_result = None
            if verify_result is not None and verify_result.status == "invalid":
                log.warning(
                    f"{mode_tag} {name} | {company} | skip | "
                    f"[EMAIL-VERIFY] invalid email: {verify_result.reason}"
                )
                log_agent_event(
                    "email_verify", contact_id=contact.get("id"), contact_name=name,
                    status="blocked_invalid_email",
                    metadata={"email": contact.get("email"), "reason": verify_result.reason},
                )
                skipped += 1
                continue
```

The `try/except` here is belt-and-suspenders on top of `verify()`'s own
never-raises contract — a defect in `email_verify.py` must never take down
the whole run, matching the labeling and ATS best-effort posture elsewhere
in this codebase.

- [x] **Step 4: Run tests to verify they pass** — `python3 -m pytest tests/test_agent_email_verify.py tests/test_agent.py -v`

- [x] **Step 5: Run the full test suite** — `python3 -m pytest` — must stay green, including the pre-existing `tests/test_agent.py` batch tests (they use real-looking emails like `dana@example.com`/`example.com` domain, but since `email_verify.verify` is not mocked in those older tests, confirm whether `EMAIL_VERIFY_ENABLED` gating requires those tests to mock it too — see Step 6).

- [x] **Step 6: Fix any newly-broken pre-existing tests.** Every existing `tests/test_agent.py` batch test that reaches the Phase 1 loop with a first-touch action will now call `email_verify.verify()` for real (a live DNS lookup) unless mocked, which breaks the "tests never travel" rule and makes tests flaky. Add `mocker.patch("agent.email_verify.verify", return_value=EmailVerifyResult("valid", None))` to `_mock_batch_pipeline` (the shared helper in `tests/test_agent.py`) so every existing batch test gets it automatically, rather than editing each test individually.

- [x] **Step 7: Run the full test suite again** — `python3 -m pytest` — must be green with no live network calls (spot-check by running with network disabled or just checking test duration didn't spike).

- [x] **Step 8: Commit**

```
feat: wire email_verify bounce-risk gate into agent.py Phase 1 loop
```

---

## Task 4: Docs

**Files:**
- Modify: `CLAUDE.md`

- [x] **Step 1: Add `email_verify.py` to the Module layout list** (alphabetical-by-feature-grouping is not enforced elsewhere, so append near `content_trust.py`/`ats.py` since it's the same "self-contained enrichment/gate module" family).

- [x] **Step 2: Add a new `## Email verification pre-flight (full-fledged buildout, Phase 5)` section**, summarizing: new module, three-way `valid`/`invalid`/`unknown` status contract, only `"invalid"` blocks, gated to `_FIRST_TOUCH_ACTIONS`, `EMAIL_VERIFY_ENABLED` off-switch, no new table/column, `[EMAIL-VERIFY]` marker, `agent_events` event type `"email_verify"`. Cross-reference the design spec.

- [x] **Step 3: Update the buildout master spec** (`docs/superpowers/specs/2026-08-26-full-fledged-job-platform-buildout.md`) — mark Phase 5 as shipped, referencing this plan.

- [x] **Step 4: Commit**

```
docs: document Phase 5 email verification pre-flight in CLAUDE.md
```

---

## Task 5: Final full-suite check

- [x] **Step 1:** `python3 -m pytest` — full green run, note the new passing count vs. the pre-feature baseline.
- [x] **Step 2:** Confirm `EMAIL_VERIFY_ENABLED = False` (temporarily, locally) reproduces byte-identical Phase 1 behavior — run one of the pre-existing batch tests with it toggled off and confirm no regression, then revert.
- [x] **Step 3:** Commit any final cleanup.
