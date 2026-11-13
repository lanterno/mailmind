"""A Gmail account's own labels in the rail: a lane of what carries each, and nothing for any other account."""
import pytest
from playwright.sync_api import expect

from fixtures import world as w

pytestmark = pytest.mark.browser

CY = "cy-inbox-03-unrelated.eml"
TICKETS = "Your train tickets"


def clean(page):
    assert page.problems == [], page.problems
    assert page.blocked == [], page.blocked


def labelled(world):
    """One more message in Cy's Gmail INBOX, carrying the label Travel as Gmail's own page left it, for the next sync to
    bring in. A sync reads the labels of mail it hasn't read, so the label comes on a new message."""
    uid = world.server.deliver(w.CY, "INBOX", w.variant(CY, Message_ID="<tickets-12@rail.example.test>", Subject=TICKETS))
    with world.server.lock:
        world.server.mailbox(w.CY, "INBOX").messages[uid].labels.add("Travel")


def accounts_inbox(page, who):
    page.goto(f"/m/{who.replace('@', '%40')}/INBOX")
    expect(page.locator('#folderrail .frow[data-folder="INBOX"]')).to_have_attribute("aria-current", "page")


def test_a_gmail_accounts_labels_are_lanes_that_open_what_carries_each(page, app, world):
    labelled(world)
    app.work("sync", w.CY, "INBOX")
    accounts_inbox(page, w.CY)
    lane = page.locator("#folderrail a.frow", has_text="Travel")
    expect(lane.locator(".unread")).to_have_text("1")
    lane.click()
    expect(page.locator("#clientCtx b")).to_have_text('label:"Travel"')
    expect(page.locator("#rows .row[data-key]", has_text=TICKETS)).to_be_visible()
    expect(lane).to_have_attribute("aria-current", "page")
    clean(page)


def test_a_label_a_sync_brought_shows_in_a_tab_already_open(page, app, world):
    accounts_inbox(page, w.CY)
    lane = page.locator("#folderrail a.frow", has_text="Travel")
    expect(lane).to_have_count(0)
    labelled(world)
    assert app.post("/api/refresh", {}).status == 200     # a sync of every account, which tells the page as each ends
    expect(lane.locator(".unread")).to_have_text("1")
    clean(page)


def test_another_kind_of_account_shows_no_labels(page, app, world):
    labelled(world)
    app.work("sync", w.CY, "INBOX")
    accounts_inbox(page, w.ADA)
    expect(page.locator("#folderrail .frail-acct", has_text="Labels")).to_have_count(0)
    clean(page)
