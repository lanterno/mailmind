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


def curate_drawer(page):
    page.goto(f"/next/m/{w.ADA}/INBOX")
    page.locator("#statusBtn").click()
    page.get_by_role("button", name=re.compile("^Curate")).click()
    d = page.get_by_role("dialog", name="Curate")
    expect(d).to_be_visible()
    return d


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


def test_help_names_every_search_operator_the_page_does(page, world):
    opened(page)
    page.keyboard.press("?")
    expect(page.locator(".legsearch")).to_contain_text("-exclude")
    clean(page)


def test_the_account_menu_names_itself_marks_yours_says_who_cant_connect_and_leads_to_settings(page, app, world):
    digest = app.get("/api/digest?min=1&account=").json
    digest["status"] = {**digest["status"], w.BEA: {"health": "error"}}
    page.pw.route(re.compile(r"/api/digest"), lambda route: route.fulfill(json=digest))
    opened(page)
    switch = page.locator("#acctSw")
    expect(switch).to_have_attribute("title", "Ada — switch account")
    expect(switch.locator(".as-chev")).to_have_count(1)
    switch.click()
    expect(page.locator(".am-ttl")).to_have_text("Show mail from")
    menu = page.locator(".acct-menu")
    expect(menu.locator(".am-item", has_text="Ada").locator(".am-chk")).to_have_count(1)
    expect(menu.locator(".am-item", has_text="Bea").locator(".err")).to_have_text("can’t connect")
    menu.get_by_role("menuitemradio", name=re.compile("^All inboxes")).click()
    expect(switch.locator("small.err")).to_have_text(re.compile(r"^1 of \d can’t connect$"))
    switch.click()
    page.locator(".acct-menu .am-item", has_text="Add or manage accounts").click()
    expect(page.get_by_role("heading", name="Email connections")).to_be_visible()
    clean(page)


def test_the_rail_keeps_the_pages_order_and_all_inboxes_is_one_inbox(page, world):
    opened(page)
    names = page.locator("#folderrail .frow .fn")
    expect(names.last).to_have_text("Snoozed")
    labels = names.all_inner_texts()
    assert labels[0] == "Inbox" and labels.index("Feed") < labels.index("Snoozed")
    assert labels.index("Feed") > labels.index("Sent")           # folders, then views, then what isn't a folder
    page.locator("#acctSw").click()
    page.get_by_role("menuitemradio", name=re.compile("^All inboxes")).click()
    expect(page.locator("#listTitle")).to_have_text("Inbox")
    expect(page.locator("#folderrail .frow.on .fn")).to_have_text("Inbox")
    expect(page.locator("#folderrail")).not_to_contain_text("newest first")
    clean(page)


def test_a_saved_view_doesnt_offer_to_be_saved_again(page, world):
    opened(page)
    asked = []
    page.pw.on("dialog", lambda d: (asked.append(d.message), d.dismiss()))
    page.locator('#folderrail [data-view]:not([data-view^="__lane_"])').first.click()
    expect(page.locator("#clientCtx")).to_be_visible()
    expect(page.locator("#clientCtx .ctx-save")).to_have_count(0)
    page.keyboard.press("v")
    page.wait_for_timeout(100)
    assert asked == []
    page.locator("#search").fill("is:unread from:lin")
    page.keyboard.press("Enter")
    expect(page.locator("#clientCtx .ctx-save")).to_be_visible()
    clean(page)


def test_a_click_anywhere_on_a_flooding_sender_opens_its_cleanup(page, world):
    for i in range(2):
        world.deliver(w.ADA, "INBOX", w.variant("ada-inbox-03-newsletter.eml", Message_ID=f"<issue-{300 + i}@loom.example.test>", Subject=f"Issue {300 + i}"))
    d = curate_drawer(page)
    d.locator(".flood-row .flood-nm").first.click()
    expect(page.get_by_role("dialog", name="Clean up a sender")).to_be_visible()
    clean(page)


def test_an_audit_line_opens_its_account(page, world):
    page.pw.route(re.compile(r"/api/audit"), lambda route: route.fulfill(json={"ok": True, "events": [
        {"ts": "2026-11-02T11:00:00+00:00", "kind": "reconcile", "account": "gone@example.test", "pruned": 1},
        {"ts": "2026-11-02T10:00:00+00:00", "kind": "reconcile", "account": w.BEA, "pruned": 2}]}))
    d = curate_drawer(page)
    page.locator('#console .ln[data-acct="gone@example.test"]').click()    # a removed account has no folders to open
    expect(d).to_be_visible()
    page.locator(f'#console .ln[data-acct="{w.BEA}"]').click()
    expect(d).to_have_count(0)
    expect(page.locator("#acctSw")).to_contain_text(w.BEA)
    clean(page)


def test_aborting_a_sweep_says_so_and_escape_aborts_it(page, world):
    world.deliver(w.ADA, "INBOX", w.raw("ada-inbox-12-scam.eml"))
    d = curate_drawer(page)
    sweep = d.get_by_role("button", name=re.compile(r"^Sweep \d+")).first
    sweep.click()
    d.get_by_role("button", name="Abort").click()
    expect(page.locator("#toast")).to_have_text("Aborted")
    expect(d.locator(".armline")).to_have_count(0)
    sweep.click()
    expect(d.locator(".armline")).to_be_visible()
    page.keyboard.press("Escape")
    expect(d.locator(".armline")).to_have_count(0)
    expect(d).to_be_visible()                                   # the sweep, not the drawer
    clean(page)
