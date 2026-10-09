"""universal_filler.button_role: what pressing a visible control would do. The walker presses only
'entry' and 'next'; anything that might send the application is 'submit' and is never pressed in a
preview; third-party sign-in buttons are never pressed at all."""

import pytest

import universal_filler as uf


@pytest.mark.parametrize("label", [
    "Next", "next", "Continue", "Save and Continue", "Save & Continue", "Proceed", "Next step", "Next Step »",
])
def test_next(label):
    assert uf.button_role(label) == "next"


@pytest.mark.parametrize("label", [
    "Submit", "Submit application", "Submit Application", "Send application", "Send", "Apply",
    "Finish", "Complete application", "Done", "Submit & Continue", "Continue and submit",
    "Review and submit", "Save and submit", "Confirm and apply", "Apply now",
])
def test_anything_that_might_send_is_submit(label):
    assert uf.button_role(label, on_form=True) == "submit"


@pytest.mark.parametrize("label", ["Apply", "Apply now", "Apply for this job", "Apply for this position",
                                   "I'm interested", "Start application", "Start your application"])
def test_entry_on_a_posting(label):
    assert uf.button_role(label, on_form=False) == "entry"


@pytest.mark.parametrize("label", ["Apply with LinkedIn", "Apply with Indeed", "Sign in with Google",
                                   "Continue with LinkedIn", "Apply using SEEK", "Autofill with LinkedIn",
                                   "Easy Apply"])
def test_third_party(label):
    assert uf.button_role(label) == "third_party"
    assert uf.button_role(label, on_form=False) == "third_party"


@pytest.mark.parametrize("label", ["Back", "Previous", "Cancel", "Save draft", "Search", "Upload", "Remove",
                                   "Add another", "Review", "Review application", ""])
def test_other_controls_are_ignored(label):
    assert uf.button_role(label) is None
