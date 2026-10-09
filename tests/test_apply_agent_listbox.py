"""Workday-style listboxes (button[aria-haspopup=listbox] + role=option popups) in apply_agent's
form inventory and field filler, against a real browser and tests/fixtures/workday_tenant.html."""

import json
import os
from pathlib import Path

import pytest

import apply_agent

FIXTURE = (Path(__file__).resolve().parent / "fixtures" / "workday_tenant.html").as_uri()


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
    p.add_init_script(f"window.SCENARIO = {json.dumps({'start': 'wizard'})};")
    p.goto(FIXTURE)
    yield p
    context.close()


def _by_label(inventory, label):
    return next(f for f in inventory if f["label"] == label)


def test_inventory_lists_listboxes_with_their_question_and_state(page):
    inventory = apply_agent._form_inventory(page)
    country = _by_label(inventory, "Country")
    source = _by_label(inventory, "How Did You Hear About Us?")
    assert country["kind"] == source["kind"] == "listbox"
    assert country["required"] and source["required"]
    assert country["filled"] is True       # prefilled by the tenant
    assert source["filled"] is False       # still "Select One"
    assert source["selector"]


def test_required_unfilled_names_the_empty_listbox(page):
    missing = apply_agent._required_unfilled(apply_agent._form_inventory(page))
    assert "How Did You Hear About Us?" in missing
    assert "Country" not in missing


def test_fill_picks_the_matching_option_and_the_inventory_sees_it(page):
    field = _by_label(apply_agent._form_inventory(page), "How Did You Hear About Us?")
    assert apply_agent._fill_field(page, field, "linkedin") is True
    assert page.locator("[data-automation-id='sourceDropdown']").inner_text() == "LinkedIn"
    assert _by_label(apply_agent._form_inventory(page), "How Did You Hear About Us?")["filled"] is True


def test_fill_refuses_a_value_that_is_not_an_option(page):
    field = _by_label(apply_agent._form_inventory(page), "How Did You Hear About Us?")
    assert apply_agent._fill_field(page, field, "A billboard") is False
    assert page.locator("[data-automation-id='sourceDropdown']").inner_text() == "Select One"
    # the popup is closed again, not left open over the form
    assert page.locator("[role=listbox]").count() == 0
