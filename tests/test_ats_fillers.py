"""Tests mock the Playwright Page object directly -- no real browser launched. Each filler is
tested for 'fills what's there, skips what's missing' -- never raises on an absent field."""

from unittest.mock import MagicMock

import ats_fillers

_FIELDS = {"name": "Kishore Theeraj", "email": "kishore@example.com", "phone": "+1 603-322-0535",
           "location": "Hanover, NH", "linkedin": "linkedin.com/in/kishoretheeraj"}


def test_fill_ashby_fills_name_and_email():
    page = MagicMock()
    ats_fillers.fill_ashby(page, _FIELDS)
    page.get_by_label.assert_any_call("Name")
    page.get_by_label.assert_any_call("Email")
    page.get_by_label.return_value.fill.assert_called()


def test_fill_greenhouse_fills_name_and_email():
    page = MagicMock()
    ats_fillers.fill_greenhouse(page, _FIELDS)
    page.get_by_label.assert_any_call("Full Name")
    page.get_by_label.assert_any_call("Email")


def test_fill_lever_fills_name_and_email():
    page = MagicMock()
    ats_fillers.fill_lever(page, _FIELDS)
    page.get_by_label.assert_any_call("Full name")
    page.get_by_label.assert_any_call("Email")


def test_fill_ashby_skips_missing_field_without_raising():
    page = MagicMock()
    page.get_by_label.side_effect = Exception("locator not found")
    ats_fillers.fill_ashby(page, _FIELDS)  # must not raise
