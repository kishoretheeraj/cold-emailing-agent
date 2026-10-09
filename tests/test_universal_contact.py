"""universal_filler.contact_key: which candidate contact detail an arbitrary form field asks for.
A wrong key types the operator's details into someone else's field (a referrer's email, an
emergency contact's phone), so anything about another person maps to None."""

import pytest

import universal_filler as uf


def _f(label, name="", autocomplete="", input_type="text", kind="input"):
    return {"label": label, "name": name, "autocomplete": autocomplete, "input_type": input_type, "kind": kind}


@pytest.mark.parametrize("label,name,expected", [
    ("First Name", "", "first_name"),
    ("Legal first name", "", "first_name"),
    ("Given name(s)", "", "first_name"),
    ("", "firstName", "first_name"),
    ("", "candidate[first_name]", "first_name"),
    ("Last Name", "", "last_name"),
    ("Family name / Surname", "", "last_name"),
    ("", "lastname", "last_name"),
    ("Full name", "", "name"),
    ("Name", "", "name"),
    ("Your name", "", "name"),
    ("Email", "", "email"),
    ("E-mail address", "", "email"),
    ("Confirm email address", "", "email"),
    ("Phone", "", "phone"),
    ("Mobile phone number", "", "phone"),
    ("Cell", "", "phone"),
    ("LinkedIn Profile", "", "linkedin"),
    ("LinkedIn URL", "", "linkedin"),
    ("GitHub", "", "github"),
    ("Portfolio / Website", "", "website"),
    ("Personal website", "", "website"),
    ("City", "", "city"),
    ("Current location", "", "location"),
    ("Where are you based?", "", "location"),
])
def test_candidate_fields(label, name, expected):
    assert uf.contact_key(_f(label, name)) == expected


@pytest.mark.parametrize("label", [
    "Referrer's email",
    "Referred by (name)",
    "Name of the employee who referred you",
    "Emergency contact phone",
    "Emergency contact name",
    "Reference 1 email",
    "Hiring manager name",
    "Recruiter name",
    "Supervisor's phone number",
    "Company name",
    "Current employer",
    "School name",
    "University name",
    "Name of previous employer",
    "Phone type",
    "Country code",
    "Phone extension",
    "Job title",
    "How did you hear about us?",
    "Search jobs",
    "Cover letter",
    "Preferred pronouns",
])
def test_fields_about_someone_or_something_else_are_left_alone(label):
    assert uf.contact_key(_f(label)) is None


@pytest.mark.parametrize("autocomplete,expected", [
    ("given-name", "first_name"), ("family-name", "last_name"), ("name", "name"),
    ("email", "email"), ("tel", "phone"), ("tel-national", "phone"), ("address-level2", "city"),
])
def test_autocomplete_wins(autocomplete, expected):
    assert uf.contact_key(_f("Field 7", autocomplete=autocomplete)) == expected


def test_autocomplete_off_is_ignored():
    assert uf.contact_key(_f("Email", autocomplete="off")) == "email"


def test_input_type_email_and_tel_count_without_a_label():
    assert uf.contact_key(_f("", input_type="email")) == "email"
    assert uf.contact_key(_f("", input_type="tel")) == "phone"


def test_an_exclusion_beats_autocomplete():
    assert uf.contact_key(_f("Referrer email", autocomplete="email")) is None


@pytest.mark.parametrize("kind", ["select", "radio", "checkbox", "file", "listbox", "combobox"])
def test_only_text_fields_are_contact_fields(kind):
    assert uf.contact_key(_f("Email", kind=kind)) is None


def test_a_textarea_is_never_a_contact_field():
    assert uf.contact_key(_f("Email", kind="textarea")) is None
