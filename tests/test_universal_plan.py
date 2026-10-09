"""universal_filler.contact_plan / file_targets: which inventory fields get the candidate's
details and which file inputs get the documents. Ambiguity fills nothing."""

import universal_filler as uf


def _f(sel, label, kind="input", **kw):
    return dict({"selector": sel, "label": label, "kind": kind, "name": "", "autocomplete": "", "input_type": "text"}, **kw)


def test_one_field_per_key():
    plan = uf.contact_plan([_f("#a", "First name"), _f("#b", "Last name"), _f("#c", "Email"), _f("#d", "Why us?")])
    assert plan == {"#a": "first_name", "#b": "last_name", "#c": "email"}


def test_a_key_two_fields_claim_fills_neither():
    plan = uf.contact_plan([_f("#a", "Email"), _f("#b", "Work email"), _f("#c", "Phone")])
    assert plan == {"#c": "phone"}


def test_email_and_confirm_email_is_the_allowed_pair():
    assert uf.contact_plan([_f("#a", "Email"), _f("#b", "Confirm email")]) == {"#a": "email", "#b": "email"}
    assert uf.contact_plan([_f("#a", "Confirm email"), _f("#b", "Re-enter email")]) == {}


def test_fields_without_a_selector_are_skipped():
    assert uf.contact_plan([_f(None, "Email")]) == {}


def test_files_by_label():
    files = [_f("#cv", "Resume/CV", "file"), _f("#cl", "Cover letter", "file"), _f("#t", "Transcript", "file")]
    assert uf.file_targets(files) == {"resume": "#cv", "cover_letter": "#cl"}


def test_a_single_neutral_file_input_is_the_resume():
    assert uf.file_targets([_f("#f", "Attach a file", "file")]) == {"resume": "#f"}


def test_a_single_file_input_for_something_else_gets_nothing():
    for label in ("Transcript", "Profile photo", "Writing sample", "Cover letter", "Other documents"):
        assert "resume" not in uf.file_targets([_f("#f", label, "file")]), label


def test_two_resume_inputs_are_ambiguous():
    assert uf.file_targets([_f("#a", "Resume", "file"), _f("#b", "Upload CV", "file")]) == {}
