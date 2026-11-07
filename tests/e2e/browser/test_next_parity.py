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
Object.defineProperty(document, "visibilityState", { get: () => (window.away ? "hidden" : "visible") });
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


def test_escape_in_settings_steps_back_one_thing_at_a_time(page, world):
    opened(page)
    page.locator("#settingsBtn").click()
    title = page.get_by_role("heading", name="Email connections")
    expect(title).to_be_visible()
    page.locator("#setAdd").click()
    field = page.locator("#setList input").first
    field.focus()
    page.keyboard.press("Escape")                               # out of the field, with what's typed still there
    expect(field).not_to_be_focused()
    expect(field).to_be_visible()
    page.keyboard.press("Escape")                               # out of the form
    expect(page.locator("#setAdd")).to_be_visible()
    expect(page.locator("#setList input")).to_have_count(0)
    expect(title).to_be_visible()
    remove = page.locator("#setList .set-mini.danger").first
    remove.click()
    expect(remove).to_have_text("Confirm?")
    page.keyboard.press("Escape")                               # out of asking
    expect(remove).to_have_text("Remove")
    expect(title).to_be_visible()
    page.keyboard.press(",")
    expect(title).to_be_hidden()
    page.locator("#settingsBtn").click()
    expect(title).to_be_visible()
    page.keyboard.press("Escape")
    expect(title).to_be_hidden()
    clean(page)


def test_a_message_slow_to_come_shows_what_is_known_and_says_so(page, world):
    opened(page)
    page.pw.route(re.compile(r"/api/message/\d+/body"), lambda route: None)    # no answer yet
    receipt = row(page, world, RECEIPT)
    receipt.click()
    expect(page.locator("#reader .rd-subj")).to_have_text(receipt.locator(".sj").inner_text())
    expect(page.locator("#rdSkelHint")).to_contain_text("Still fetching from the server", timeout=7000)
    clean(page)


def test_a_message_that_wont_load_offers_retry(page, world):
    opened(page)
    page.pw.route(re.compile(r"/api/message/\d+/body"), lambda route: route.fulfill(status=500, body="{}"))
    row(page, world, RECEIPT).click()
    expect(page.locator("#reader .nores")).to_contain_text("Couldn’t load.", timeout=7000)
    page.pw.unroute(re.compile(r"/api/message/\d+/body"))
    page.locator("#reader").get_by_role("button", name="Retry").click()
    expect(page.locator("#reader .rd-frame, #reader iframe").first).to_be_visible()
    page.problems[:] = [p for p in page.problems if "500" not in p]
    clean(page)


def test_the_palette_says_what_the_pages_does(page, world):
    opened(page)
    page.keyboard.press("ControlOrMeta+k")
    labels = page.locator("#plist .pi-l").all_inner_texts()
    for label in ("Compose new message", "Commitments — your to-dos", "Weekly review — how your week went", "Feed (lane)"):
        assert label in labels
    assert "Views" in page.locator("#plist .pgrp").all_inner_texts()
    page.keyboard.press("Escape")
    row(page, world, RECEIPT).click()
    page.keyboard.press("ControlOrMeta+k")
    page.locator("#pq").fill("Reading settings")
    expect(page.locator(".pi.sel")).to_have_text("Reading settings — width, text, mail colors")
    page.keyboard.press("Enter")
    expect(page.locator(".read-menu")).to_be_visible()
    clean(page)


def test_the_palette_reaches_every_accounts_folders(page, world):
    opened(page)
    page.keyboard.press("ControlOrMeta+k")
    page.locator("#pq").fill("Cy / All mail")
    expect(page.locator(".pi.sel")).to_contain_text("Cy / All mail")
    page.keyboard.press("Enter")
    expect(page.locator("#listTitle")).to_have_text("All mail")
    expect(page.locator("#acctSw")).to_contain_text("Cy")
    clean(page)


def test_the_theme_button_says_which_theme(page, world):
    opened(page)
    page.locator("#themeBtn").click()
    expect(page.locator("#toast")).to_have_text(re.compile(r"^(Day|Dusk|Night) theme$"))
    clean(page)


def test_the_slash_key_opens_the_folded_search_on_a_phone_and_an_empty_one_folds_back(page, world):
    page.set_viewport_size({"width": 420, "height": 800})
    opened(page)
    page.keyboard.press("/")
    body = page.locator("body")
    expect(body).to_have_class(re.compile("search-open"))
    expect(page.locator("#search")).to_be_focused()
    page.locator("#search").blur()
    expect(body).not_to_have_class(re.compile("search-open"))
    clean(page)


def test_a_folder_on_a_desktop_opens_on_its_first_message_and_closing_it_stays_closed(page, world):
    opened(page)
    expect(page.locator("#reader .rd-subj")).to_have_count(0)    # the Inbox opens on the brief
    page.locator('#folderrail [data-folder="Archive"]').click()
    first = page.locator("#rows .row[data-pk]").first
    expect(first).to_have_class(re.compile(r"\bsel\b"))
    expect(page.locator("#reader .rd-subj")).to_be_visible()
    page.keyboard.press("Escape")
    expect(first).not_to_have_class(re.compile(r"\bsel\b"))
    expect(page.locator("#reader")).to_have_text("No message selected.")
    page.wait_for_timeout(100)
    expect(first).not_to_have_class(re.compile(r"\bsel\b"))
    clean(page)


def test_reconcile_says_how_many_stand(page, world):
    opened(page)
    with world.server.lock:                                      # deleted in another client
        world.server.mailbox(w.ADA, "INBOX").messages.pop(world.uid(RECEIPT))
    page.keyboard.press("ControlOrMeta+k")
    page.locator("#pq").fill("Reconcile")
    page.keyboard.press("Enter")
    expect(page.locator("#toast")).to_have_text(re.compile(r"^Struck 1 deleted items · [\d,]+ stand$"), timeout=7000)
    clean(page)


def test_a_meeting_about_to_start_notifies_with_where_and_a_click_opens_the_invite(page, world):
    pk = world.pk(RECEIPT)
    page.pw.add_init_script(AWAY + "window.away = true;")
    page.pw.route("**/api/upcoming", lambda route: route.fulfill(json={"ok": True, "items": [
        {"pk": pk, "account": w.ADA, "start": "2026-11-02T12:05:00+00:00", "end": "2026-11-02T12:35:00+00:00", "summary": "Standup", "location": "Room 1", "organizer": "", "all_day": False}]}))
    opened(page)
    expect(page.locator("#soonPill")).to_be_visible()
    until(page, lambda: page.pw.evaluate("notes.filter((n) => n.tag.startsWith('mailmind-soon-')).length") == 1, "the reminder went out")
    note = page.pw.evaluate("(() => { const n = notes.find((x) => x.tag.startsWith('mailmind-soon-')); return { title: n.title, body: n.body }; })()")
    assert note["title"] == "Standup starts in 5 min"
    assert note["body"].endswith(" · Room 1")
    page.pw.evaluate("notes.find((x) => x.tag.startsWith('mailmind-soon-')).onclick()")
    expect(row(page, world, RECEIPT)).to_have_class(re.compile(r"\bsel\b"))
    clean(page)


def test_mail_back_from_snooze_says_so_where_no_list_shows_it(page, world):
    opened(page)
    sse.publish("snooze-wake", {"accounts": [w.ADA], "count": 1})
    page.wait_for_timeout(200)
    expect(page.locator("#toast")).not_to_have_text("1 snoozed message back")    # it's on the list you're looking at
    page.locator("#search").fill("from:lin")
    page.keyboard.press("Enter")
    expect(page.locator("#clientCtx")).to_be_visible()
    sse.publish("snooze-wake", {"accounts": [w.ADA], "count": 2})
    expect(page.locator("#toast")).to_have_text("2 snoozed messages back")
    clean(page)


def test_a_week_with_nothing_unanswered_says_so(page, world, ai):
    page.goto("/next")
    page.get_by_role("button", name="Your week").click()
    panel = page.get_by_role("dialog", name="Weekly review")
    expect(panel.locator(".rv-empty")).to_have_text("Nothing important is unanswered.")
    expect(panel.locator(".rv-sec", has_text="Still needs you")).to_have_text("Still needs you")
    clean(page)


def test_a_review_that_cant_be_written_says_why(page, world, ai):
    page.pw.route("**/api/review", lambda route: route.fulfill(json={"ok": False, "error": "The mail couldn't be read"}))
    page.goto("/next")
    page.get_by_role("button", name="Your week").click()
    panel = page.get_by_role("dialog", name="Weekly review")
    expect(panel.locator(".rv-err")).to_contain_text("The mail couldn't be read")
    clean(page)


def test_search_scope_narrows_to_this_account_and_is_remembered(page, world, ai):
    opened(page)
    page.locator("#search").fill("in:INBOX")
    page.keyboard.press("Enter")
    toggle = page.locator("#scopeTog")
    expect(toggle).to_have_text("all accounts ▾")
    expect(page.locator("#rows .acctchip", has_text="Bea").first).to_be_visible()
    toggle.click()
    expect(toggle).to_have_text("this account ▾")
    expect(page.locator("#rows .acctchip", has_text="Bea")).to_have_count(0)
    expect(page.locator("#rows .row").first).to_be_visible()
    page.pw.reload()
    expect(page.locator("#scopeTog")).to_have_text("this account ▾")
    with page.pw.expect_request("**/api/ai/ask") as asked:
        page.locator("#search").fill("? when is lunch")
        page.keyboard.press("Enter")
    assert asked.value.post_data_json["account"] == w.ADA
    clean(page)

def test_closing_a_bulk_action_takes_the_command_out_of_the_search_box(page, world, ai):
    opened(page)
    box = page.get_by_role("textbox", name="Search")
    page.keyboard.press("/")
    box.fill("!")
    box.press("Enter")
    expect(page.locator(".do-intro")).to_contain_text("before anything happens")
    page.get_by_role("button", name="Close").click()
    expect(page.locator(".do-intro")).to_have_count(0)
    expect(box).to_have_value("")
    clean(page)

def test_with_the_event_stream_down_the_counts_and_the_tray_are_still_read(page, app, world):
    page.route("**/api/events", lambda route: route.abort())
    asked = []
    page.on("request", lambda r: asked.append(r.url))
    opened(page)
    page.wait_for_timeout(100)                                  # the stream has said it is down
    asked.clear()
    page.clock.run_for(21_000)
    until(page, lambda: any("/api/outbox" in u for u in asked), "the tray was never read again")
    assert not any("/api/digest" in u for u in asked)
    page.clock.run_for(25_000)
    until(page, lambda: any("/api/digest" in u for u in asked), "the counts were never read again")
    assert all("/api/events" in p or "ERR_FAILED" in p for p in page.problems), page.problems
    page.problems.clear()
    assert page.blocked == [], page.blocked


def test_with_the_event_stream_up_nothing_is_read_on_a_timer(page, app, world):
    asked = []
    opened(page)
    expect(page.get_by_role("option").first).to_be_visible()
    page.wait_for_timeout(300)                                  # the stream has opened
    page.on("request", lambda r: asked.append(r.url))
    page.clock.run_for(46_000)
    page.wait_for_timeout(200)
    assert not any("/api/digest" in u or "/api/outbox" in u for u in asked), asked
    clean(page)

def test_settings_says_what_each_inbox_holds_and_what_stops_one_answering(page, app, world):
    stats = app.get("/api/stats").json
    held = {a["account"]: a["in_inbox"] for a in stats["accounts"]}
    down = dict(stats, accounts=[dict(a, error="LoginFailedForGood and more", in_inbox=None) if a["account"] == w.BEA else a
                                 for a in stats["accounts"]])
    page.route("**/api/stats", lambda route: route.fulfill(json=down))
    opened(page)
    page.locator("#settingsBtn").click()
    ada = page.locator(f'.set-row[data-email="{w.ADA}"]')
    expect(ada.locator(".acts .set-count")).to_have_text(str(held[w.ADA]))
    bea = page.locator(f'.set-row[data-email="{w.BEA}"]')
    expect(bea.locator(".acts .set-count")).to_have_text("LoginFailedForGood")
    expect(bea.locator(".chip-dot")).to_have_class(re.compile(r"\berror\b"))
    expect(ada.locator(".chip-dot")).not_to_have_class(re.compile(r"\berror\b"))
    clean(page)

def test_a_view_that_wont_go_says_so_in_the_pages_words(page, app, world):
    opened(page)
    page.pw.on("dialog", lambda d: d.accept("Lunch"))
    page.keyboard.press("/")
    box = page.get_by_role("textbox", name="Search")
    box.fill("from:lin")
    box.press("Enter")
    page.locator("#clientCtx .ctx-save").click()
    view = page.locator("#folderrail [data-view]", has_text="Lunch")
    expect(view).to_be_visible()
    page.route("**/api/views", lambda route: route.abort() if route.request.method == "POST" else route.fallback())
    page.locator("#folderrail .vrow", has_text="Lunch").locator(".vdel").dispatch_event("click")   # at this width the rail folds it away, as the page's does
    expect(page.locator("#toast")).to_have_text("Couldn\u2019t remove view")
    assert all("ERR_FAILED" in p for p in page.problems), page.problems
    page.problems.clear()
    assert page.blocked == [], page.blocked

def test_a_card_on_the_brief_says_where_it_filed_the_mail(page, app, world, ai):
    from fakes import anthropic_api as fa
    from mailmind.bootstrap.assistant import answers
    from mailmind.bootstrap.mailbox import store
    lunch, receipt = "ada-inbox-01-lunch.eml", RECEIPT
    c = store.conn()
    try:
        for name in (lunch, receipt):
            answers.save_summary(c, world.pk(name), "A summary.", 5, "action-needed", "Reply", "claude-opus-5-5", "2026-11-02T11:00:00+00:00")
        c.commit()
    finally:
        c.close()
    ai.responder = lambda req: (fa.answer({"narrative": "Lin is waiting.", "groups": []})
                                if "narrative" in ((req.schema or {}).get("properties") or {}) else None)
    page.goto("/next")
    page.locator(f'.bf-need[data-pk="{world.pk(receipt)}"]').get_by_role("button", name=re.compile("^Archive")).click()
    expect(page.locator("#toast")).to_have_text("Archived \u00b7 in the mailbox's Archive")
    expect(page.locator("#undoFloat")).to_contain_text("Archived")
    page.locator(f'.bf-need[data-pk="{world.pk(lunch)}"]').get_by_role("button", name=re.compile("^Delete")).click()
    expect(page.locator("#toast")).to_have_text("Deleted \u00b7 in the mailbox's Trash")
    expect(page.locator("#undoFloat")).to_contain_text("Deleted")
    clean(page)

def test_a_folder_opened_again_shows_what_its_catch_up_brings(page, app, world, monkeypatch):
    from gate import held_elsewhere
    from mailmind.bootstrap import mailbox
    monkeypatch.setattr(mailbox.folders, "CATCH_UP_WAIT", 0.1)
    page.goto(f"/next/m/{w.ADA}/INBOX")
    folder = lambda name: page.locator("#folderrail .frow", has_text=re.compile(f"^{name}"))
    rows = page.locator("#rows .row[data-pk]")
    folder("Archive").click()
    expect(rows).to_have_count(1)
    folder("Inbox").click()
    expect(page.locator("#listTitle")).to_have_text("Inbox")
    world.server.deliver(w.ADA, "Archive", w.variant("ada-inbox-03-newsletter.eml", Message_ID="<issue-219@example.test>"))
    monkeypatch.setattr(mailbox.folders, "CATCH_UP_EVERY", 0)
    with held_elsewhere(w.ADA):                                 # the catch-up waits behind a long op
        folder("Archive").click()
        expect(rows).to_have_count(1)
        monkeypatch.setattr(mailbox.folders, "CATCH_UP_EVERY", 60)   # that open was due one; the reads after it aren't
    expect(rows).to_have_count(2)
    clean(page)

def test_a_browser_gone_offline_still_reaches_its_own_server(page, app, world):
    page.goto(f"/next/m/{w.ADA}/INBOX")
    archive = page.locator("#folderrail .frow", has_text=re.compile("^Archive"))
    expect(archive).to_be_visible()
    page.evaluate("window.dispatchEvent(new Event('offline'))")  # the Wi-Fi went; the server is on this machine
    archive.click()
    expect(page.locator("#rows .row[data-pk]")).to_have_count(1)
    clean(page)

def test_a_tab_nobody_is_looking_at_counts_as_no_one_and_reads_what_it_missed_on_return(page, app, world, ai):
    from urllib.parse import urlsplit
    from mailmind.http.handler import _UI_QUIET
    page.pw.add_init_script(AWAY)
    with page.pw.expect_response("**/api/digest*"):
        page.goto(f"/next/m/{w.ADA}/Archive")
    expect(page.frame_locator("iframe.rd-iframe").locator("body")).to_contain_text("log cabin")   # the folder opened its first message
    page.evaluate("window.away = true")
    asked = []
    page.on("request", lambda r: asked.append(urlsplit(r.url).path))
    sse.publish("sync-done", {"account": w.ADA})
    sse.publish("folder-synced", {"account": w.ADA, "folder": "Archive", "ok": True, "new": 1})
    sse.publish("new-mail", {"account": w.ADA, "folder": "INBOX", "count": 1})
    expect(page.pw).to_have_title("(1) MailMind")                # all three have arrived
    page.clock.run_for(61_000)                                  # the digest's delay, the headline's, and a minute of timers
    page.wait_for_timeout(300)
    assert [p for p in asked if p.startswith("/api/") and p not in _UI_QUIET] == []
    page.evaluate("window.away = false; document.dispatchEvent(new Event('visibilitychange', { bubbles: true }))")
    until(page, lambda: "/api/digest" in asked and "/api/messages" in asked, "what it missed was never read")
    page.clock.run_for(1_600)
    until(page, lambda: "/api/briefing/since" in asked, "the pill's line was never asked for")
    clean(page)

def test_a_digest_that_wont_come_says_the_server_cant_be_reached_and_offers_retry(page, app, world):
    page.route("**/api/digest*", lambda route: route.abort())
    page.goto("/next")
    expect(page.locator("#rows")).to_contain_text("Couldn\u2019t reach the server.")
    page.unroute("**/api/digest*")
    page.locator("#dgRetry").click()
    expect(page.locator("#rows")).to_contain_text("Today\u2019s brief")
    assert all("ERR_FAILED" in p for p in page.problems), page.problems
    page.problems.clear()
    assert page.blocked == [], page.blocked


def test_a_digest_that_wont_come_is_tried_again_by_itself(page, app, world):
    page.route("**/api/digest*", lambda route: route.abort())
    page.goto("/next")
    expect(page.locator("#rows")).to_contain_text("Couldn\u2019t reach the server.")
    page.unroute("**/api/digest*")
    page.clock.run_for(5_100)
    expect(page.locator("#rows")).to_contain_text("Today\u2019s brief")
    page.problems.clear()
    assert page.blocked == [], page.blocked

def test_a_list_or_a_search_that_fails_says_so_in_the_pages_words(page, app, world):
    page.goto(f"/next/m/{w.ADA}/INBOX")
    archive = page.locator("#folderrail .frow", has_text=re.compile("^Archive"))
    expect(archive).to_be_visible()
    page.route("**/api/messages*", lambda route: route.abort())
    archive.click()
    expect(page.locator("#rows")).to_contain_text("Couldn\u2019t load messages.")
    page.unroute("**/api/messages*")
    page.locator("#rows .ai-rerun").click()
    expect(page.locator("#rows .row[data-pk]")).to_have_count(1)
    page.route("**/api/search*", lambda route: route.abort())
    page.locator("#search").fill("lunch")
    page.keyboard.press("Enter")
    expect(page.locator("#rows")).to_contain_text("Search failed.")
    assert all("ERR_FAILED" in p for p in page.problems), page.problems
    page.problems.clear()
    assert page.blocked == [], page.blocked

def test_the_question_mark_opens_help_over_settings_and_escape_comes_back(page, world):
    opened(page)
    page.locator("#settingsBtn").click()
    title = page.get_by_role("heading", name="Email connections")
    expect(title).to_be_visible()
    page.keyboard.press("?")
    keys = page.get_by_role("dialog", name="Keyboard")
    expect(keys).to_be_visible()
    page.keyboard.press("Escape")
    expect(keys).to_be_hidden()
    expect(title).to_be_visible()
    clean(page)

def test_a_custom_send_time_already_gone_says_pick_a_future_one(page, world):
    opened(page)
    page.locator("#composeBtn").click()
    page.locator("#cmpSchedule").click()
    page.locator("#slWhen").fill("2026-01-01T09:00")
    expect(page.locator("#toast")).to_have_text("Pick a future time")
    expect(page.locator("#slWhen")).to_be_visible()             # still choosing
    clean(page)


def test_a_hidden_tab_still_reads_what_its_meeting_reminder_needs(page):
    page.pw.add_init_script(AWAY)
    opened(page)
    page.evaluate("window.away = true")
    asked = []
    page.on("request", lambda r: asked.append(r.url) if "/api/upcoming" in r.url else None)
    page.clock.run_for(61_000)
    until(page, lambda: asked, "an invite that came while the tab was hidden would never remind anyone")
    clean(page)


def test_marked_search_results_mute_their_threads(page, world):
    opened(page)
    box = page.get_by_role("textbox", name="search")
    box.fill("from:lin")
    box.press("Enter")
    one = page.locator("#rows .row[data-pk]").first
    expect(one).to_be_visible()
    one.hover()
    one.locator(".cbx").click()
    page.locator(".bulkbar .bb").filter(has_text="Mute").click()
    expect(page.locator("#toast")).to_have_text("Muted 1 thread")
    clean(page)


def test_a_saved_view_can_be_removed_from_the_keyboard(page):
    page.pw.on("dialog", lambda d: d.accept("Lunch"))           # it asks what to call the view
    opened(page)
    box = page.get_by_role("textbox", name="search")
    box.fill("from:lin")
    box.press("Enter")
    page.locator("#clientCtx .ctx-save").click()
    saved = page.locator("#folderrail .vrow")                   # what a person saved, and only that, can go
    expect(saved).to_have_count(1)
    box.blur()
    page.keyboard.press("[")                                    # a narrow rail shows only icons, and no way to remove a view
    remove = page.get_by_role("button", name="Remove view")
    remove.focus()
    expect(remove).to_be_focused()
    page.keyboard.press("Enter")
    expect(saved).to_have_count(0)
    clean(page)


# -- Esc backs out one step, which the owner chose over the page's on 2026-11-07 --------------------

def test_escape_closes_a_message_before_it_leaves_the_search_it_was_opened_from(page, world):
    opened(page)
    box = page.get_by_role("textbox", name="Search")
    box.fill("lunch")
    box.press("Enter")
    expect(page.pw).to_have_url(re.compile("search"))
    page.get_by_role("option").first.click()
    expect(page.locator("#bodyframe, iframe").first).to_be_visible()
    box.blur()
    page.keyboard.press("Escape")                               # the message, and the search stays
    expect(page.locator("#bodyframe, iframe")).to_have_count(0)
    expect(page.pw).to_have_url(re.compile("search"))
    expect(page.get_by_role("option").first).to_be_visible()
    page.keyboard.press("Escape")                               # then the search
    expect(page.pw).not_to_have_url(re.compile("search"))
    clean(page)


def test_escape_closes_a_message_on_a_desktop(page, world):
    opened(page)
    expect(page.locator("#bodyframe, iframe")).to_have_count(0)
    page.get_by_role("option").first.click()
    expect(page.locator("#bodyframe, iframe").first).to_be_visible()
    page.keyboard.press("Escape")
    expect(page.locator("#bodyframe, iframe")).to_have_count(0)
    expect(page.get_by_role("option").first).to_be_visible()
    clean(page)


def test_escape_in_the_search_box_clears_the_search_and_goes_home(page, world):
    opened(page)
    box = page.get_by_role("textbox", name="Search")
    box.fill("lunch")
    box.press("Enter")
    expect(page.pw).to_have_url(re.compile("search"))
    box.focus()
    box.press("Escape")
    expect(page.pw).not_to_have_url(re.compile("search"))
    expect(box).to_have_value("")
    expect(page.get_by_role("option").first).to_be_visible()
    clean(page)
