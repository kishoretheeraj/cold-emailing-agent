"""approval_signature.py and its gate in apply_agent.submit() (spec §9.1). The contact-manager's
submit route signs id + rendered hash + epoch-ms with APPROVAL_SIGNING_KEY; a worker refuses any
approval it cannot verify, so an approve_application call made with the public anon key alone
never reaches a Submit click."""

from unittest.mock import MagicMock

import pytest

import approval_signature
import apply_agent

KEY = "k" * 32
HASH = "a" * 64
SIGNED_AT_MS = 1791428400000  # 2026-10-08T03:00:00Z
# Shared with contact-manager/src/lib/approvalSignature.test.ts -- both sides must agree.
VECTOR = "813fb85b60f9e586a35ca8c3bff7a1636ace15b7aaa404167fbb7505fb1e751d"


def _signed_job(**overrides):
    job = {"id": 42, "approved_revision_hash": HASH, "approval_signed_at_ms": SIGNED_AT_MS,
           "approval_signature": VECTOR, "approved_at": "2026-10-08T03:00:01.250+00:00",
           "approval_expires_at": "2999-01-01T00:00:00+00:00"}
    job.update(overrides)
    return job


def test_sign_matches_the_shared_vector():
    assert approval_signature.sign(KEY, 42, HASH, SIGNED_AT_MS) == VECTOR


def test_verify_accepts_a_valid_signature():
    assert approval_signature.verify(_signed_job(), KEY) is None


@pytest.mark.parametrize("overrides,reason", [
    ({"approval_signature": None}, "not signed"),
    ({"approval_signature": "0" * 64}, "does not match"),
    ({"approval_signature": VECTOR.upper()}, "does not match"),
    ({"id": 43}, "does not match"),
    ({"approved_revision_hash": "b" * 64}, "does not match"),
    ({"approval_signed_at_ms": SIGNED_AT_MS + 1}, "does not match"),
    ({"approval_signed_at_ms": None}, "not signed"),
    ({"approval_signed_at_ms": "1791428400000x"}, "not signed"),
    ({"approved_revision_hash": None}, "not signed"),
    ({"approved_at": None}, "stale"),
    ({"approved_at": "not a date"}, "stale"),
    # a signature minted for an approval long before this one (replayed after a reset)
    ({"approved_at": "2026-10-08T03:05:01+00:00"}, "stale"),
    ({"approved_at": "2026-10-08T02:54:59+00:00"}, "stale"),
])
def test_verify_rejects(overrides, reason):
    assert reason in approval_signature.verify(_signed_job(**overrides), KEY)


def test_signed_at_ms_may_arrive_as_a_numeric_string():
    # PostgREST returns BIGINT as a JSON number, but a string must not break verification.
    assert approval_signature.verify(_signed_job(approval_signed_at_ms=str(SIGNED_AT_MS)), KEY) is None


EXPIRES = "2026-10-15T03:00:01.250+00:00"
EXPIRES_MS = 1792033201250


def test_verify_accepts_before_expiry_and_rejects_after():
    job = _signed_job(approval_expires_at=EXPIRES)
    assert approval_signature.verify(job, KEY, now_ms=EXPIRES_MS) is None
    assert "expired" in approval_signature.verify(job, KEY, now_ms=EXPIRES_MS + 1)


@pytest.mark.parametrize("expires", [None, "", "not a date"])
def test_verify_fails_closed_without_a_valid_expiry(expires):
    assert "expired" in approval_signature.verify(_signed_job(approval_expires_at=expires), KEY)


def test_a_thirty_day_old_approval_does_not_verify():
    job = _signed_job(approval_expires_at=EXPIRES)
    thirty_days_later = SIGNED_AT_MS + 30 * 24 * 3600 * 1000
    assert "expired" in approval_signature.verify(job, KEY, now_ms=thirty_days_later)


@pytest.mark.parametrize("key", [None, "", "short-key"])
def test_key_must_be_configured_and_long(key):
    assert not approval_signature.key_configured(key)
    assert "APPROVAL_SIGNING_KEY" in approval_signature.verify(_signed_job(), key)


def test_key_is_read_from_the_environment_at_call_time(monkeypatch):
    monkeypatch.delenv("APPROVAL_SIGNING_KEY", raising=False)
    assert not approval_signature.key_configured()
    monkeypatch.setenv("APPROVAL_SIGNING_KEY", KEY)
    assert approval_signature.key_configured()
    assert approval_signature.verify(_signed_job()) is None


# ── submit() gate ─────────────────────────────────────────────────────────────

@pytest.fixture
def signed_row():
    return {"id": 42, "company": "Acme", "job_url": "https://boards.greenhouse.io/acme/jobs/1",
            "stage": "ready_to_submit", "apply_preview": {"platform": "greenhouse", "field_values": {},
            "screening_answers": {}, "eligibility_answers": {}}, "automation_status": "submitting",
            "preview_revision_hash": HASH, "resume_file_ref": "r", "cover_letter_file_ref": "c",
            **_signed_job()}


def _wire(mocker, row):
    mocker.patch.dict("os.environ", {"APPLY_AGENT_ARMED": "1", "APPROVAL_SIGNING_KEY": KEY})
    mocker.patch.object(apply_agent.db, "recover_stale_leases", return_value=0)
    mocker.patch.object(apply_agent.db, "get_job_application", return_value=row)
    claim = mocker.patch.object(apply_agent.db, "claim_application", return_value="lease-1")
    release = mocker.patch.object(apply_agent.db, "release_application")
    mocker.patch.object(apply_agent.db, "record_submission", return_value=True)
    mocker.patch.object(apply_agent.db, "heartbeat_application")
    mocker.patch.object(apply_agent.db, "renew_submission_lease", return_value=True)
    launch = mocker.patch.object(apply_agent, "_launch_page", return_value=MagicMock())
    mocker.patch.object(apply_agent, "_close_page")
    mocker.patch.object(apply_agent, "_form_inventory", return_value=[])
    mocker.patch.object(apply_agent.ats_fillers, "fill_greenhouse",
                        return_value={"first_name": True, "last_name": True, "email": True})
    mocker.patch.object(apply_agent, "_attach_resume_and_cover_letter", return_value={"resume": True, "cover_letter": True})
    mocker.patch.object(apply_agent, "_fill_screening_questions")
    mocker.patch.object(apply_agent, "_fill_eligibility_answers")
    mocker.patch.object(apply_agent, "_submission_confirmed", return_value=True)
    return claim, release, launch


def test_submit_with_a_valid_signature_proceeds(mocker, signed_row):
    _, release, launch = _wire(mocker, signed_row)
    apply_agent.submit(42)
    launch.assert_called_once()
    release.assert_not_called()


def test_submit_with_a_forged_signature_never_opens_a_browser(mocker, signed_row):
    signed_row["approval_signature"] = "f" * 64
    _, release, launch = _wire(mocker, signed_row)
    with pytest.raises(apply_agent.ApprovalSignatureError):
        apply_agent.submit(42)
    launch.assert_not_called()
    # needs_input clears the approval, so only a fresh human tap can approve it again.
    args = release.call_args[0]
    assert args[:3] == (42, "lease-1", "needs_input")
    assert "signature" in args[3]


def test_submit_without_a_configured_key_refuses_before_claiming(mocker, signed_row):
    claim, release, launch = _wire(mocker, signed_row)
    mocker.patch.dict("os.environ", {"APPROVAL_SIGNING_KEY": ""})
    with pytest.raises(RuntimeError, match="APPROVAL_SIGNING_KEY"):
        apply_agent.submit(42)
    # An unconfigured worker is not the row's fault: the approval is left intact.
    claim.assert_not_called()
    release.assert_not_called()
    launch.assert_not_called()
