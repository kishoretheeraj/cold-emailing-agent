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
import job_filters

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
    "gpa_undergraduate": ["3.73/4.0 (8.75/10)", "3.73"],
    "gpa_graduate": ["N/A"],
}
RANKING = job_filters.DEFAULT_PREFERENCES["preferred_locations"]


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


@pytest.mark.parametrize("markup", [
    '<input id="city" class="form-control" role="combobox" required>',
    '<div class="form-control"><input id="city" role="combobox" required></div>',
])
def test_plain_autocomplete_with_generic_control_class_counts_its_text(page, markup):
    page.set_content('<label for="city">City</label>' + markup)
    box = page.locator("#city")
    assert not box.evaluate(apply_agent._COMBOBOX_FILLED_JS)
    assert not _filled(page, "City")
    box.fill("Boston")
    assert box.evaluate(apply_agent._COMBOBOX_FILLED_JS)
    assert _filled(page, "City")
    assert apply_agent._required_unfilled(apply_agent._form_inventory(page)) == []


def test_default_react_select_control_does_not_count_search_text(page):
    page.set_content('<label for="react-select-1-input">City</label>'
                     '<div class="css-default-control">'
                     '<input id="react-select-1-input" role="combobox" required>'
                     '</div>')
    box = page.locator("input")
    box.fill("Boston")
    assert not box.evaluate(apply_agent._COMBOBOX_FILLED_JS)
    assert not _filled(page, "City")
    page.locator("div").evaluate(
        "el => el.insertAdjacentHTML('afterbegin', '<div class=css-default-singleValue>Boston</div>')")
    box.fill("")
    assert box.evaluate(apply_agent._COMBOBOX_FILLED_JS)
    assert _filled(page, "City")


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
    mocker.patch.object(apply_agent.candidate_profile, "form_answers", return_value={**FACTS, "start_year": ["2025"]})
    mocker.patch.object(apply_agent.candidate_profile, "profile_text", return_value="PM at Example Co.")
    mocker.patch.object(apply_agent, "_eligibility_answers",
                        return_value={"work_authorized_us": "Yes", "requires_visa_sponsorship": "Yes"})
    mocker.patch.object(apply_agent, "_answer_bank", return_value={"school": "Some Other University"})
    mocker.patch.object(apply_agent, "_preferred_locations", return_value=RANKING)
    prompts = []

    def fake_completion(prompt, job_id):
        prompts.append(prompt)
        return "I like the product."

    mocker.patch.object(apply_agent, "_screening_completion", side_effect=fake_completion)
    answers = apply_agent._generate_screening_answers(page, {"id": 1})

    assert answers["Country"] == "United States +1"
    assert answers["Location (City)"] == "Hanover, New Hampshire, United States"
    assert answers["School"] == "Dartmouth College"           # master.json, not the answer bank
    assert answers["Degree"] == "Master's Degree"
    assert answers["Discipline"] == "Engineering"
    assert answers["If located in the US, in what city and state do you reside?"] == "Hanover, NH"
    assert answers["Start date year"] == "2025"
    assert answers["GPA (Undergraduate)"] == "3.73"           # a number box takes the bare number
    # Ranked cities: Seattle first; San Diego isn't offered, so the second pick is San Francisco.
    assert answers["What is your first location preference?"] == "Seattle"
    assert answers["Second location preference"] == "San Francisco"
    assert answers["Why do you want to work here?"] == "I like the product."
    # Eligibility questions are left to _fill_eligibility_answers, optional ones to nobody.
    assert not any("eligible to work" in label or "sponsorship" in label for label in answers)
    assert "Veteran Status" not in answers
    assert not any(label.startswith("If you do not see your University") for label in answers)
    # Only the open question reaches the model; standard facts and city choices never do.
    joined = "\n".join(prompts)
    assert "Why do you want to work here?" in joined
    assert "Dartmouth" not in joined and "Country" not in joined and "location preference" not in joined


def test_preview_sequence_fills_every_answerable_required_question(page, mocker):
    mocker.patch.object(apply_agent.candidate_profile, "form_answers", return_value=FACTS)
    mocker.patch.object(apply_agent.candidate_profile, "profile_text", return_value="PM at Example Co.")
    eligibility = {"work_authorized_us": "Yes", "requires_visa_sponsorship": "Yes",
                   "veteran_status": "decline to self-identify"}
    mocker.patch.object(apply_agent, "_eligibility_answers", return_value=eligibility)
    mocker.patch.object(apply_agent, "_preferred_locations", return_value=RANKING)
    mocker.patch.object(apply_agent, "_screening_completion", return_value="I like the product.")

    eligibility_report = apply_agent._fill_eligibility_answers(page, eligibility)
    answers = apply_agent._generate_screening_answers(page, {"id": 1})
    report = apply_agent._fill_screening_questions(page, answers)

    assert eligibility_report == {
        "Are you currently eligible to work in the United States?": True,
        "Do you require visa sponsorship, now or in the future, to continue working in the United States?": True,
        "Veteran Status": True,
    }
    assert all(report.values()), report
    # FACTS has no start year: the one fact not on file is left for the human.
    assert apply_agent._required_unfilled(apply_agent._form_inventory(page)) == ["Start date year"]
    assert page.locator("#gpa_number").input_value() == "3.73"
    assert page.locator("#other_school").input_value() == ""
    assert page.evaluate("window.SUBMITS || 0") == 0


def test_dryrun_profile_answers_never_call_a_model(page, mocker):
    mocker.patch.object(apply_agent.candidate_profile, "form_answers", return_value=FACTS)
    mocker.patch.object(apply_agent, "_eligibility_answers", return_value={})
    mocker.patch.object(apply_agent, "_preferred_locations", return_value=RANKING)
    model = mocker.patch.object(apply_agent, "_screening_completion")
    answers = apply_agent._profile_answers(page)
    assert answers["Country"] == "United States +1"
    assert answers["What is your first location preference?"] == "Seattle"
    assert "Why do you want to work here?" not in answers
    model.assert_not_called()


@pytest.mark.parametrize("options,expected", [
    (["New York, NY", "San Francisco, CA", "Seattle, WA", "Chicago, IL"],
     ["Seattle, WA", "San Francisco, CA", "New York, NY", "Chicago, IL"]),
    (["Remote", "Dublin, Ireland"], []),
    (["South San Francisco, CA", "Washington, DC", "Austin, TX (Hybrid)"],
     ["South San Francisco, CA", "Washington, DC", "Austin, TX (Hybrid)"]),
    (["NYC", "Boston - MA"], ["Boston - MA", "NYC"]),
])
def test_ranked_locations(options, expected):
    assert apply_agent._ranked_locations(options, RANKING) == expected


@pytest.mark.parametrize("label,rank", [
    ("What is your first location preference?", 1),
    ("Second location preference", 2),
    ("Third location preference", 3),
    ("Location preference #2", 2),
    ("Location (City)", None),
    ("Do you have a location preference?", None),
])
def test_location_rank(label, rank):
    assert apply_agent._location_rank(label) == rank


def test_location_preference_with_no_ranked_city_offered_stays_blank():
    field = {"kind": "select", "options": ["Remote", "London"]}
    assert apply_agent._location_preference_answer(None, field, 1, RANKING) is None


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
    (["United States Minor Outlying Islands +1", "Canada"], FACTS["country"], None),
    (["United States (+1)"], FACTS["country"], "United States (+1)"),
    (["Dartmouth College of Medicine"], ["Dartmouth College"], None),
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


def test_profile_combobox_with_one_candidate_does_not_use_fuzzy_answer_matching(page):
    field = _field(page, "School")
    # A partial name is not an explicit alias for the fixture's Dartmouth College option.
    assert apply_agent._profile_answer(page, field, ["Dartmouth"]) is None


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
    ("Start date year", "start_year"),
    ("GPA (Undergraduate)", "gpa_undergraduate"),
    ("GPA (Graduate)", "gpa_graduate"),
    ("Undergraduate GPA", "gpa_undergraduate"),
    ("GPA", "gpa"),
    ("Similarly, we also invite you to provide your GPA, this can help us further understand your academic experience.", None),
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


def test_form_answers_start_year_and_gpa(tmp_path, monkeypatch):
    master = {"education": [
        {"institution": "Dartmouth College", "start": "Sep 2025", "graduation": "Expected Nov 2026", "gpa": None,
         "program": "Master of Engineering Management (MEM)"},
        {"institution": "Anna University", "graduation": "Apr 2022", "gpa": "8.75/10", "gpa_us": "3.73/4.0",
         "program": "Bachelor of Engineering, Computer Science and Engineering"},
    ]}
    path = tmp_path / "master.json"
    path.write_text(json.dumps(master))
    monkeypatch.setattr(candidate_profile, "_MASTER_DATA_PATH", path)
    answers = candidate_profile.form_answers()
    assert answers["start_year"] == ["2025"]
    assert answers["gpa_graduate"] == ["N/A"]                 # Thayer issues no GPA: never a number
    assert answers["gpa_undergraduate"] == ["3.73/4.0 (8.75/10)", "3.73"]
    assert answers["gpa"] == ["3.73/4.0 (8.75/10) (undergraduate)", "3.73"]


@pytest.mark.parametrize("options,expected", [
    (["Below 3.0", "3.0 - 3.49", "3.50 - 3.74", "3.75 - 4.0"], "3.50 - 3.74"),
    (["2.5+", "3.0+", "3.5+", "3.8+"], "3.5+"),
    (["3.0 or above", "3.5 or above"], "3.5 or above"),
    (["3.75 - 4.0", "N/A"], None),                    # never rounds up
    (["Yes", "No"], None),
])
def test_gpa_option_picks_the_range_holding_the_number(options, expected):
    assert apply_agent._gpa_option(options, "3.73") == expected


@pytest.mark.parametrize("value,options,expected", [
    ("Yes", ["I agree", "I do not agree"], "I agree"),
    ("Yes", ["I acknowledge", "I don't acknowledge"], "I acknowledge"),
    ("Yes", ["Yes, I have read it", "No"], "Yes, I have read it"),
    ("Yes", ["I will require sponsorship", "I will not require sponsorship"], None),
    ("No", ["I agree", "I do not agree"], "I do not agree"),
    ("No", ["I consent", "No, thank you"], "No, thank you"),
    ("No", ["Yes, text me", "Email only"], None),                   # nothing reads as declining
    ("No", ["I do not consent", "I don't want texts"], None),       # two declining options: ambiguous
    ("Yes", ["I agree", "I accept"], None),          # two agreeing options: ambiguous, left blank
])
def test_pick_option_maps_yes_onto_the_single_agreeing_choice(value, options, expected):
    assert apply_agent._pick_option(options, value) == expected


SAVED = {"work_authorized_us": "Yes", "privacy policy": "Yes",
         "updates from a recruiter": "Yes", "promotional text": "No"}


@pytest.mark.parametrize("label,expected", [
    # nCino's required SMS consent questions (2026-10-07 inventory), which also mention the privacy
    # policy: the longer, more specific saved key must win.
    ("I would like to receive promotional texts and SMS messages about job opportunities and recruiting "
     "events at nCino. I consent to the transfer and processing of my personal data subject to the "
     "privacy policy.", ("promotional text", "No")),
    ("I would like to receive updates from a recruiter about my job application via text and/or SMS. "
     "I consent to the transfer and processing of my personal data subject to the privacy policy.",
     ("updates from a recruiter", "Yes")),
    ("Please confirm you have read the terms of our Applicant Privacy Policy.", ("privacy policy", "Yes")),
    ("Are you legally authorized to work in the United States?", ("work_authorized_us", "Yes")),
    ("Why do you want to work here?", (None, None)),
])
def test_most_specific_saved_answer_wins(label, expected):
    assert apply_agent._eligibility_match(label, SAVED) == expected


def test_profile_answer_snaps_a_gpa_to_a_range_dropdown(page):
    page.set_content("""
      <label for="g">GPA (Undergraduate)*</label>
      <div class="select__container" style="width:300px">
        <div class="select__control"><input id="g" class="select__input" role="combobox" aria-required="true"></div>
      </div>
      <script>
        const input = document.getElementById('g');
        input.addEventListener('keydown', (e) => {
          if (e.key !== 'ArrowDown' || document.querySelector('[role=option]')) return;
          const menu = document.createElement('div');
          for (const t of ['3.0 - 3.49', '3.50 - 3.74', '3.75 - 4.0']) {
            const o = document.createElement('div'); o.setAttribute('role', 'option'); o.textContent = t; menu.appendChild(o);
          }
          input.parentElement.appendChild(menu);
        });
        input.addEventListener('input', () => { const m = document.querySelector('[role=option]'); if (m) m.parentElement.remove(); });
      </script>""")
    field = _field(page, "GPA (Undergraduate)")
    assert apply_agent._profile_answer(page, field, ["3.73/4.0 (8.75/10)", "3.73"]) == "3.50 - 3.74"


def test_number_box_never_gets_na(page):
    field = dict(_field(page, "GPA (Undergraduate)"))
    assert apply_agent._profile_answer(page, field, ["N/A"]) is None
    assert apply_agent._profile_answer(page, field, ["3.73/4.0 (8.75/10)", "3.73"]) == "3.73"


def test_form_answers_outside_the_us_has_no_country_and_unreadable_file_is_empty(tmp_path, monkeypatch):
    path = tmp_path / "master.json"
    path.write_text(json.dumps({"contact": {"location": "Chennai, India"}}))
    monkeypatch.setattr(candidate_profile, "_MASTER_DATA_PATH", path)
    assert candidate_profile.form_answers() == {"city": ["Chennai, India"]}
    monkeypatch.setattr(candidate_profile, "_MASTER_DATA_PATH", tmp_path / "missing.json")
    assert candidate_profile.form_answers() == {}
