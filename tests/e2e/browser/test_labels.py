"""A Gmail account's own labels in the rail: a lane of what carries each, and nothing for any other account."""
import pytest
from playwright.sync_api import expect

from fixtures import world as w
from mailmind.bootstrap import accounts
from mailmind.bootstrap.mailbox import ingest, store

pytestmark = pytest.mark.browser

CY = "cy-inbox-03-unrelated.eml"


def clean(page):
    assert page.problems == [], page.problems
    assert page.blocked == [], page.blocked


def labelled(world):
    next(m for m in world.server.messages(w.CY, "INBOX").values() if (m.headers().get("Message-ID") or "").strip() == w.message_id(CY)
         ).labels.add("Travel")
    c = store.conn()
    try:
        c.execute("UPDATE sync_state SET last_uid=0 WHERE account=? AND folder='INBOX'", (w.CY,))
        c.commit()
    finally:
        c.close()
    ingest.sync_new(accounts.get(w.CY))


def accounts_inbox(page, who):
    page.goto(f"/m/{who.replace('@', '%40')}/INBOX")
    expect(page.locator('#folderrail .frow[data-folder="INBOX"]')).to_have_attribute("aria-current", "page")


def test_a_gmail_accounts_labels_are_lanes_that_open_what_carries_each(page, world):
    labelled(world)
    accounts_inbox(page, w.CY)
    lane = page.locator("#folderrail a.frow", has_text="Travel")
    expect(lane.locator(".unread")).to_have_text("1")
    lane.click()
    expect(page.locator("#clientCtx b")).to_have_text('label:"Travel"')
    expect(page.locator(f'#rows .row[data-key="{world.key(CY)}"]')).to_be_visible()
    expect(lane).to_have_attribute("aria-current", "page")
    clean(page)


def test_another_kind_of_account_shows_no_labels(page, world):
    labelled(world)
    accounts_inbox(page, w.ADA)
    expect(page.locator("#folderrail .frail-acct", has_text="Labels")).to_have_count(0)
    clean(page)
