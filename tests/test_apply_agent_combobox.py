"""Searchable dropdowns (react-select style comboboxes), standard questions answered from
master.json, and Lever's custom-question labels -- against a real browser and
tests/fixtures/searchable_selects.html / lever_cards.html. The 2026-10-07 live preview left
Country, Location (City), School, Degree, Discipline and the work-authorization/sponsorship
dropdowns empty on Stripe, AXS, nCino and Beyond Finance, and read unitQ's questions as
cards[<uuid>][field0]."""

import json
import os
from pathlib import Path

import pytest

import apply_agent
import candidate_profile

FIXTURES = Path(__file__).resolve().parent / "fixtures"
SELECTS = (FIXTURES / "searchable_selects.html").as_uri()
LEVER = (FIXTURES / "lever_cards.html").as_uri()

FACTS = {
    "city": ["Hanover, NH", "Hanover, New Hampshire", "Hanover, New Hampshire, United States"],
    "country": ["United States", "United States of America", "USA"],
    "school": ["Dartmouth College"],
    "degree": ["Master of Engineering Management", "Master's Degree", "Master's", "Masters"],
    "discipline": ["Engineering Management", "Engineering"],
    "end_year": ["2026"],
}


@pytest.fixture(autouse=True)
def _short_field_timeout(monkeypatch):
    # The fixture's menus answer within 300 ms; a 1 s wait keeps the "no option" paths quick.
    monkeypatch.setattr(apply_agent.config, "APPLY_AGENT_FIELD_TIMEOUT_MS", 1000)


@pytest.fixture(scope="module")
def browser():
    sync_api = pytest.importorskip("playwright.sync_api")
    executable = "/opt/pw-browsers/chromium" if os.path.exists("/opt/pw-browsers/chromium") else None
    with sync_api.sync_playwright() as p:
        try:
            b = p.chromium.launch(executable_path=executable)
        except Exception as exc:
            pytest.skip(f"no launchable Chromium here: {exc}")
        yield b
        b.close()


@pytest.fixture
def page(browser):
    context = browser.new_context()
    p = context.new_page()
    p.goto(SELECTS)
    yield p
    context.close()


@pytest.fixture
def lever(browser):
    context = browser.new_context()
    p = context.new_page()
    p.goto(LEVER)
    yield p
    context.close()


def _field(page, label):
    return next(f for f in apply_agent._form_inventory(page) if f["label"] == label)


def _filled(page, label):
    return _field(page, label)["filled"]


# ── Filling a searchable dropdown ─────────────────────────────────────────────

def test_inventory_reads_searchable_dropdowns_as_required_empty_comboboxes(page):
    country = _field(page, "Country")
    assert country["kind"] == "combobox"
    assert country["required"] is True
    assert country["filled"] is False


def test_yes_no_answer_is_chosen_by_clicking_the_option_not_by_enter(page):
    # Regression: typing "Yes" + Enter fired before the menu rendered and chose nothing, while the
    # filler reported success. The menu here renders 150 ms late, like the live one.
    field = _field(page, "Do you require visa sponsorship, now or in the future, to continue working in the United States?")
    assert apply_agent._fill_field(page, field, "Yes") is True
    assert page.locator("#question_sponsor").locator("xpath=ancestor::div[contains(@class,'select__container')]") \
        .locator(".select__single-value").inner_text() == "Yes"
    assert _filled(page, field["label"]) is True


def test_no_matching_option_leaves_the_dropdown_empty_and_reports_false(page):
    field = _field(page, "What is your first location preference?")
    assert apply_agent._fill_field(page, field, "Austin") is False
    assert _filled(page, field["label"]) is False
    assert page.locator("#question_pref").input_value() == ""


def test_decline_answer_maps_to_the_forms_decline_wording(page):
    field = _field(page, "Veteran Status")
    assert apply_agent._fill_field(page, field, "decline to self-identify") is True
    assert _filled(page, "Veteran Status") is True


def test_fill_never_submits_the_form(page):
    for label in ("Country", "Do you require visa sponsorship, now or in the future, to continue working in the United States?"):
        apply_agent._fill_field(page, _field(page, label), "Yes")
    assert page.evaluate("window.SUBMITS || 0") == 0


# ── Standard questions from master.json ───────────────────────────────────────

def test_profile_answer_picks_the_country_not_the_outlying_islands(page):
    answer = apply_agent._profile_answer(page, _field(page, "Country"), FACTS["country"])
    assert answer == "United States +1"
    assert _filled(page, "Country") is False  # reading options never chooses one


def test_profile_answer_searches_an_async_city_list_and_picks_the_right_state(page):
    answer = apply_agent._profile_answer(page, _field(page, "Location (City)"), FACTS["city"])
    assert answer == "Hanover, New Hampshire, United States"
    assert page.locator("#candidate-location").input_value() == ""


def test_profile_answer_school_degree_and_discipline(page):
    assert apply_agent._profile_answer(page, _field(page, "School"), FACTS["school"]) == "Dartmouth College"
    assert apply_agent._profile_answer(page, _field(page, "Degree"), FACTS["degree"]) == "Master's Degree"
    # "Engineering" must be the exact option, never "Engineering Physics".
    assert apply_agent._profile_answer(page, _field(page, "Discipline"), FACTS["discipline"]) == "Engineering"


def test_profile_answer_for_a_text_box_is_the_first_candidate(page):
    field = _field(page, "If located in the US, in what city and state do you reside?")
    assert apply_agent._profile_answer(page, field, FACTS["city"]) == "Hanover, NH"


def test_stored_profile_answer_replays_into_the_async_dropdown(page):
    # submit() replays the reviewed text through _fill_screening_questions.
    report = apply_agent._fill_screening_questions(page, {"Location (City)": "Hanover, New Hampshire, United States",
                                                         "School": "Dartmouth College"})
    assert report == {"Location (City)": True, "School": True}
    assert _filled(page, "Location (City)") and _filled(page, "School")


def test_generate_answers_uses_master_json_for_standard_questions_and_the_model_for_the_rest(page, mocker):
    mocker.patch.object(apply_agent.candidate_profile, "form_answers", return_value=FACTS)
    mocker.patch.object(apply_agent.candidate_profile, "profile_text", return_value="PM at Example Co.")
    mocker.patch.object(apply_agent, "_eligibility_answers",
                        return_value={"work_authorized_us": "Yes", "requires_visa_sponsorship": "Yes"})
    mocker.patch.object(apply_agent, "_answer_bank", return_value={"school": "Some Other University"})
    prompts = []

    def fake_completion(prompt, job_id):
        prompts.append(prompt)
        if "location preference" in prompt and "San Francisco" in prompt:
            # Batched in form order: [0] Start date year, [1] first location preference.
            return json.dumps([{"id": 0, "answer": "NEEDS HUMAN REVIEW"}, {"id": 1, "answer": "San Francisco"}])
        return "NEEDS HUMAN REVIEW"

    mocker.patch.object(apply_agent, "_screening_completion", side_effect=fake_completion)
    answers = apply_agent._generate_screening_answers(page, {"id": 1})

    assert answers["Country"] == "United States +1"
    assert answers["Location (City)"] == "Hanover, New Hampshire, United States"
    assert answers["School"] == "Dartmouth College"           # master.json, not the answer bank
    assert answers["Degree"] == "Master's Degree"
    assert answers["Discipline"] == "Engineering"
    assert answers["If located in the US, in what city and state do you reside?"] == "Hanover, NH"
    assert answers["What is your first location preference?"] == "San Francisco"
    assert answers["Start date year"].startswith("NEEDS HUMAN REVIEW")  # not on file: a human answers
    # Eligibility questions are left to _fill_eligibility_answers, optional ones to nobody.
    assert not any("eligible to work" in label or "sponsorship" in label for label in answers)
    assert "Veteran Status" not in answers
    assert not any(label.startswith("If you do not see your University") for label in answers)
    # Standard facts never reach the model; the dropdown's real choices do.
    joined = "\n".join(prompts)
    assert "Dartmouth" not in joined and "Country" not in joined
    assert "New York | San Francisco | Seattle" in joined


def test_preview_sequence_fills_every_answerable_required_question(page, mocker):
    mocker.patch.object(apply_agent.candidate_profile, "form_answers", return_value=FACTS)
    mocker.patch.object(apply_agent.candidate_profile, "profile_text", return_value="PM at Example Co.")
    eligibility = {"work_authorized_us": "Yes", "requires_visa_sponsorship": "Yes",
                   "veteran_status": "decline to self-identify"}
    mocker.patch.object(apply_agent, "_eligibility_answers", return_value=eligibility)
    mocker.patch.object(apply_agent, "_screening_completion", return_value=json.dumps(
        [{"id": 0, "answer": "NEEDS HUMAN REVIEW"}, {"id": 1, "answer": "Seattle"}]))

    eligibility_report = apply_agent._fill_eligibility_answers(page, eligibility)
    answers = apply_agent._generate_screening_answers(page, {"id": 1})
    apply_agent._fill_screening_questions(page, answers)

    assert eligibility_report == {
        "Are you currently eligible to work in the United States?": True,
        "Do you require visa sponsorship, now or in the future, to continue working in the United States?": True,
        "Veteran Status": True,
    }
    # Only the fact nobody has on file is left for the human.
    assert apply_agent._required_unfilled(apply_agent._form_inventory(page)) == ["Start date year"]
    assert page.locator("#other_school").input_value() == ""
    assert page.evaluate("window.SUBMITS || 0") == 0


def test_dryrun_profile_answers_never_call_a_model(page, mocker):
    mocker.patch.object(apply_agent.candidate_profile, "form_answers", return_value=FACTS)
    mocker.patch.object(apply_agent, "_eligibility_answers", return_value={})
    model = mocker.patch.object(apply_agent, "_screening_completion")
    answers = apply_agent._profile_answers(page)
    assert answers["Country"] == "United States +1"
    assert "What is your first location preference?" not in answers
    model.assert_not_called()


# ── Lever custom questions ────────────────────────────────────────────────────

def test_lever_custom_questions_are_read_from_their_card_label(lever):
    inventory = apply_agent._form_inventory(lever)
    labels = [f["label"] for f in inventory]
    assert "Why do you want to work at unitQ?" in labels
    assert "Are you legally authorized to work in the United States?" in labels
    assert "Which city are you based in?" in labels
    assert not any(label.startswith("cards[") for label in labels)
    assert "Full name" in labels  # U+2731 stripped
    why = next(f for f in inventory if f["label"] == "Why do you want to work at unitQ?")
    auth = next(f for f in inventory if f["label"] == "Are you legally authorized to work in the United States?")
    city = next(f for f in inventory if f["label"] == "Which city are you based in?")
    assert why["required"] and auth["required"] and not city["required"]
    assert auth["kind"] == "radio" and auth["options"] == ["Yes", "No"]


def test_lever_eligibility_radio_is_answered_by_its_question(lever):
    report = apply_agent._fill_eligibility_answers(lever, {"work_authorized_us": "Yes"})
    assert report == {"Are you legally authorized to work in the United States?": True}
    assert lever.locator("input[value='Yes']").is_checked()


# ── Pure helpers ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("options,candidates,expected", [
    (["United States Minor Outlying Islands +1", "United States +1"], FACTS["country"], "United States +1"),
    (["Canada", "United States"], FACTS["country"], "United States"),
    (["Engineering Physics", "Engineering"], FACTS["discipline"], "Engineering"),
    (["Engineering Physics"], FACTS["discipline"], None),
    (["Hanover, Pennsylvania, United States", "Hanover, New Hampshire, United States"], FACTS["city"],
     "Hanover, New Hampshire, United States"),
    (["Hanover, NH, USA"], FACTS["city"], "Hanover, NH, USA"),
    (["Bachelor's Degree", "Master's Degree"], FACTS["degree"], "Master's Degree"),
    ([], FACTS["degree"], None),
])
def test_pick_candidates(options, candidates, expected):
    assert apply_agent._pick_candidates(options, candidates) == expected


@pytest.mark.parametrize("label,key", [
    ("Country", "country"),
    ("Please select the country where you currently reside.", "country"),
    ("Location (City)", "city"),
    ("If located in the US, in what city and state do you reside?", "city"),
    ("School", "school"),
    ("School (Undergraduate)", "school_undergraduate"),
    ("School (Graduate)", "school_graduate"),
    ("Degree", "degree"),
    ("Discipline", "discipline"),
    ("End date year", "end_year"),
    ("Start date year", None),
    ("GPA (Undergraduate)", None),
    ("If you do not see your University listed, let us know your school name here.", None),
    ("Are you willing to relocate to our New York office?", None),
    ("What country are you a citizen of?", None),
])
def test_profile_key_for(label, key):
    assert apply_agent._profile_key_for(label) == key


@pytest.mark.parametrize("label,matches", [
    ("Are you currently eligible to work in the United States?", True),
    ("Are you legally authorized to work in the United States?", True),
    ("What is your work authorization status?", False),
    ("Are you currently a F-1 visa holder on Optional Practical Training (OPT)?", False),
])
def test_work_authorization_pattern_is_yes_no_wording_only(label, matches):
    pattern = apply_agent._ELIGIBILITY_QUESTION_PATTERNS["work_authorized_us"]
    assert bool(pattern.search(label)) is matches


def test_form_answers_derive_from_master_json(tmp_path, monkeypatch):
    master = {
        "contact": {"location": "Hanover, NH"},
        "education": [
            {"institution": "Dartmouth College", "graduation": "Expected Nov 2026",
             "program": "Master of Engineering Management (MEM) | Joint program by Thayer and Tuck"},
            {"institution": "Anna University", "graduation": "Apr 2022",
             "program": "Bachelor of Engineering, Computer Science and Engineering"},
        ],
    }
    path = tmp_path / "master.json"
    path.write_text(json.dumps(master))
    monkeypatch.setattr(candidate_profile, "_MASTER_DATA_PATH", path)
    answers = candidate_profile.form_answers()
    assert answers["city"][:3] == ["Hanover, NH", "Hanover, New Hampshire", "Hanover, New Hampshire, United States"]
    assert answers["country"][0] == "United States"
    assert answers["school"] == ["Dartmouth College"]
    assert answers["degree"][:2] == ["Master of Engineering Management", "Master's Degree"]
    assert answers["discipline"] == ["Engineering Management", "Engineering"]
    assert answers["end_year"] == ["2026"]
    assert answers["school_graduate"] == ["Dartmouth College"]
    assert answers["school_undergraduate"] == ["Anna University"]
    assert "start_year" not in answers and "gpa" not in answers


def test_form_answers_outside_the_us_has_no_country_and_unreadable_file_is_empty(tmp_path, monkeypatch):
    path = tmp_path / "master.json"
    path.write_text(json.dumps({"contact": {"location": "Chennai, India"}}))
    monkeypatch.setattr(candidate_profile, "_MASTER_DATA_PATH", path)
    assert candidate_profile.form_answers() == {"city": ["Chennai, India"]}
    monkeypatch.setattr(candidate_profile, "_MASTER_DATA_PATH", tmp_path / "missing.json")
    assert candidate_profile.form_answers() == {}
