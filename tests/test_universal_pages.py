"""universal_filler's page reading against real fixture pages in a real browser: page_state,
entry_control (never a third-party apply button, never a site search box), controls."""

import os
from pathlib import Path

import pytest

import universal_filler as uf

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "universal"


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
def open_page(browser):
    contexts = []

    def _open(name):
        context = browser.new_context()
        contexts.append(context)
        page = context.new_page()
        page.goto((FIXTURES / name).as_uri())
        return page

    yield _open
    for context in contexts:
        context.close()


@pytest.mark.parametrize("name,state", [
    ("posting.html", "posting"),
    ("single_page.html", "form"),
    ("wizard.html", "form"),
    ("email_code.html", "form"),
    ("password_wall.html", "auth"),
])
def test_page_state(open_page, name, state):
    assert uf.page_state(open_page(name)) == state


def test_the_code_field_reads_as_email_code(open_page):
    page = open_page("email_code.html")
    page.fill("#em", "a@b.co")
    page.click("#next")
    assert uf.page_state(page) == "email_code"


def test_entry_control_is_the_real_apply_link_not_the_linkedin_button(open_page):
    page = open_page("posting.html")
    entry = uf.entry_control(page)
    assert entry is not None
    assert entry.inner_text().strip() == "Apply now"


def test_no_entry_control_on_a_form(open_page):
    assert uf.entry_control(open_page("password_wall.html")) is None


def test_controls_skip_the_search_box_button(open_page):
    labels = [label for _, label in uf.controls(open_page("posting.html"))]
    assert "Search" not in labels
    assert "Apply with LinkedIn" in labels and "Apply now" in labels


def test_controls_are_retagged_on_every_scan(open_page):
    page = open_page("posting.html")
    uf.controls(page)
    first = page.locator("[data-uf-ctl]").count()
    uf.controls(page)
    assert page.locator("[data-uf-ctl]").count() == first


def test_page_state_on_a_broken_page_is_unknown():
    class Broken:
        def evaluate(self, *_):
            raise RuntimeError("gone")
    assert uf.page_state(Broken()) == "unknown"
