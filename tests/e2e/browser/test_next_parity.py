"""What the page does and the React app has to as well, each found by holding the two side by side."""
import re

import pytest
from playwright.sync_api import expect

from fixtures import world as w
from mailmind.platform import sse

pytestmark = [pytest.mark.browser, pytest.mark.usefixtures("serve_the_build")]

RECEIPT = "ada-inbox-05-receipt.eml"

# Stands in for the browser's Notification, and lets a test say the tab is in the background.
AWAY = """
window.notes = [];
window.Notification = class { constructor(title, o) { this.title = title; Object.assign(this, o); window.notes.push(this); } close() {} };
window.Notification.permission = "granted";
window.away = false;
Object.defineProperty(document, "hidden", { get: () => window.away });
"""


def clean(page):
    assert page.problems == [], page.problems
    assert page.blocked == [], page.blocked


def until(page, check, what):
    for _ in range(100):
        if check():
            return
        page.wait_for_timeout(50)
    assert check(), what


def opened(page):
    page.goto("/next")
    page.get_by_role("tab", name=re.compile("^Everything")).click()
    expect(page.get_by_role("option").first).to_be_visible()
    return page


def row(page, world, name):
    pk = world.pk(name)
    assert pk is not None, f"{name} has no row in the database"
    return page.locator(f'#rows .row[data-pk="{pk}"]')


def test_read_only_says_what_it_files_waits_for(page, world, monkeypatch):
    monkeypatch.delenv("MAILMIND_NOSYNC", raising=False)
    monkeypatch.setenv("MAILMIND_PROFILE", "read-only")
    opened(page)
    expect(page.locator("#statusTxt")).to_have_text("Read-only")
    receipt = row(page, world, RECEIPT)
    receipt.click()
    page.keyboard.press("e")
    expect(page.locator("#toast")).to_have_text("Archived · waits for a full run")
    clean(page)


def test_a_rows_snooze_button_asks_when_and_snoozes_that_row(page, world):
    opened(page)
    receipt = row(page, world, RECEIPT)
    receipt.hover()
    receipt.locator('[data-racts="snooze"]').click()
    menu = page.get_by_role("dialog", name="Snooze until")
    expect(menu).to_be_visible()
    expect(receipt).not_to_have_class(re.compile(r"\bsel\b"))   # asking about a row doesn't open it
    menu.get_by_role("button", name=re.compile("Tomorrow")).click()
    expect(receipt).to_have_count(0)
    expect(page.locator("#toast")).to_have_text("Snoozed")
    clean(page)


def test_a_double_click_on_a_row_reads_it_in_focus(page, world):
    opened(page)
    receipt = row(page, world, RECEIPT)
    receipt.locator(".sj").dblclick()
    expect(receipt).to_have_class(re.compile(r"\bsel\b"))
    expect(page.locator("body")).to_have_class(re.compile("read-focus"))
    clean(page)


def test_asking_for_the_folders_leaves_focus_reading_and_focus_reading_closes_their_drawer(page, world):
    page.set_viewport_size({"width": 1000, "height": 800})      # the rail is a drawer here, and focus reading still is one
    opened(page)
    row(page, world, RECEIPT).click()
    body = page.locator("body")
    page.keyboard.press("Enter")
    expect(body).to_have_class(re.compile("read-focus"))
    page.keyboard.press("[")
    expect(body).not_to_have_class(re.compile("read-focus"))
    expect(body).to_have_class(re.compile("rail-open"))
    page.keyboard.press("Enter")
    expect(body).to_have_class(re.compile("read-focus"))
    expect(body).not_to_have_class(re.compile("rail-open"))
    clean(page)
