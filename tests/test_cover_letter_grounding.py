"""Claim-level grounding for letter.v1: a body sentence must restate one of its cited evidence items."""

import copy

import pytest

from cover_letter.validate import validate_letter

JD = ("Acme Robotics builds warehouse robots that move inventory for retailers. As a Product Manager you "
      "will own the product roadmap, work with engineering and operations, and communicate with "
      "stakeholders across customers and partners. You will use analytics to measure adoption of our "
      "fleet management software across many sites.")


def _evidence():
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
        "bank_facts": {"full_name": "Test Person", "email": "fake.user@example.invalid", "phone": "555-0142",
                       "linkedin": "https://linkedin.com/in/testperson", "location": "Austin, TX"},
        "jd_text": JD,
        "banned_words": ["leverage", "synergy"],
        "unknown_topics": ["security clearance"],
    }


def _content():
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


def _codes(violations):
    return {v.split(":")[0] for v in violations}


def test_baseline_letter_is_valid():
    assert validate_letter(_content(), _evidence()) == (True, [])


def test_invented_claim_with_unrelated_bullets_is_ungrounded():
    content = _content()
    content["body"][0]["text"] = "I led a global engineering organization and doubled revenue."
    ok, violations = validate_letter(content, _evidence())
    assert not ok and "claim_ungrounded" in _codes(violations)


def test_faithful_paraphrase_of_a_cited_bullet_is_valid():
    content = _content()
    content["body"][0]["text"] = ("At Globex Systems I led the roadmap for a billing tool that 1,200 customers "
                                  "used, and I cut invoice errors by 35%.")
    assert validate_letter(content, _evidence()) == (True, [])


def test_invented_sentence_added_to_a_grounded_paragraph_is_caught():
    content = _content()
    content["body"][0]["text"] += " I managed a team of engineers across several continents."
    ok, violations = validate_letter(content, _evidence())
    assert not ok and "claim_ungrounded" in _codes(violations)


def test_claim_must_match_a_cited_bullet_not_just_any_bullet():
    content = _content()
    content["body"][0]["evidence_ids"] = ["b3"]  # the billing sentence now cites the interviews bullet
    ok, violations = validate_letter(content, _evidence())
    assert not ok and "claim_ungrounded" in _codes(violations)


def test_bank_only_paragraph_is_exempt_from_the_overlap_check():
    content = _content()
    content["body"].append({"text": "I am based in Austin and reachable at the number on this letter.",
                            "evidence_ids": ["bank:location"]})
    assert validate_letter(content, _evidence()) == (True, [])


def test_bank_only_paragraph_is_still_subject_to_the_number_rule():
    content = _content()
    content["body"].append({"text": "I am based in Austin and have 99 teammates there.",
                            "evidence_ids": ["bank:location"]})
    ok, violations = validate_letter(content, _evidence())
    assert not ok and "number_ungrounded" in _codes(violations)
    assert "claim_ungrounded" not in _codes(violations)


@pytest.mark.parametrize("bad_ids", [["zz"], ["bank:ssn"]])
def test_unresolved_ids_report_only_the_id_problem(bad_ids):
    content = copy.deepcopy(_content())
    content["body"][0]["evidence_ids"] = bad_ids
    ok, violations = validate_letter(content, _evidence())
    assert not ok and "evidence_id_unresolved" in _codes(violations)
    assert "claim_ungrounded" not in _codes(violations)
