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


def test_changing_from_swaps_the_signature_and_leaves_one_you_edited(page, world):
    from mailmind.bootstrap.accounts import config
    config.update_account(w.ADA, {"signature": "Ada at Quilt"})
    config.update_account(w.BEA, {"signature": "Bea at Quilt"})
    opened(page)
    page.keyboard.press("c")
    text = page.get_by_role("textbox", name="message body")
    expect(text).to_have_value("\n\n-- \nAda at Quilt")
    text.focus()
    page.pw.evaluate("document.querySelector('#cmpBody').setSelectionRange(0, 0)")
    page.keyboard.type("Hello")
    page.locator("#cmpFrom").select_option(w.BEA)
    expect(text).to_have_value("Hello\n\n-- \nBea at Quilt")
    until(page, lambda: page.pw.evaluate("document.querySelector('#cmpBody').selectionStart") == 5, "you go on writing where you were")
    text.fill("Hello\n\n-- \nBea, edited")
    page.locator("#cmpFrom").select_option(w.ADA)
    expect(text).to_have_value("Hello\n\n-- \nBea, edited")    # yours now: it stays
    clean(page)


def test_new_mail_in_the_background_notifies_and_counts_in_the_title_and_here_it_is_said(page, world):
    page.pw.add_init_script(AWAY)
    opened(page)
    page.pw.evaluate("window.away = true")
    sse.publish("new-mail", {"account": w.ADA, "folder": "INBOX", "count": 2})
    expect(page.pw).to_have_title("(2) MailMind")
    sse.publish("new-mail", {"account": w.ADA, "folder": "INBOX", "count": 1})
    expect(page.pw).to_have_title("(3) MailMind")
    first = page.pw.evaluate("({ title: notes[0].title, body: notes[0].body, tag: notes[0].tag })")
    assert first == {"title": "New mail · Ada", "body": "2 new messages", "tag": f"mailmind-{w.ADA}"}
    page.pw.evaluate("window.away = false; document.dispatchEvent(new Event('visibilitychange'))")
    expect(page.pw).to_have_title("MailMind")
    sse.publish("new-mail", {"account": w.ADA, "folder": "INBOX", "count": 1})
    expect(page.locator("#srLive")).to_have_text("1 new message arrived in Ada")
    assert page.pw.evaluate("notes.length") == 2               # with someone here, no desktop notification
    clean(page)


def test_filing_the_server_refuses_puts_you_back_on_the_message(page, world):
    opened(page)
    receipt = row(page, world, RECEIPT)
    receipt.click()
    expect(receipt).to_have_class(re.compile(r"\bsel\b"))
    world.server.fail("UID MOVE", user=w.ADA, times=None)
    world.server.fail("UID COPY", user=w.ADA, times=None)
    page.keyboard.press("e")
    expect(page.locator("#toast")).to_have_text("Couldn’t archive")
    expect(receipt).to_have_class(re.compile(r"\bsel\b"))
    clean(page)


def test_a_star_says_so_either_way(page, world):
    opened(page)
    row(page, world, RECEIPT).click()
    page.keyboard.press("s")
    expect(page.locator("#toast")).to_have_text("Starred")
    page.keyboard.press("s")
    expect(page.locator("#toast")).to_have_text("Unstarred")
    world.server.fail("UID STORE", user=w.ADA, times=None)
    page.keyboard.press("s")
    expect(page.locator("#toast")).to_have_text("Couldn’t star")
    clean(page)


def test_with_single_keys_off_help_still_opens(page, world):
    opened(page)
    page.pw.evaluate("localStorage.setItem('mm_nokeys', '1')")
    receipt = row(page, world, RECEIPT)
    receipt.click()
    page.keyboard.press("e")
    page.wait_for_timeout(100)
    expect(receipt).to_have_count(1)                            # off, as you asked
    page.keyboard.press("?")
    expect(page.locator(".legsearch")).to_contain_text("from:")
    clean(page)
