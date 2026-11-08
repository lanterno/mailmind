"""Filters on the page: a search offers to make one, the drawer lists them for editing and removing, and the server's refusal is shown."""
import re

import pytest
from axe_playwright_python.sync_playwright import Axe
from playwright.sync_api import expect

from fixtures import world as w
from mailmind.bootstrap.mailbox import commands

pytestmark = pytest.mark.browser

LUNCH = "ada-inbox-02-lunch-confirm.eml"
INBOX = f"/m/{w.ADA.replace('@', '%40')}/INBOX"
FADES_DONE = """Promise.all(document.getAnimations()
    .filter(a => a.effect.getComputedTiming().iterations !== Infinity)
    .map(a => a.finished.catch(() => null)))"""


def clean(page):
    assert page.problems == [], page.problems
    assert page.blocked == [], page.blocked


def box(page):
    return page.get_by_role("combobox", name="Search")


def search(page, words):
    page.goto(INBOX)
    expect(page.locator("#listTitle")).to_have_text("Inbox")
    box(page).fill(words)
    box(page).press("Enter")
    expect(page.locator("#clientCtx")).to_contain_text(words)


def dialog(page, name="New filter"):
    return page.get_by_role("dialog", name=name)


def filters_of(app):
    return app.get("/api/rules").json["rules"]["filters"]


def drawer(page):
    page.goto(INBOX)
    page.locator("#statusBtn").click()
    page.get_by_role("button", name=re.compile("^Curate")).click()
    expect(page.get_by_role("dialog", name="Curate")).to_be_visible()


def made(app, q="from:lin@example.test", star=True, read=False):
    r = app.post("/api/filters", {"verb": "create", "q": q, "star": star, "read": read})
    assert r.status == 200, r.json


def test_a_search_offers_a_filter_of_itself(page, app):
    search(page, "from:lin@example.test")
    page.locator("#createFilter").click()
    d = dialog(page)
    expect(d.get_by_label("New mail that fits this search")).to_have_value("from:lin@example.test")
    expect(d.get_by_label("Star it")).to_be_checked()
    expect(d.get_by_label("Mark it read")).not_to_be_checked()
    expect(d).to_contain_text("not to what’s already here")
    expect(d).to_contain_text("never moves mail")
    d.get_by_role("button", name="Create filter").click()
    expect(d).to_have_count(0)
    expect(page.locator("#toast")).to_have_text("Filter created")
    saved = filters_of(app)
    assert [(f["q"], f["star"], f["read"]) for f in saved] == [("from:lin@example.test", True, False)]
    clean(page)


def test_the_filter_stars_the_next_mail_that_fits(page, app, world):
    search(page, "from:lin@example.test")
    page.locator("#createFilter").click()
    dialog(page).get_by_role("button", name="Create filter").click()
    expect(dialog(page)).to_have_count(0)
    uid = world.deliver(w.ADA, "INBOX", w.variant(LUNCH, Message_ID="<filter-page@example.test>", Subject="Lunch again"))
    page.goto(INBOX)
    page.get_by_role("tab", name="Everything").click()
    expect(page.locator("#rows .row", has_text="Lunch again").first.locator(".rflag")).to_be_visible()
    commands.drain()
    assert w.FLAGGED in world.server.messages(w.ADA, "INBOX")[uid].flags
    clean(page)


def test_a_search_held_to_one_account_makes_a_filter_held_to_it(page, app):
    page.goto(INBOX)
    expect(page.locator("#listTitle")).to_have_text("Inbox")
    box(page).fill("from:lin@example.test")
    box(page).press("Enter")
    page.locator("#scopeTog").click()
    expect(page.locator("#scopeTog")).to_have_text("this account ▾")
    page.locator("#createFilter").click()
    expect(dialog(page).get_by_label("New mail that fits this search")).to_have_value(f"from:lin@example.test account:{w.ADA}")
    clean(page)


def test_what_a_filter_cant_look_at_is_said_in_the_dialog(page, app):
    search(page, "from:lin@example.test has:attachment")
    page.locator("#createFilter").click()
    d = dialog(page)
    d.get_by_role("button", name="Create filter").click()
    expect(d.locator("#filterWhy")).to_contain_text("has:attachment")
    expect(d).to_be_visible()
    assert filters_of(app) == []
    d.get_by_label("New mail that fits this search").fill("from:lin@example.test")
    page.problems.clear()                                       # the 400 is on the console
    d.get_by_role("button", name="Create filter").click()
    expect(d).to_have_count(0)
    assert len(filters_of(app)) == 1
    clean(page)


def test_a_filter_has_to_do_something(page, app):
    search(page, "from:lin@example.test")
    page.locator("#createFilter").click()
    d = dialog(page)
    d.get_by_label("Star it").uncheck()
    d.get_by_role("button", name="Create filter").click()
    expect(d.locator("#filterWhy")).to_contain_text("Choose what the filter does")
    assert filters_of(app) == []
    page.problems.clear()


def test_enter_in_the_field_saves(page, app):
    search(page, "from:lin@example.test")
    page.locator("#createFilter").click()
    dialog(page).get_by_label("New mail that fits this search").press("Enter")
    expect(dialog(page)).to_have_count(0)
    assert len(filters_of(app)) == 1
    clean(page)


def test_esc_closes_the_dialog_and_saves_nothing(page, app):
    search(page, "from:lin@example.test")
    page.locator("#createFilter").click()
    d = dialog(page)
    expect(d).to_be_visible()
    page.keyboard.press("Escape")
    expect(d).to_have_count(0)
    expect(page.locator("#clientCtx")).to_contain_text("from:lin@example.test")      # the search stays
    assert filters_of(app) == []
    clean(page)


def test_back_closes_the_dialog_and_stays_on_the_search(page, app):
    search(page, "from:lin@example.test")
    page.locator("#createFilter").click()
    d = dialog(page)
    expect(d).to_be_visible()
    page.pw.go_back()
    expect(d).to_have_count(0)
    expect(page.locator("#clientCtx")).to_contain_text("from:lin@example.test")
    clean(page)


def test_a_conversation_search_offers_none(page, app, world):
    thread = world.row(LUNCH)["thread_id"]
    page.goto(f"/search?q=lunch&account={w.ADA.replace('@', '%40')}&thread={thread}")
    expect(page.locator("#scopeThread")).to_be_visible()
    expect(page.locator("#createFilter")).to_have_count(0)
    clean(page)


def test_the_palette_offers_it_from_a_search(page, app):
    search(page, "from:lin@example.test")
    page.keyboard.press("ControlOrMeta+k")
    page.get_by_role("combobox", name="Command").fill("Create a filter")
    page.keyboard.press("Enter")
    expect(dialog(page).get_by_label("New mail that fits this search")).to_have_value("from:lin@example.test")
    clean(page)


def test_the_drawer_lists_them_and_edits_one(page, app):
    made(app)
    drawer(page)
    row = page.locator("#filterList .flt")
    expect(row).to_contain_text("from:lin@example.test")
    expect(row).to_contain_text("Star")
    page.get_by_role("button", name="Edit the filter from:lin@example.test").click()
    d = dialog(page, "Edit filter")
    field = d.get_by_label("New mail that fits this search")
    expect(field).to_have_value("from:lin@example.test")
    expect(d.get_by_label("Star it")).to_be_checked()
    field.fill("subject:lunch")
    d.get_by_label("Star it").uncheck()
    d.get_by_label("Mark it read").check()
    d.get_by_role("button", name="Save").click()
    expect(d).to_have_count(0)
    expect(row).to_contain_text("subject:lunch")
    expect(row).to_contain_text("Mark read")
    assert [(f["q"], f["star"], f["read"]) for f in filters_of(app)] == [("subject:lunch", False, True)]
    expect(page.get_by_role("dialog", name="Curate")).to_be_visible()    # back where you were
    clean(page)


def test_the_drawer_removes_one(page, app):
    made(app)
    made(app, "subject:invoice", star=False, read=True)
    drawer(page)
    expect(page.locator("#filterList .flt")).to_have_count(2)
    page.get_by_role("button", name="Remove the filter subject:invoice").click()
    expect(page.locator("#filterList .flt")).to_have_count(1)
    assert [f["q"] for f in filters_of(app)] == ["from:lin@example.test"]
    page.get_by_role("button", name="Remove the filter from:lin@example.test").click()
    expect(page.locator("#filterList")).to_have_text("none")
    assert filters_of(app) == []
    clean(page)


def test_the_drawer_makes_a_new_one(page, app):
    drawer(page)
    page.locator("#newFilter").click()
    d = dialog(page)
    d.get_by_label("New mail that fits this search").fill("to:ada@example.test")
    d.get_by_role("button", name="Create filter").click()
    expect(page.locator("#filterList .flt")).to_contain_text("to:ada@example.test")
    clean(page)


def test_the_dialog_and_the_list_pass_axe(page, app):
    made(app)
    drawer(page)
    page.locator("#filterList .flt").wait_for()
    page.pw.evaluate(FADES_DONE)
    assert Axe().run(page.pw).response["violations"] == []
    page.locator("#newFilter").click()
    expect(dialog(page)).to_be_visible()
    page.pw.evaluate(FADES_DONE)
    assert Axe().run(page.pw).response["violations"] == []
    clean(page)
