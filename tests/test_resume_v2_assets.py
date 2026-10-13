"""Resume v2 assets: anti-watermark shim, cache, manifest, evidence and the build_assets orchestrator.

Every model call and external command is stubbed; nothing leaves the process.
"""

import hashlib
import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from resume.v2 import build, cache, evidence, llm, manifest, tailor_v2, wm_stage

# ── Fixtures ───────────────────────────────────────────────────────────────────

ROLE_A = "Acme Corp: Product Manager"
ROLE_B = "Beta Labs: Associate Product Manager"
TEXTS = [
    (ROLE_A, "Led the payments roadmap for 40 merchants and cut onboarding time by 30%.", ["roadmap", "payments"]),
    (ROLE_A, "Built an experimentation process that raised checkout conversion by 12%.", ["experimentation"]),
    (ROLE_A, "Partnered with engineering to ship a billing API used by 8 teams.", ["api", "billing"]),
    (ROLE_B, "Wrote SQL dashboards that tracked weekly retention for 5 products.", ["sql", "dashboards"]),
    (ROLE_B, "Interviewed 25 customers to prioritize the mobile backlog.", ["research", "mobile"]),
    (ROLE_B, "Documented requirements for a partner portal launched in 3 months.", ["requirements"]),
]
PROFILE = {
    "name": "Test Candidate",
    "contact": {"location": "Town, ST", "phone": "000-000-0000", "email": "cand@example.test",
                "linkedin": "linkedin.com/in/test"},
    "education": ["Example University | BS"],
    "bullets": [{"id": f"b{i}", "role": r, "text": t, "tags": tags} for i, (r, t, tags) in enumerate(TEXTS)],
}
CONTENT = {
    "headline": "Product Manager",
    "summary": "Product manager focused on payments, analytics and platform delivery.",
    "experience": [
        {"role": ROLE_A, "bullets": [t for r, t, _ in TEXTS if r == ROLE_A]},
        {"role": ROLE_B, "bullets": [t for r, t, _ in TEXTS if r == ROLE_B][:2]},
    ],
    "skills": ["roadmap", "payments", "sql"],
}
REQS = {"must_have": ["product management", "sql"], "nice_to_have": [], "keywords": ["payments"],
        "role_title": "Product Manager", "company": "Acme"}
JOB = {"id": "job-1", "company": "Acme", "title": "Product Manager"}
JD = "We are hiring a product manager to own payments and onboarding. " * 5
BANK = {"full_name": "Test Candidate", "email": "cand@example.test", "phone": "000", "linkedin": "x",
        "location": "Town, ST"}


def _no_letter():
    raise ImportError("cover_letter unavailable")


@pytest.fixture
def stubs(monkeypatch):
    calls = []

    def fake_call(prompt, system, model, action, max_output_tokens=None):
        calls.append(action)
        if action == "extract_requirements":
            return json.dumps(REQS)
        if action in ("generate", "repair"):
            return json.dumps(CONTENT)
        raise AssertionError(action)

    monkeypatch.setattr(llm, "call_claude", fake_call)
    monkeypatch.setattr(wm_stage, "run_anti_watermark",
                        lambda texts, timeout_s=150: (list(texts), {"wm_applied": True}))
    monkeypatch.setattr(build, "_load_letter_builder", _no_letter)
    monkeypatch.setenv("RESUME_V2_RANKER_BACKEND", "tfidf")
    return calls


def _build(tmp_path, **kw):
    return build.build_assets(JOB, JD, PROFILE, BANK, tmp_path / "art", cache_root=tmp_path / "cache",
                              master_md_sha="m1", **kw)


# ── wm_stage ───────────────────────────────────────────────────────────────────

class _Proc:
    def __init__(self, out="", code=0):
        self.stdout, self.returncode, self.stderr = out, code, ""


@pytest.fixture
def wm_env(monkeypatch):
    monkeypatch.setenv("RESUME_DEWATERMARK_CMD", "dewm --flag")
    monkeypatch.setenv("RESUME_PARAPHRASE_CMD", "para")


def _runner(calls, handler=None):
    def run(argv, input, capture_output, text, timeout):
        calls.append(argv[0])
        if handler:
            return handler(len(calls), input)
        return _Proc(json.dumps({"texts": json.loads(input)["texts"]}))
    return run


def test_wm_stage_runs_three_steps_in_order(monkeypatch, wm_env):
    calls = []
    monkeypatch.setattr(wm_stage.subprocess, "run", _runner(calls))
    out, meta = wm_stage.run_anti_watermark(["a", "b"])
    assert out == ["a", "b"] and meta == {"wm_applied": True}
    assert calls == ["dewm", "para", "dewm"]


def test_wm_stage_passes_transformed_text_between_steps(monkeypatch, wm_env):
    def handler(n, inp):
        return _Proc(json.dumps({"texts": [t + str(n) for t in json.loads(inp)["texts"]]}))
    monkeypatch.setattr(wm_stage.subprocess, "run", _runner([], handler))
    assert wm_stage.run_anti_watermark(["x"])[0] == ["x123"]


@pytest.mark.parametrize("missing", ["RESUME_DEWATERMARK_CMD", "RESUME_PARAPHRASE_CMD"])
def test_wm_stage_unset_command_is_unavailable(monkeypatch, wm_env, missing):
    calls = []
    monkeypatch.setattr(wm_stage.subprocess, "run", _runner(calls))
    monkeypatch.delenv(missing)
    with pytest.raises(wm_stage.WmStageError, match="wm_unavailable"):
        wm_stage.run_anti_watermark(["a"])
    assert calls == []  # availability is checked before any subprocess runs


def test_wm_stage_empty_command_is_unavailable(monkeypatch, wm_env):
    calls = []
    monkeypatch.setattr(wm_stage.subprocess, "run", _runner(calls))
    monkeypatch.setenv("RESUME_PARAPHRASE_CMD", "   ")
    with pytest.raises(wm_stage.WmStageError, match="wm_unavailable"):
        wm_stage.run_anti_watermark(["a"])
    assert calls == []


def test_wm_stage_command_vanishing_mid_run_is_unavailable_without_execution(monkeypatch, wm_env):
    calls = []

    def handler(n, inp):
        monkeypatch.delenv("RESUME_PARAPHRASE_CMD")  # gone before the second step resolves it
        return _Proc(json.dumps({"texts": json.loads(inp)["texts"]}))
    monkeypatch.setattr(wm_stage.subprocess, "run", _runner(calls, handler))
    with pytest.raises(wm_stage.WmStageError, match="wm_unavailable"):
        wm_stage.run_anti_watermark(["a"])
    assert calls == ["dewm"]  # the unavailable step never executed


def test_wm_stage_resolves_the_command_on_every_attempt(monkeypatch, wm_env):
    calls = []

    def handler(n, inp):
        if n == 1:
            monkeypatch.setenv("RESUME_DEWATERMARK_CMD", "dewm2")  # fixed between attempts
            return _Proc("", 1)
        return _Proc(json.dumps({"texts": json.loads(inp)["texts"]}))
    monkeypatch.setattr(wm_stage.subprocess, "run", _runner(calls, handler))
    assert wm_stage.run_anti_watermark(["a"])[1] == {"wm_applied": True}
    assert calls == ["dewm", "dewm2", "para", "dewm2"]


def test_wm_stage_failing_step_retries_once_then_raises(monkeypatch, wm_env):
    calls = []
    monkeypatch.setattr(wm_stage.subprocess, "run", _runner(calls, lambda n, i: _Proc("", 1)))
    with pytest.raises(wm_stage.WmStageError, match="wm_step_failed:dewatermark:exit_1"):
        wm_stage.run_anti_watermark(["a"])
    assert calls == ["dewm", "dewm"]


def test_wm_stage_retry_can_recover(monkeypatch, wm_env):
    calls = []

    def handler(n, inp):
        if n == 1:
            raise wm_stage.subprocess.TimeoutExpired("dewm", 60)
        return _Proc(json.dumps({"texts": json.loads(inp)["texts"]}))
    monkeypatch.setattr(wm_stage.subprocess, "run", _runner(calls, handler))
    assert wm_stage.run_anti_watermark(["a"])[1] == {"wm_applied": True}
    assert len(calls) == 4


@pytest.mark.parametrize("stdout", ["not json", json.dumps({"texts": ["a", "b"]}), json.dumps({"texts": [1]}),
                                    json.dumps(["a"]), json.dumps({"other": []})])
def test_wm_stage_malformed_output_raises(monkeypatch, wm_env, stdout):
    monkeypatch.setattr(wm_stage.subprocess, "run", _runner([], lambda n, i: _Proc(stdout)))
    with pytest.raises(wm_stage.WmStageError):
        wm_stage.run_anti_watermark(["a"])


def test_wm_stage_error_never_contains_text(monkeypatch, wm_env):
    monkeypatch.setattr(wm_stage.subprocess, "run", _runner([], lambda n, i: _Proc("SECRET-TEXT", 2)))
    with pytest.raises(wm_stage.WmStageError) as exc:
        wm_stage.run_anti_watermark(["SECRET-TEXT"])
    assert "SECRET-TEXT" not in str(exc.value)


# ── cache ──────────────────────────────────────────────────────────────────────

def test_cache_key_is_stable_and_input_sensitive():
    args = ("m", "t1", "p1", "j", {"haiku": "h", "sonnet": "s"})
    assert cache.cache_key(*args) == cache.cache_key(*args)
    assert cache.cache_key(*args) == hashlib.sha256(
        "|".join(["m", "t1", "p1", "j", json.dumps(args[4], sort_keys=True)]).encode()).hexdigest()
    assert cache.cache_key("m", "t2", "p1", "j", args[4]) != cache.cache_key(*args)


def test_jd_whitespace_and_case_variants_share_a_key():
    assert build._jd_sha("Hello   World\n") == build._jd_sha(" hello world ")
    assert build._jd_sha("hello world") != build._jd_sha("hello there")


def test_cache_round_trip_and_permissions(tmp_path):
    key = cache.cache_key("m", "t", "p", "j", "ids")
    assert cache.load_cached(key, tmp_path) is None
    cache.save_cached(key, {"content": {"a": 1}}, tmp_path)
    assert cache.load_cached(key, tmp_path) == {"content": {"a": 1}}
    assert (tmp_path / key).stat().st_mode & 0o777 == 0o700
    assert (tmp_path / key / "cache.json").stat().st_mode & 0o777 == 0o600


def test_cache_rejects_path_traversal_keys(tmp_path):
    with pytest.raises(ValueError):
        cache.cache_dir("../evil", tmp_path)


def test_cache_corrupt_file_reads_as_miss(tmp_path):
    key = cache.cache_key("m", "t", "p", "j", "ids")
    cache.cache_dir(key, tmp_path)
    (tmp_path / key / "cache.json").write_text("{broken")
    assert cache.load_cached(key, tmp_path) is None


# ── manifest ───────────────────────────────────────────────────────────────────

def test_manifest_write_and_verify(tmp_path):
    pdf = tmp_path / "r.pdf"
    pdf.write_bytes(b"%PDF-fake")
    data = manifest.write_manifest(tmp_path / "m.json", {"pdf_sha256": hashlib.sha256(b"%PDF-fake").hexdigest()})
    assert data["manifest_version"] == manifest.MANIFEST_VERSION and data["ts"]
    assert json.loads((tmp_path / "m.json").read_text()) == data
    assert manifest.verify_pdf_hash(data, str(pdf)) is True
    pdf.write_bytes(b"%PDF-tampered")
    assert manifest.verify_pdf_hash(data, str(pdf)) is False
    assert manifest.verify_pdf_hash(data, str(tmp_path / "missing.pdf")) is False
    assert manifest.verify_pdf_hash({}, str(pdf)) is False


# ── evidence ───────────────────────────────────────────────────────────────────

def test_evidence_shape():
    ranked = [{"id": "b1", "role": "R", "text": "T", "tags": ["x"], "score": 0.9}]
    ev = evidence.build_evidence(JOB, "j" * 13000, REQS, ranked,
                                 {"full_name": "N", "email": "e", "extra": "dropped",
                                  "unknown_do_not_invent": ["topic"]})
    assert set(ev) == {"requirements", "bullets", "bank_facts", "jd_text", "banned_words", "unknown_topics"}
    assert ev["bullets"] == [{"id": "b1", "role": "R", "text": "T", "tags": ["x"]}]
    assert ev["bank_facts"] == {"full_name": "N", "email": "e", "phone": "", "linkedin": "", "location": ""}
    assert len(ev["jd_text"]) == 12000
    assert "robust" in ev["banned_words"] and ev["unknown_topics"] == ["topic"]
    assert evidence.build_evidence(JOB, "", REQS, [], None)["unknown_topics"] == []


# ── build_assets ───────────────────────────────────────────────────────────────

def test_build_assets_then_cache_hit(tmp_path, stubs):
    first = _build(tmp_path)
    assert first.cache_hit is False
    assert Path(first.resume_pdf).read_bytes().startswith(b"%PDF")
    assert first.resume_manifest["tailored"] is True and first.resume_manifest["wm_applied"] is True
    assert {"requirements", "rank", "generate", "validate", "wm", "render", "total"} <= set(first.stage_timings)
    assert manifest.verify_pdf_hash(first.resume_manifest, first.resume_pdf)
    assert first.resume_content["name"] == "Test Candidate"
    assert first.letter is None and first.letter_review_reasons == ["letter_unavailable"]
    for name in ("resume.content.json", "resume.manifest.json", "jd.txt"):
        assert (Path(first.resume_pdf).parent / name).is_file()

    calls_before = len(stubs)
    second = _build(tmp_path)
    assert second.cache_hit is True
    assert len(stubs) == calls_before
    assert second.resume_manifest["pdf_sha256"] == first.resume_manifest["pdf_sha256"]
    assert manifest.verify_pdf_hash(second.resume_manifest, second.resume_pdf)


def test_cache_key_changes_with_master_sha(tmp_path, stubs):
    _build(tmp_path)
    other = build.build_assets(JOB, JD, PROFILE, BANK, tmp_path / "art", cache_root=tmp_path / "cache",
                               master_md_sha="m2")
    assert other.cache_hit is False


def test_letter_builds_from_shared_evidence(tmp_path, stubs, monkeypatch):
    seen = {}

    def fake_letter(job, ev, artifacts_dir):
        seen.update(job=job, ev=ev, root=artifacts_dir)
        return SimpleNamespace(pdf_path="letter.pdf", review=False, review_reasons=[])

    monkeypatch.setattr(build, "_load_letter_builder", lambda: fake_letter)
    bundle = _build(tmp_path)
    assert bundle.letter.pdf_path == "letter.pdf" and bundle.letter_review_reasons == []
    assert seen["ev"]["requirements"] == REQS
    # R5: the letter builds into the attempt dir, never directly into the real artifacts root.
    assert seen["root"] != str(tmp_path / "art") and "resumev2-" in seen["root"]
    assert bundle.evidence["bullets"]


def test_letter_failure_never_fails_the_resume(tmp_path, stubs, monkeypatch):
    def boom(job, ev, artifacts_dir):
        raise RuntimeError("letter exploded")

    monkeypatch.setattr(build, "_load_letter_builder", lambda: boom)
    bundle = _build(tmp_path)
    assert Path(bundle.resume_pdf).is_file()
    assert bundle.letter is None and bundle.letter_review_reasons == ["letter_error:RuntimeError"]


def test_letter_disabled_by_config(tmp_path, stubs, monkeypatch):
    monkeypatch.setattr(build.config, "APPLY_COVER_LETTER", False)
    bundle = _build(tmp_path)
    assert bundle.letter is None and bundle.letter_review_reasons == ["letter_disabled"]


def test_validator_always_failing_never_yields_a_pdf(tmp_path, stubs, monkeypatch):
    bad = {**CONTENT, "summary": "A robust product manager focused on payments."}

    def fake_call(prompt, system, model, action, max_output_tokens=None):
        return json.dumps(REQS if action == "extract_requirements" else bad)

    monkeypatch.setattr(llm, "call_claude", fake_call)
    with pytest.raises(build.AssetBuildError) as exc:
        _build(tmp_path)
    assert exc.value.stage == "validate" and exc.value.violations
    assert list(tmp_path.rglob("*.pdf")) == []


def test_wm_failure_blocks_the_build(tmp_path, stubs, monkeypatch):
    def boom(texts, timeout_s=150):
        raise wm_stage.WmStageError("wm_unavailable")

    monkeypatch.setattr(wm_stage, "run_anti_watermark", boom)
    with pytest.raises(build.AssetBuildError) as exc:
        _build(tmp_path)
    assert exc.value.stage == "wm" and "wm_unavailable" in exc.value.reason
    assert list(tmp_path.rglob("*.pdf")) == []


def test_wm_that_breaks_validation_raises_wm_regate(tmp_path, stubs, monkeypatch):
    def dirty(texts, timeout_s=150):
        out = list(texts)
        out[2] = out[2].replace("40", "99")  # first bullet: an invented number
        return out, {"wm_applied": True}

    monkeypatch.setattr(wm_stage, "run_anti_watermark", dirty)
    with pytest.raises(build.AssetBuildError) as exc:
        _build(tmp_path)
    assert exc.value.stage == "wm_regate"
    assert list(tmp_path.rglob("*.pdf")) == []


def test_wm_output_is_what_gets_rendered(tmp_path, stubs, monkeypatch):
    def tweak(texts, timeout_s=150):
        out = list(texts)
        out[0] = "Product Manager, Payments"  # fact-preserving: no new seniority, numbers or roles
        return out, {"wm_applied": True}

    monkeypatch.setattr(wm_stage, "run_anti_watermark", tweak)
    bundle = _build(tmp_path)
    assert bundle.resume_content["headline"] == "Product Manager, Payments"


def test_wm_headline_with_invented_seniority_is_rejected(tmp_path, stubs, monkeypatch):
    def promote(texts, timeout_s=150):
        out = list(texts)
        out[0] = "Senior Product Manager"  # no source role says Senior
        return out, {"wm_applied": True}

    monkeypatch.setattr(wm_stage, "run_anti_watermark", promote)
    with pytest.raises(build.AssetBuildError) as exc:
        _build(tmp_path)
    assert exc.value.stage == "wm_regate" and exc.value.violations == ["headline_ungrounded"]
    assert list(tmp_path.rglob("*.pdf")) == []


@pytest.mark.parametrize("headline,role_title,expected", [
    ("Product Manager", "Product Manager", True),
    ("Product Manager, Payments", "Product Manager", True),
    ("Senior Product Manager", "Product Manager", False),
    ("Lead Product Manager", "Product Manager", False),
    ("Director of Product", "Product Manager", False),
    ("Senior Product Manager", "Senior Product Manager", False),  # target title is NOT a source
    ("Product Leadership", "Product Manager", True),  # "leadership" is not a seniority modifier
])
def test_headline_grounded(headline, role_title, expected):
    assert build._headline_grounded(headline, PROFILE, {"role_title": role_title}) is expected


def test_headline_grounded_accepts_a_seniority_word_from_a_source_role():
    profile = {"bullets": [{"role": "Acme: Senior Product Manager", "text": "x"}]}
    assert build._headline_grounded("Senior Product Manager", profile, {}) is True
    assert build._headline_grounded("Principal Product Manager", profile, {}) is False


def test_lease_renewal_failure_is_a_lease_error(tmp_path, stubs):
    renewals = []
    assert _build(tmp_path, renew_lease=lambda: renewals.append(1)).resume_pdf
    assert renewals

    def lost():
        raise RuntimeError("claim_lost")

    with pytest.raises(build.AssetBuildError) as exc:
        build.build_assets(JOB, JD, PROFILE, BANK, tmp_path / "art2", cache_root=tmp_path / "cache2",
                           renew_lease=lost)
    assert exc.value.stage == "lease"


def test_total_timeout_raises_timeout(tmp_path, stubs, monkeypatch):
    monkeypatch.setattr(build, "TOTAL_TIMEOUT", 0.3)
    monkeypatch.setattr(tailor_v2, "_generate", lambda reqs, ranked: time.sleep(1.5) or CONTENT)
    with pytest.raises(build.AssetBuildError) as exc:
        _build(tmp_path)
    assert exc.value.stage == "timeout"
    assert list(tmp_path.rglob("*.pdf")) == []


def test_tiny_total_timeout_raises_timeout_before_any_stage(tmp_path, stubs, monkeypatch):
    monkeypatch.setattr(build, "TOTAL_TIMEOUT", 0)
    with pytest.raises(build.AssetBuildError) as exc:
        _build(tmp_path)
    assert exc.value.stage == "timeout" and exc.value.reason == "total deadline of 0s exceeded before requirements"
    assert stubs == []  # no model call was made
    assert list(tmp_path.rglob("*.pdf")) == []


def test_deadline_expiring_after_render_discards_the_build(tmp_path, stubs, monkeypatch):
    real_render = build._render

    def render_then_expire(stages, content, profile, pdf_path):
        final = real_render(stages, content, profile, pdf_path)
        stages.deadline = time.monotonic() - 1  # the deadline passes while the PDF is being written
        return final

    monkeypatch.setattr(build, "_render", render_then_expire)
    with pytest.raises(build.AssetBuildError) as exc:
        _build(tmp_path)
    assert exc.value.stage == "timeout"
    assert list(tmp_path.rglob("*.pdf")) == []
    assert not (tmp_path / "art" / "job-1" / "resume" / "resume.manifest.json").exists()


def test_total_timeout_is_capped_at_360_seconds():
    assert build.TOTAL_TIMEOUT <= 360


def test_stage_exception_text_never_reaches_the_build_error(tmp_path, stubs, monkeypatch):
    def boom(reqs, ranked):
        raise ValueError("secret name 555-1234")

    monkeypatch.setattr(tailor_v2, "generate_content", boom)
    with pytest.raises(build.AssetBuildError) as exc:
        _build(tmp_path)
    err = exc.value
    assert err.stage == "generate" and "ValueError" in err.reason
    shown = str(err) + err.reason + repr(err)
    assert "secret" not in shown and "555-1234" not in shown and "1234" not in shown
    assert err.__cause__ is None
    assert list(tmp_path.rglob("*.pdf")) == []


def test_lease_error_does_not_chain_the_original(tmp_path, stubs):
    def lost():
        raise RuntimeError("claim 555-1234 lost")

    with pytest.raises(build.AssetBuildError) as exc:
        build.build_assets(JOB, JD, PROFILE, BANK, tmp_path / "art3", cache_root=tmp_path / "cache3",
                           renew_lease=lost)
    assert "555-1234" not in str(exc.value) and exc.value.__cause__ is None


def test_unsafe_job_id_is_rejected(tmp_path, stubs):
    with pytest.raises(build.AssetBuildError) as exc:
        build.build_assets({"id": "../x"}, JD, PROFILE, BANK, tmp_path)
    assert exc.value.stage == "input"
