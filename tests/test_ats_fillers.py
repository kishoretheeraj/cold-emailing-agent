"""Tests use fake pages -- no real browser launched. Fillers locate fields by id/name first with
a label fallback, and report per-field success so apply_agent can refuse an incomplete form."""

import re

import pytest

import ats_fillers
import config


class _FakeLocator:
    def __init__(self, present=True, raises=None):
        self.present = present
        self.raises = raises
        self.fills = []

    @property
    def first(self):
        return self

    def count(self):
        return 1 if self.present else 0

    def fill(self, value, timeout=None):
        if self.raises:
            raise self.raises
        self.fills.append((value, timeout))


class _FakePage:
    def __init__(self, present=(), raising=()):
        self.present = set(present)
        self.raising = set(raising)
        self.locators = {}
        self.queried = []

    def _get(self, key):
        self.queried.append(key)
        if key not in self.locators:
            self.locators[key] = _FakeLocator(
                present=key in self.present,
                raises=RuntimeError("boom") if key in self.raising else None)
        return self.locators[key]

    def locator(self, sel):
        return self._get(sel)

    def get_by_label(self, pattern):
        return self._get(pattern.pattern)


_FIELDS = {"name": "Kishore Theeraj", "first_name": "Kishore", "last_name": "Jaya",
           "email": "k@example.com", "phone": "+1 603", "location": "Hanover, NH",
           "linkedin": "linkedin.com/in/k"}


def test_fill_first_uses_first_matching_selector_and_config_timeout():
    page = _FakePage(present={"#a", "#b"})
    assert ats_fillers._fill_first(page, ["#a", "#b"], "v") is True
    assert page.locators["#a"].fills == [("v", config.APPLY_AGENT_FIELD_TIMEOUT_MS)]
    assert "#b" not in page.locators


def test_fill_first_skips_candidate_with_zero_matches():
    page = _FakePage(present={"#b"})
    assert ats_fillers._fill_first(page, ["#a", "#b"], "v") is True
    assert page.locators["#a"].fills == []
    assert page.locators["#b"].fills


def test_fill_first_falls_through_a_raising_candidate():
    page = _FakePage(present={"#a", "#b"}, raising={"#a"})
    assert ats_fillers._fill_first(page, ["#a", "#b"], "v") is True
    assert page.locators["#b"].fills


def test_fill_first_label_pattern_candidate():
    page = _FakePage(present={"^first name"})
    assert ats_fillers._fill_first(page, [re.compile(r"^first name", re.I)], "v") is True


def test_fill_first_returns_false_when_nothing_works():
    assert ats_fillers._fill_first(_FakePage(), ["#a", re.compile("x")], "v") is False


def test_fill_first_empty_value_is_not_attempted():
    page = _FakePage(present={"#a"})
    assert ats_fillers._fill_first(page, ["#a"], "") is None
    assert page.queried == []


def test_greenhouse_fills_first_and_last_not_full_name():
    page = _FakePage(present={"#first_name", "#last_name", "#email", "#phone"})
    report = ats_fillers.fill_greenhouse(page, _FIELDS)
    assert report["first_name"] is True and report["last_name"] is True
    assert report["email"] is True and report["phone"] is True
    assert report["linkedin"] is False
    assert "name" not in report
    assert not any("full name" in str(q).lower() for q in page.queried)


def test_greenhouse_phone_uses_id_before_any_label():
    page = _FakePage(present={"#phone"})
    ats_fillers.fill_greenhouse(page, _FIELDS)
    assert page.locators["#phone"].fills
    assert "phone" not in [q for q in page.queried if not str(q).startswith(("#", "input"))]


def test_report_omits_fields_with_empty_values():
    page = _FakePage(present={"input[name='name']", "input[name='email']"})
    report = ats_fillers.fill_lever(page, {"name": "N", "email": "e", "phone": "", "location": None})
    assert report == {"name": True, "email": True}


@pytest.mark.parametrize("filler,expected", [
    (ats_fillers.fill_ashby, {"name", "email", "phone", "location", "linkedin"}),
    (ats_fillers.fill_lever, {"name", "email", "phone", "location", "linkedin"}),
    (ats_fillers.fill_greenhouse, {"first_name", "last_name", "email", "phone", "linkedin"}),
])
def test_fillers_report_every_nonempty_field_and_never_raise(filler, expected):
    report = filler(_FakePage(), _FIELDS)
    assert set(report) == expected
    assert not any(report.values())
