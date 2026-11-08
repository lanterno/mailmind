"""The floats, on the fictional world: the toast, the offer of Undo and a send on its way out, what each says, how long for, and
who hears it. One primitive shows them all (`Float` in web/src/lib/toast.tsx); the composer's own rules for a send are in
tests/e2e/browser/test_compose.py and web/src/features/compose/machine.test.ts."""
import re

import pytest
from playwright.sync_api import expect

pytestmark = pytest.mark.browser

RECEIPT = "ada-inbox-05-receipt.eml"
LIN = "lin@example.test"


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
    page.goto("/")
    page.get_by_role("tab", name=re.compile("^Everything")).click()
    expect(page.get_by_role("option").first).to_be_visible()
    return page


def row(page, world, name):
    assert world.pk(name) is not None, f"{name} has no row in the database"
    return page.locator('#rows .row[data-key="{key}"][data-folder="{folder}"]'.format(**world.copy(name)))


def bar_of(float_):
    return float_.locator(".float-bar")


def lasts(bar):
    return bar.evaluate("e => getComputedStyle(e).animationDuration")


def writing(page, to, subject, text):
    page.keyboard.press("c")
    page.get_by_role("combobox", name="To").fill(to)
    page.get_by_role("textbox", name="subject").fill(subject)
    page.get_by_role("textbox", name="message body").fill(text)


def send(page):
    page.locator("#cmpSend").click()


# ── how long, and how you can tell ─────────────────────────────────────

def test_the_offer_of_undo_shows_its_time_running_out_and_a_new_offer_starts_it_over(page, world):
    opened(page)
    row(page, world, RECEIPT).click()
    page.keyboard.press("e")
    float_ = page.locator("#undoFloat")
    expect(float_).to_contain_text("Archived")
    bar = bar_of(float_)
    expect(bar).to_be_visible()
    assert lasts(bar) == "8s"                                  # the offer's own life
    expect(page.locator("#toast .float-bar")).to_have_count(0)   # a toast offers nothing, so it has nothing to run out of
    first = bar.element_handle()
    page.keyboard.press("e")                                   # a second filing makes a second offer
    expect(page.locator("#toast")).to_have_text(re.compile("Archived"))
    until(page, lambda: first.evaluate("e => !e.isConnected"), "a new offer kept the old bar")
    assert lasts(bar_of(page.locator("#undoFloat"))) == "8s"
    page.clock.run_for(8_100)
    expect(page.locator("#undoFloat")).to_have_count(0)
    clean(page)


def test_a_sends_countdown_drains_once_over_the_window_and_doesnt_start_over_each_second(page, world):
    opened(page)
    writing(page, LIN, "Fabric order", "Hi Lin, the linen came in.")
    send(page)
    float_ = page.locator("#sendFloat")
    expect(float_).to_contain_text("Sending in 10s")
    bar = bar_of(float_)
    expect(bar).to_be_visible()
    assert lasts(bar) == "10s"                                 # the window Undo Send has
    same = bar.element_handle()
    page.clock.run_for(3_000)
    expect(float_).to_contain_text("Sending in 7s")
    assert same.evaluate("e => e.isConnected")                  # the same bar, still running
    page.clock.run_for(8_000)
    expect(float_).to_contain_text("Sent")
    expect(bar_of(float_)).to_have_count(0)                     # nothing left to run out of
    clean(page)
