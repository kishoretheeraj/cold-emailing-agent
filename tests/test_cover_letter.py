"""Cover letter pipeline: schema, grounding rules, repair, wm shim, deterministic PDF, review queue."""

import copy
import io
import json
import logging
import subprocess
from datetime import date

import pytest

import cover_letter
from cover_letter import content_gen, render_pdf, validate, wm
from cover_letter.content_gen import LetterGenError
from cover_letter.validate import validate_letter

FAKE_EMAIL = "fake.user@example.invalid"
FAKE_PHONE = "555-0142"

JD = ("Acme Robotics builds warehouse robots that move inventory for retailers. As a Product Manager you "
      "will own the product roadmap, work with engineering and operations, and communicate with "
      "stakeholders across customers and partners. You will use analytics to measure adoption of our "
      "fleet management software across many sites.")

JOB = {"id": 42, "company": "Acme Robotics", "role": "Product Manager", "url": "https://example.com/j/42"}


def make_evidence():
    return {
        "requirements": {"must_have": ["roadmap ownership"], "nice_to_have": ["analytics"],
                         "keywords": ["roadmap", "analytics", "SQL"],
                         "role_title": "Product Manager", "company": "Acme Robotics"},
        "bullets": [
            {"id": "b1", "role": "Globex Systems, Product Analyst", "tags": ["roadmap"],
             "text": "Led a roadmap for a billing tool used by 1,200 customers and cut invoice errors by 35%."},
            {"id": "b2", "role": "Globex Systems, Product Analyst", "tags": ["analytics"],
             "text": "Built dashboards in Tableau that gave 14 stakeholders weekly funnel metrics."},
            {"id": "b3", "role": "Globex Systems, Product Analyst", "tags": ["research"],
             "text": "Ran customer interviews with 25 users to prioritize onboarding fixes."},
        ],
        "bank_facts": {"full_name": "Test Person", "email": FAKE_EMAIL, "phone": FAKE_PHONE,
                       "linkedin": "https://linkedin.com/in/testperson", "location": "Austin, TX"},
        "jd_text": JD,
        "banned_words": ["leverage", "synergy"],
        "unknown_topics": ["security clearance"],
    }


def make_content():
    return {
        "schema_version": "letter.v1", "job_id": "42", "company": "Acme Robotics",
        "role": "Product Manager",
        "opening_hook": ("Acme Robotics builds warehouse robots for retailers, and your Product Manager role "
                         "centers on owning the product roadmap with engineering and operations."),
        "body": [
            {"text": ("At Globex Systems I led a roadmap for a billing tool used by 1,200 customers and cut "
                      "invoice errors by 35%."), "evidence_ids": ["b1"]},
            {"text": ("I also built dashboards in Tableau that gave 14 stakeholders weekly funnel metrics, "
                      "and I ran customer interviews with 25 users to prioritize onboarding fixes."),
             "evidence_ids": ["b2", "b3"]},
        ],
        "closing": ("I would welcome a conversation about how this work could support your team. "
                    "Thank you for your time."),
        "ungrounded_claims": [],
    }


@pytest.fixture
def evidence():
    return make_evidence()


def codes_of(violations):
    return {v.split(":")[0] for v in violations}


# ── Schema ─────────────────────────────────────────────────────────────────────

def test_valid_letter_passes(evidence):
    assert validate_letter(make_content(), evidence) == (True, [])


def _drop_evidence_ids(c):
    del c["body"][0]["evidence_ids"]


def _empty_evidence_ids(c):
    c["body"][0]["evidence_ids"] = []


def _four_paragraphs(c):
    c["body"] = c["body"] + copy.deepcopy(c["body"])


def _one_paragraph(c):
    c["body"] = c["body"][:1]


def _long_paragraph(c):
    c["body"][0]["text"] = "word " * 141


def _long_hook(c):
    c["opening_hook"] = "x" * 351


def _long_closing(c):
    c["closing"] = "x" * 301


def _missing_key(c):
    del c["closing"]


def _wrong_version(c):
    c["schema_version"] = "letter.v0"


def _claims_not_list(c):
    c["ungrounded_claims"] = "none"


@pytest.mark.parametrize("mutate", [
    _drop_evidence_ids, _empty_evidence_ids, _four_paragraphs, _one_paragraph, _long_paragraph,
    _long_hook, _long_closing, _missing_key, _wrong_version, _claims_not_list])
def test_schema_rejections(evidence, mutate):
    content = make_content()
    mutate(content)
    ok, violations = validate_letter(content, evidence)
    assert not ok
    assert "schema" in codes_of(violations)


@pytest.mark.parametrize("bad", [None, [], "text", 7])
def test_non_object_content_never_raises(evidence, bad):
    assert validate_letter(bad, evidence)[0] is False


def test_bad_evidence_shape_never_raises():
    assert validate_letter(make_content(), None)[0] in (True, False)
    assert validate_letter(make_content(), {"bullets": "oops", "bank_facts": 3})[0] in (True, False)


# ── Evidence ids ───────────────────────────────────────────────────────────────

def test_unknown_bullet_id_is_invalid(evidence):
    content = make_content()
    content["body"][0]["evidence_ids"] = ["zz"]
    ok, violations = validate_letter(content, evidence)
    assert not ok and "evidence_id_unresolved" in codes_of(violations)


def test_bank_id_resolves_and_unknown_bank_key_does_not(evidence):
    content = make_content()
    content["body"][1]["evidence_ids"] = ["b2", "b3", "bank:location"]
    assert validate_letter(content, evidence)[0]
    content["body"][1]["evidence_ids"] = ["b2", "b3", "bank:ssn"]
    ok, violations = validate_letter(content, evidence)
    assert not ok and "evidence_id_unresolved" in codes_of(violations)


# ── Grounding ──────────────────────────────────────────────────────────────────

def _set_body0(text):
    def mutate(c):
        c["body"][0]["text"] = text
    return mutate


def _append_body0(text):
    def mutate(c):
        c["body"][0]["text"] += " " + text
    return mutate


def _set_closing(text):
    def mutate(c):
        c["closing"] = text
    return mutate


CASES = [
    ("invented_metric", _set_body0("At Globex Systems I increased revenue 40% for a billing tool."), "number_ungrounded"),
    ("number_from_other_bullet", _set_body0("I built dashboards for 25 customers."), "number_ungrounded"),
    ("extra_company", _append_body0("I previously worked at Initech on pricing."), "unknown_proper_noun"),
    ("em_dash", _append_body0("I shipped fast \u2014 and carefully."), "dash_found"),
    ("en_dash", _append_body0("I shipped fast \u2013 and carefully."), "dash_found"),
    ("banned_word", _append_body0("I know how to leverage data."), "banned_word"),
    ("slop", _append_body0("Furthermore, I enjoy the work."), "slop_phrase"),
    ("salary", _set_closing("My salary expectation is flexible."), "auth_or_pay_wording"),
    ("visa", _set_closing("I will need visa support."), "auth_or_pay_wording"),
    ("sponsorship", _set_closing("I do not require sponsorship."), "auth_or_pay_wording"),
    ("work_auth", _set_closing("I hold work authorization."), "auth_or_pay_wording"),
    ("years", _append_body0("I bring 5 years of experience."), "years_not_in_sources"),
    ("years_word", _append_body0("I bring five years of experience."), "years_not_in_sources"),
    ("inline_url", _append_body0("See https://evil.example/portfolio for more."), "url_not_allowed"),
    ("company_claim", _append_body0("Acme Robotics is the market leader in drone delivery."),
     "company_claim_ungrounded"),
    ("unknown_topic", _append_body0("I hold a security clearance."), "unknown_topic"),
    ("degree", _append_body0("I earned an MBA."), "degree_not_in_sources"),
    ("title", _append_body0("I was a director of product."), "title_not_in_sources"),
    ("date", _append_body0("I joined in March."), "date_not_in_sources"),
    ("skill", _append_body0("I write SQL daily."), "skill_not_in_sources"),
    ("foreign_email", _append_body0("Write to someone@elsewhere.example."), "email_not_in_identity"),
]


@pytest.mark.parametrize("name,mutate,code", CASES, ids=[c[0] for c in CASES])
def test_banned_patterns(evidence, name, mutate, code):
    content = make_content()
    mutate(content)
    ok, violations = validate_letter(content, evidence)
    assert not ok, violations
    assert code in codes_of(violations)


def test_linkedin_url_is_allowed(evidence):
    content = make_content()
    content["closing"] = "You can find me at linkedin.com/in/testperson. Thank you for your time."
    assert validate_letter(content, evidence)[0]
    content["closing"] = "You can find me at https://www.linkedin.com/in/testperson/. Thank you."
    assert validate_letter(content, evidence)[0]


def test_years_phrase_allowed_when_in_a_source_bullet(evidence):
    evidence["bullets"][0]["text"] += " Over 5 years of experience in billing."
    content = make_content()
    content["body"][0]["text"] += " I bring 5 years of experience."
    assert validate_letter(content, evidence)[0]


def test_bank_only_paragraph_may_use_bank_numbers(evidence):
    content = make_content()
    content["body"].append({"text": "I am based in Austin and reachable at the number on this letter.",
                            "evidence_ids": ["bank:location"]})
    assert validate_letter(content, evidence)[0]


def test_ungrounded_claims_nonempty_is_invalid(evidence):
    content = make_content()
    content["ungrounded_claims"] = ["I admire the founder"]
    ok, violations = validate_letter(content, evidence)
    assert not ok and "ungrounded_claims_nonempty" in codes_of(violations)


# ── Fakes for build_letter ─────────────────────────────────────────────────────

def _identity_wm(texts, timeout_s=150):
    return list(texts), True


@pytest.fixture
def identity_wm(monkeypatch):
    monkeypatch.setattr(wm, "apply_wm", _identity_wm)


def _stub_gen(monkeypatch, outputs):
    calls = []

    def fake(job, evidence, timeout_s=90, violations=None, previous=None):
        calls.append({"violations": violations, "previous": previous})
        item = outputs[min(len(calls) - 1, len(outputs) - 1)]
        if isinstance(item, Exception):
            raise item
        return copy.deepcopy(item)

    monkeypatch.setattr(content_gen, "generate_letter", fake)
    return calls


# ── build_letter: success, repair, review ──────────────────────────────────────

def test_build_letter_success_writes_pdf_and_manifest(tmp_path, evidence, monkeypatch, identity_wm):
    _stub_gen(monkeypatch, [make_content()])
    result = cover_letter.build_letter(JOB, evidence, str(tmp_path))
    assert not result.review and result.validation_passed and result.violations == []
    out = tmp_path / "42" / "cover_letter"
    assert result.pdf_path == str(out / "Kishore_Theeraj_Vasudevan_Jaya_Cover_Letter.pdf")
    assert (out / "letter.content.json").exists() and (out / "letter.manifest.json").exists()
    assert not (out / "needs_review.json").exists()
    m = result.manifest
    assert m["template_version"] == "letter.v1" and m["tailored"] and m["wm_applied"]
    assert set(m) >= {"pdf_sha256", "content_sha256", "model_ids", "stage_timings"}
    assert json.loads((out / "letter.manifest.json").read_text()) == m


def test_repair_path_second_attempt_passes(tmp_path, evidence, monkeypatch, identity_wm):
    bad = make_content()
    bad["opening_hook"] += " It is a bold \u2014 move."
    calls = _stub_gen(monkeypatch, [bad, make_content()])
    result = cover_letter.build_letter(JOB, evidence, str(tmp_path))
    assert not result.review and len(calls) == 2
    assert calls[0]["violations"] is None
    assert calls[1]["violations"] and "dash_found" in codes_of(calls[1]["violations"])
    assert calls[1]["previous"]["opening_hook"] == bad["opening_hook"]


def test_repair_that_still_fails_goes_to_review(tmp_path, evidence, monkeypatch, identity_wm):
    bad = make_content()
    bad["body"][0]["text"] = "I increased revenue 40%."
    calls = _stub_gen(monkeypatch, [bad])
    result = cover_letter.build_letter(JOB, evidence, str(tmp_path))
    assert result.review and len(calls) == 2
    assert "number_ungrounded" in result.review_reasons and "validation_failed" in result.review_reasons
    assert result.pdf_path is None and result.manifest is None


def test_ungrounded_claims_go_to_review_without_pdf(tmp_path, evidence, monkeypatch, identity_wm):
    content = make_content()
    content["ungrounded_claims"] = ["I lead a team of 30 engineers"]
    calls = _stub_gen(monkeypatch, [content])
    result = cover_letter.build_letter(JOB, evidence, str(tmp_path))
    out = tmp_path / "42" / "cover_letter"
    assert result.review and result.pdf_path is None and len(calls) == 1
    assert "ungrounded_claims_nonempty" in result.review_reasons
    assert not list(out.glob("*.pdf"))
    review = json.loads((out / "needs_review.json").read_text())
    assert review["job_id"] == "42" and review["reason_codes"] == result.review_reasons
    assert review["ts"] and all(len(s["excerpt"]) <= 60 and len(s["sha256"]) == 64
                                for s in review["offending_spans"])


def test_short_jd_goes_to_review_without_calling_the_model(tmp_path, evidence, monkeypatch, identity_wm):
    evidence["jd_text"] = "Short."
    calls = _stub_gen(monkeypatch, [make_content()])
    result = cover_letter.build_letter(JOB, evidence, str(tmp_path))
    assert result.review and result.review_reasons == ["jd_too_short"] and calls == []


def test_generation_error_goes_to_review(tmp_path, evidence, monkeypatch, identity_wm):
    _stub_gen(monkeypatch, [LetterGenError("bad reply")])
    result = cover_letter.build_letter(JOB, evidence, str(tmp_path))
    assert result.review and result.review_reasons == ["gen_failed"]


def test_overall_timeout_goes_to_review(tmp_path, evidence, monkeypatch, identity_wm):
    _stub_gen(monkeypatch, [make_content()])
    result = cover_letter.build_letter(JOB, evidence, str(tmp_path), timeout_s=0)
    assert result.review and result.review_reasons == ["timeout"] and result.pdf_path is None


def test_missing_wm_command_goes_to_review(tmp_path, evidence, monkeypatch):
    monkeypatch.delenv(wm.DEWATERMARK_ENV, raising=False)
    monkeypatch.delenv(wm.PARAPHRASE_ENV, raising=False)
    _stub_gen(monkeypatch, [make_content()])
    result = cover_letter.build_letter(JOB, evidence, str(tmp_path))
    assert result.review and result.review_reasons == ["wm_unavailable"]
    assert not list((tmp_path / "42" / "cover_letter").glob("*.pdf"))


def test_wm_output_that_breaks_grounding_goes_to_review(tmp_path, evidence, monkeypatch):
    def bad_wm(texts, timeout_s=150):
        return [t + " I increased revenue 90%." for t in texts], True

    monkeypatch.setattr(wm, "apply_wm", bad_wm)
    _stub_gen(monkeypatch, [make_content()])
    result = cover_letter.build_letter(JOB, evidence, str(tmp_path))
    assert result.review and "wm_revalidation_failed" in result.review_reasons


def test_page_overflow_goes_to_review_and_removes_pdf(tmp_path, evidence, monkeypatch, identity_wm):
    monkeypatch.setattr(render_pdf, "render_letter_pdf", lambda c, b, p: (open(p, "wb").close() or 2))
    _stub_gen(monkeypatch, [make_content()])
    result = cover_letter.build_letter(JOB, evidence, str(tmp_path))
    assert result.review and result.review_reasons == ["page_count_not_one"]
    assert not list((tmp_path / "42" / "cover_letter").glob("*.pdf"))


def test_review_clears_stale_outputs(tmp_path, evidence, monkeypatch, identity_wm):
    out = tmp_path / "42" / "cover_letter"
    out.mkdir(parents=True)
    (out / "Kishore_Theeraj_Vasudevan_Jaya_Cover_Letter.pdf").write_bytes(b"old")
    bad = make_content()
    bad["ungrounded_claims"] = ["x"]
    _stub_gen(monkeypatch, [bad])
    cover_letter.build_letter(JOB, evidence, str(tmp_path))
    assert not list(out.glob("*.pdf"))


# ── No identity values in logs or review files ─────────────────────────────────

@pytest.mark.parametrize("failure", ["echoed_phone", "exception_message"])
def test_failing_build_leaks_no_identity_values(tmp_path, evidence, monkeypatch, caplog, identity_wm, failure):
    caplog.set_level(logging.DEBUG)
    if failure == "echoed_phone":
        bad = make_content()
        bad["opening_hook"] = f"Call me at {FAKE_PHONE} or write to {FAKE_EMAIL} about the role."
        _stub_gen(monkeypatch, [bad])
    else:
        _stub_gen(monkeypatch, [RuntimeError(f"boom {FAKE_EMAIL} {FAKE_PHONE}")])
    result = cover_letter.build_letter(JOB, evidence, str(tmp_path))
    assert result.review
    review_text = (tmp_path / "42" / "cover_letter" / "needs_review.json").read_text()
    for text in (caplog.text, review_text):
        assert FAKE_EMAIL not in text and FAKE_PHONE not in text
        assert "5550142" not in text.replace("-", "")


# ── wm shim ────────────────────────────────────────────────────────────────────

def _fake_run(calls, fail_first=0, always_fail=False, timeout=False, bad_shape=False):
    state = {"n": 0}

    def run(argv, input=None, **kwargs):
        calls.append(argv[0])
        state["n"] += 1
        if timeout:
            raise subprocess.TimeoutExpired(argv, 1)
        if always_fail or state["n"] <= fail_first:
            return subprocess.CompletedProcess(argv, 1, stdout="", stderr="")
        texts = json.loads(input)["texts"]
        if bad_shape:
            return subprocess.CompletedProcess(argv, 0, stdout=json.dumps({"texts": texts[:-1]}), stderr="")
        mark = "~" if argv[0] == "dewm" else "+"
        return subprocess.CompletedProcess(
            argv, 0, stdout=json.dumps({"texts": [t + mark for t in texts]}), stderr="")

    return run


@pytest.fixture
def wm_env(monkeypatch):
    monkeypatch.setenv(wm.DEWATERMARK_ENV, "dewm --clean")
    monkeypatch.setenv(wm.PARAPHRASE_ENV, "para --rewrite")


def test_wm_applies_three_steps_in_order(monkeypatch, wm_env):
    calls = []
    monkeypatch.setattr(wm.subprocess, "run", _fake_run(calls))
    texts, applied = wm.apply_wm(["one", "two"])
    assert applied is True
    assert calls == ["dewm", "para", "dewm"]
    assert texts == ["one~+~", "two~+~"]


@pytest.mark.parametrize("unset", [wm.DEWATERMARK_ENV, wm.PARAPHRASE_ENV])
def test_wm_missing_command_raises_unavailable(monkeypatch, wm_env, unset):
    monkeypatch.delenv(unset)
    calls = []
    monkeypatch.setattr(wm.subprocess, "run", _fake_run(calls))
    with pytest.raises(wm.WmError) as err:
        wm.apply_wm(["one"])
    assert err.value.code == "wm_unavailable" and calls == []


def test_wm_empty_command_raises_unavailable(monkeypatch, wm_env):
    monkeypatch.setenv(wm.PARAPHRASE_ENV, "   ")
    with pytest.raises(wm.WmError):
        wm.apply_wm(["one"])


def test_wm_retries_once_then_succeeds(monkeypatch, wm_env):
    calls = []
    monkeypatch.setattr(wm.subprocess, "run", _fake_run(calls, fail_first=1))
    texts, applied = wm.apply_wm(["one"])
    assert applied and texts == ["one~+~"] and len(calls) == 4


def test_wm_failing_twice_raises(monkeypatch, wm_env):
    calls = []
    monkeypatch.setattr(wm.subprocess, "run", _fake_run(calls, always_fail=True))
    with pytest.raises(wm.WmError):
        wm.apply_wm(["one"])
    assert len(calls) == 2


def test_wm_timeout_raises_after_retry(monkeypatch, wm_env):
    calls = []
    monkeypatch.setattr(wm.subprocess, "run", _fake_run(calls, timeout=True))
    with pytest.raises(wm.WmError):
        wm.apply_wm(["one"])
    assert len(calls) == 2


def test_wm_bad_output_shape_raises(monkeypatch, wm_env):
    monkeypatch.setattr(wm.subprocess, "run", _fake_run([], bad_shape=True))
    with pytest.raises(wm.WmError):
        wm.apply_wm(["one", "two"])


# ── PDF ────────────────────────────────────────────────────────────────────────

def _render(tmp_path, name, day=date(2026, 10, 12)):
    path = tmp_path / name
    pages = render_pdf.render_letter_pdf(make_content(), make_evidence()["bank_facts"], str(path), today=day)
    return path, pages


def test_pdf_is_byte_identical_for_same_input(tmp_path):
    a, pages_a = _render(tmp_path, "a.pdf")
    b, pages_b = _render(tmp_path, "b.pdf")
    assert pages_a == pages_b == 1
    assert a.read_bytes() == b.read_bytes()


def test_pdf_text_has_name_date_and_body(tmp_path):
    from pypdf import PdfReader

    path, pages = _render(tmp_path, "c.pdf")
    assert pages == 1
    reader = PdfReader(io.BytesIO(path.read_bytes()))
    text = " ".join(" ".join(p.extract_text().split()) for p in reader.pages)
    assert len(reader.pages) == 1
    assert "Test Person" in text and "October 12, 2026" in text
    assert "Dear Hiring Manager," in text and "billing tool used by 1,200 customers" in text
    assert "Sincerely," in text and FAKE_EMAIL in text


def test_pdf_default_date_is_not_taken_from_the_model(tmp_path):
    content = make_content()
    content["opening_hook"] = "Acme Robotics builds warehouse robots for retailers."
    path = tmp_path / "d.pdf"
    assert render_pdf.render_letter_pdf(content, make_evidence()["bank_facts"], str(path)) == 1
