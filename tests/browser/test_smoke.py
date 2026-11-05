"""Smoke tests: dashboard.html in Chromium, on the app fixture's server over the fake IMAP world.
Each test drives one everyday flow with the keyboard and the mouse as a person would, then checks
that the page and the servers behind it (the fake IMAP server, the outbox, the fake SMTP server)
agree on what happened.

Locators prefer roles and accessible names, so most of these can follow the frontend into its next
form. The message list and the folder rail have no roles yet, so rows are found by data-pk and
folders by data-folder.

`test_accessibility_does_not_get_worse` runs axe on three screens and compares what it finds with
tests/golden/browser/axe_baseline.json, rule by rule. More findings fail; fewer fail too, until
`--update-golden` lowers the baseline, so it only ratchets down.

Run with `just test-full` (pytest --browser).
"""
import json
import os
import re
from datetime import timedelta

import pytest
from playwright.sync_api import expect

from fakes import anthropic_api as fa
from fixtures import world as w

pytestmark = pytest.mark.browser

RECEIPT = "ada-inbox-05-receipt.eml"
LUNCH = ("ada-inbox-01-lunch.eml", "ada-inbox-02-lunch-confirm.eml")
DRAFT = "ada-drafts-02-thunderbird-v2.eml"
BASELINE = os.path.join(os.path.dirname(__file__), "..", "golden", "browser", "axe_baseline.json")


def opened(page):
    """Load the app and wait until Ada's inbox list is on screen."""
    page.goto("/")
    page.get_by_role("tab", name=re.compile("^Everything")).click()
    expect(page.locator("#rows .row[data-pk]").first).to_be_visible()
    return page


def row(page, world, name):
    """The list row for corpus message `name`, by its pk now: take it before the message moves."""
    pk = world.pk(name)
    assert pk is not None, f"{name} has no row in the database"
    return page.locator(f'#rows .row[data-pk="{pk}"]')


def rows_titled(page, text):
    return page.locator("#rows .row[data-pk]").filter(has_text=text)


def folder(page, name):
    return page.locator(f'#folderrail .frow[data-folder="{name}"]')


def mailbox(world, name):
    return world.where(name)["server"][0][1]


def until(page, check, what):
    """Wait up to five seconds for `check()` to hold: the page shows a change as soon as it sends
    the request, before the server has done it."""
    for _ in range(100):
        if check():
            return
        page.wait_for_timeout(50)
    assert check(), what


def mailboxes(world, names):
    return [mailbox(world, n) for n in names]


def switch_account(page, acct):
    page.locator("#acctSw").click()
    page.locator(f'#acctMenu [role="menuitemradio"][data-acct="{acct}"]').click()


def clean(page):
    assert page.problems == []
    assert {u.split("/")[2] for u in page.blocked} <= {"fonts.googleapis.com", "fonts.gstatic.com"}


def compose(page, to, subject, body):
    page.keyboard.press("c")
    page.get_by_role("textbox", name="name@example.com").fill(to)
    page.get_by_role("textbox", name="subject").fill(subject)
    page.get_by_role("textbox", name="message body").fill(body)
    page.get_by_role("button", name="Send ▸").click()


def let_undo_pass(page):
    """Wait out the page's ten-second Undo Send window on the page's clock."""
    expect(page.locator("#sendFloat")).to_contain_text("Sending in 10s")
    page.clock.run_for(11_000)
    expect(page.locator("#sendFloat")).to_contain_text("Sent")


def due(app):
    """Move the server clock past the outbox's send delay and run one outbox pass."""
    import outbox
    app.clock.shift(timedelta(seconds=outbox.SEND_DELAY + 1))
    outbox.drain()


# ── reading and filing ──────────────────────────────────────────────────

def test_the_inbox_loads_without_errors(page, world):
    opened(page)
    expect(row(page, world, RECEIPT)).to_contain_text("Your receipt for order 1042")
    clean(page)


def test_a_script_the_page_didnt_ship_breaks_its_policy(page, world):
    """The page's own scripts run under its content security policy without a word; an event
    handler that gets in later is reported. The policy is report-only for now, so it still runs."""
    opened(page)
    clean(page)
    page.evaluate("""() => document.body.insertAdjacentHTML("beforeend",
        '<img hidden src="data:," onerror="document.body.dataset.injected = 1">')""")
    expect(page.locator("body")).to_have_attribute("data-injected", "1")
    assert page.problems, "the policy said nothing"             # Chromium may say it more than once
    assert all("inline event handler violates" in m and "script-src" in m for m in page.problems), page.problems


def test_archive_from_the_list_then_undo(page, world):
    opened(page)
    receipt = row(page, world, RECEIPT)                   # the pk, taken while the row exists
    receipt.click()
    page.keyboard.press("e")
    expect(receipt).to_have_count(0)
    until(page, lambda: mailbox(world, RECEIPT) == "Archive", "archived on the server")
    page.keyboard.press("z")
    expect(rows_titled(page, "Your receipt for order 1042")).to_have_count(1)
    until(page, lambda: mailbox(world, RECEIPT) == "INBOX", "back in the inbox on the server")
    clean(page)


def test_read_only_says_so_and_what_it_files_waits(page, world, monkeypatch):
    monkeypatch.delenv("MAILMIND_NOSYNC", raising=False)
    monkeypatch.setenv("MAILMIND_PROFILE", "read-only")
    opened(page)
    expect(page.locator("#statusTxt")).to_have_text("Read-only")
    since = world.mark()
    receipt = row(page, world, RECEIPT)
    receipt.click()
    page.keyboard.press("e")
    expect(receipt).to_have_count(0)
    expect(page.locator("#toast")).to_have_text("Archived · waits for a full run")
    assert world.writes(since) == [] and mailbox(world, RECEIPT) == "INBOX"
    page.locator("#statusBtn").click()
    expect(page.locator("#statusMenu")).to_contain_text("MailMind is running read-only")
    clean(page)


def test_an_undo_in_another_tab_brings_the_row_back(page, app, world):
    opened(page)
    receipt = row(page, world, RECEIPT)
    receipt.click()
    page.keyboard.press("e")
    expect(receipt).to_have_count(0)
    until(page, lambda: mailbox(world, RECEIPT) == "Archive", "archived on the server")
    assert app.post("/api/undo", {}).json["ok"]           # another tab's Undo: this one hears undo-done
    expect(rows_titled(page, "Your receipt for order 1042")).to_have_count(1)
    clean(page)


def test_mail_deleted_elsewhere_leaves_an_open_folder_list(page, world):
    import accounts
    import server
    gone = "ada-archive-01-old-thread.eml"
    opened(page)
    folder(page, "Archive").click()
    listed = row(page, world, gone)
    expect(listed).to_have_count(1)
    world.server.deliver(w.ADA, "Archive", w.variant("ada-inbox-03-newsletter.eml",
                                                     Message_ID="<issue-219@example.test>"))
    with world.server.lock:                                      # deleted in another client
        world.server.mailbox(w.ADA, "Archive").messages.pop(world.uid(gone))

    def pruned():                         # the ghost pruner's round, which yields while the page reads
        server._auto_reconcile_one(accounts.get(w.ADA))
        return world.pk(gone) is None
    until(page, pruned, "the ghost pruned")
    expect(listed).to_have_count(0)
    clean(page)


def test_archive_a_conversation_files_every_message_then_undo(page, world):
    opened(page)
    convo = rows_titled(page, "Lunch on Thursday?")
    expect(convo).to_have_count(1)                        # one row for the two inbox messages
    expect(convo.locator(".thr-count")).to_have_text("2")
    convo.click()
    page.keyboard.press("e")
    expect(convo).to_have_count(0)
    until(page, lambda: mailboxes(world, LUNCH) == ["Archive", "Archive"], "both archived on the server")
    assert mailbox(world, "ada-sent-01-lunch-reply.eml") == "Sent"   # only the anchor's folder files
    page.keyboard.press("z")
    expect(convo).to_have_count(1)
    until(page, lambda: mailboxes(world, LUNCH) == ["INBOX", "INBOX"], "both back on the server")
    clean(page)


def test_an_undo_that_restores_only_part_says_so_and_z_tries_the_rest(page, world):
    opened(page)
    convo = rows_titled(page, "Lunch on Thursday?")
    convo.click()
    page.keyboard.press("e")
    until(page, lambda: mailboxes(world, LUNCH) == ["Archive", "Archive"], "both archived on the server")
    world.server.fail("UID SEARCH", user=w.ADA)                 # the first one undo looks for isn't found
    page.keyboard.press("z")
    expect(page.locator("#toast")).to_have_text("1 couldn’t be restored — press z to try again")
    until(page, lambda: sorted(mailboxes(world, LUNCH)) == ["Archive", "INBOX"], "one back on the server")
    page.keyboard.press("z")
    until(page, lambda: mailboxes(world, LUNCH) == ["INBOX", "INBOX"], "both back on the server")
    expect(convo).to_have_count(1)
    clean(page)

def test_snooze_hides_the_message_until_it_is_due(page, world):
    import store
    opened(page)
    receipt = row(page, world, RECEIPT)
    receipt.click()
    page.keyboard.press("h")
    expect(page.get_by_role("menu")).to_be_visible()
    page.keyboard.press("1")                              # Later today
    expect(receipt).to_have_count(0)
    c = store.conn()
    try:
        wakes = c.execute("SELECT snoozed_until FROM messages WHERE pk=?", (world.pk(RECEIPT),)).fetchone()[0]
    finally:
        c.close()
    assert wakes is not None
    assert mailbox(world, RECEIPT) == "INBOX"             # snoozing is local; the server copy stays
    clean(page)


def test_the_rail_counts_a_message_just_snoozed(page, world):
    opened(page)
    expect(folder(page, "__snoozed__").locator(".unread")).to_have_count(0)
    receipt = row(page, world, RECEIPT)
    receipt.click()
    page.keyboard.press("h")
    page.keyboard.press("1")
    expect(receipt).to_have_count(0)
    expect(folder(page, "__snoozed__").locator(".unread")).to_have_text("1", timeout=3000)


def test_a_notification_shows_the_summary_it_has(page, world):
    # Triage skips junk, notifications included, so one has a summary only from before it was
    # reclassified. A stored summary shows whatever the bucket.
    import store
    ci = "ada-inbox-04-ci.eml"
    assert world.row(ci)["bucket"] == "automated-notifications"
    c = store.conn()
    try:
        store.save_summary(c, world.pk(ci), "Quilt's build 412 passed.", 2, "info", "", "test",
                           "2026-11-02T12:00:00+00:00")
        c.commit()
    finally:
        c.close()
    opened(page)
    notification = row(page, world, ci)
    expect(notification.locator(".gist")).to_contain_text("Quilt's build 412 passed.")
    notification.click()
    expect(page.locator("#aiCard")).to_contain_text("Quilt's build 412 passed.")
    clean(page)


def test_gmails_all_mail_is_named_so_and_g_a_opens_it(page, world):
    opened(page)
    switch_account(page, w.CY)
    all_mail = folder(page, "[Gmail]/All Mail")
    expect(all_mail).to_contain_text("All mail")
    page.locator("#rows").click()                         # keys go to the mailbox, not a field
    page.keyboard.press("g")
    page.keyboard.press("a")                              # Gmail has no Archive: it files into All Mail
    expect(all_mail).to_have_class(re.compile(r"\bon\b"))
    clean(page)


def test_a_changed_certificate_is_shown_in_settings_and_trusted_from_there(page, world):
    import config
    import server
    where = f"127.0.0.1:{world.server.port}"

    def bea(raw):
        return next(a for a in raw["accounts"] if a["email"] == w.BEA)
    raw = config._raw()
    bea(raw)["tls_pins"] = {where: "sha256:" + "0" * 64}
    config.save(raw)                                      # as if Bridge had been reinstalled
    server.sync_accounts({w.BEA})
    opened(page)
    page.locator("#settingsBtn").click()
    row = page.locator(f'.set-row[data-email="{w.BEA}"]')
    expect(row).to_contain_text("certificate changed")
    expect(row).to_contain_text("MailMind sent no password")
    row.get_by_role("button", name="Trust it").click()
    expect(row).to_contain_text("✓ synced", timeout=10_000)
    assert bea(config._raw())["tls_pins"] == {where: "sha256:" + world.server.cert_sha256}
    clean(page)


def test_search_finds_one_senders_mail(page, world):
    opened(page)
    page.keyboard.press("/")
    page.get_by_role("textbox", name="search").fill("from:lin")
    rows = page.locator("#rows .row[data-pk]")
    expect(rows.first).to_contain_text("Lin")
    texts = rows.all_inner_texts()
    assert texts and all("Lin" in t for t in texts)
    expect(rows_titled(page, "Your receipt for order 1042")).to_have_count(0)
    clean(page)


# ── sending ─────────────────────────────────────────────────────────────

def test_compose_send_undo_then_send(page, app, world):
    opened(page)
    compose(page, "lin@example.test", "Fabric order", "Hi Lin, the linen came in.")
    float_ = page.locator("#sendFloat")
    expect(float_).to_contain_text("Sending in")
    float_.get_by_role("button", name="Undo").click()
    expect(page.get_by_role("textbox", name="subject")).to_have_value("Fabric order")   # the composer is back
    expect(page.get_by_role("textbox", name="message body")).to_have_value(re.compile("the linen came in"))
    assert [o["state"] for o in app.get("/api/outbox").json["items"]] == []
    page.get_by_role("button", name="Send ▸").click()
    let_undo_pass(page)
    due(app)
    [sent] = world.smtp.messages
    assert sent.rcpts == ["lin@example.test"] and b"Subject: Fabric order" in sent.data
    clean(page)


def give_up_on_a_send(page, app, world):
    """Send a message the SMTP server keeps refusing, and run the outbox until it gives up."""
    import outbox
    world.smtp.fail("RCPT", "451 4.3.0 try again later", times=None)
    opened(page)
    compose(page, "lin@example.test", "Thread colours", "Which blue did you mean?")
    let_undo_pass(page)
    due(app)
    for _ in range(outbox.MAX_ATTEMPTS - 1):
        app.clock.shift(timedelta(seconds=outbox._BACKOFF_CAP))
        outbox.drain()
    assert [o["state"] for o in app.get("/api/outbox").json["items"]] == ["error"]
    return page.locator("#outboxBtn")


def test_a_failed_send_offers_retry_and_retry_sends(page, app, world):
    import outbox
    button = give_up_on_a_send(page, app, world)
    button.click()                                        # opening the outbox reloads it
    fly = page.locator("#outboxFly")
    expect(fly).to_contain_text("Thread colours")
    expect(fly).to_contain_text("recipient refused")
    world.smtp.clear_faults()
    fly.get_by_role("button", name="Retry").click()
    expect(fly).to_contain_text("queued")
    outbox.drain()
    [sent] = world.smtp.messages
    assert b"Subject: Thread colours" in sent.data
    expect(button).to_be_hidden()                         # a sent message leaves the outbox
    clean(page)


def test_the_page_hears_when_the_outbox_gives_up(page, app, world):
    button = give_up_on_a_send(page, app, world)
    expect(button).to_have_class(re.compile("has-fail"), timeout=3000)


def test_reopen_a_saved_draft(page, world):
    opened(page)
    switch_account(page, w.BEA)
    folder(page, "Drafts").click()
    draft = row(page, world, "bea-drafts-01-apple-mail.eml")
    expect(draft).to_contain_text("Re: Contract draft v2")
    draft.click()
    page.locator('#reader [data-ca="edit"]').click()
    expect(page.get_by_role("textbox", name="subject")).to_have_value("Re: Contract draft v2")
    expect(page.get_by_role("textbox", name="message body")).to_have_value(
        re.compile("my comments are in the attached file"))
    expect(page.locator("#cmpTo")).to_contain_text("ivo@harbor.example.test")
    clean(page)


def test_a_folder_whose_first_sync_fails_says_so_and_retry_reads_it(page, world):
    opened(page)
    world.server.deliver(w.ADA, "Junk", w.raw("ada-inbox-12-scam.eml"))
    world.server.fail("UID SEARCH", user=w.ADA)
    folder(page, "Junk").click()
    expect(page.locator("#rows")).to_contain_text("Couldn’t sync this folder.")
    page.get_by_role("button", name="Retry").click()
    expect(page.locator("#rows .row[data-pk]")).to_have_count(1)
    clean(page)


def test_a_folder_still_reading_fills_in_when_its_first_scan_ends(page, app, world, monkeypatch):
    import server
    from gate import held_elsewhere
    monkeypatch.setattr(server, "FIRST_SYNC_WAIT", 0.2)
    opened(page)
    world.server.deliver(w.ADA, "Junk", w.raw("ada-inbox-12-scam.eml"))
    with held_elsewhere(w.ADA):
        folder(page, "Junk").click()
        expect(page.locator("#rows")).to_contain_text("Reading this folder for the first time…")
    expect(page.locator("#rows .row[data-pk]")).to_have_count(1)
    clean(page)


def test_a_folder_opened_again_shows_what_its_catch_up_brings(page, app, world, monkeypatch):
    import server
    from gate import held_elsewhere
    monkeypatch.setattr(server, "CATCH_UP_WAIT", 0.1)
    opened(page)
    folder(page, "Archive").click()
    expect(page.locator("#rows .row[data-pk]")).to_have_count(1)
    folder(page, "INBOX").click()
    world.server.deliver(w.ADA, "Archive", w.variant("ada-inbox-03-newsletter.eml",
                                                     Message_ID="<issue-219@example.test>"))
    monkeypatch.setattr(server, "CATCH_UP_EVERY", 0)
    with held_elsewhere(w.ADA):                                  # the catch-up waits behind a long op
        folder(page, "Archive").click()
        expect(page.locator("#rows .row[data-pk]")).to_have_count(1)
    expect(page.locator("#rows .row[data-pk]")).to_have_count(2)
    clean(page)


@pytest.mark.xfail(strict=True, reason="two copies of one draft share a Message-ID, so the Drafts row "
                                       "opens as a conversation, whose reader has no Edit")
def test_a_draft_with_an_older_copy_still_opens_for_editing(page, world):
    opened(page)
    folder(page, "Drafts").click()
    draft = row(page, world, DRAFT)
    expect(draft).to_contain_text("Notes for the review")
    draft.click()
    page.locator('#reader [data-ca="edit"]').click(timeout=3000)
    expect(page.get_by_role("textbox", name="message body")).to_have_value(
        re.compile("the header colour fights the logo"))


# ── Ask and Do ──────────────────────────────────────────────────────────

def test_ask_answers_and_changes_nothing(page, world, ai):
    def reply(req):
        if req.schema and "answer" in (req.schema.get("properties") or {}):
            return fa.answer({"answer": "Lunch is on Thursday at noon.",
                              "cited_pks": [world.pk("ada-inbox-02-lunch-confirm.eml")]})
    ai.responder = reply
    opened(page)
    since = world.mark()
    page.keyboard.press("/")
    page.get_by_role("textbox", name="search").fill("?When is lunch with Lin?")
    expect(page.locator(".ask-answer")).to_have_text("Lunch is on Thursday at noon.")
    assert any("When is lunch with Lin?" in r.prompt for r in ai.requests)
    assert world.writes(since) == []
    clean(page)


def test_do_previews_and_moves_nothing_until_confirmed(page, world, ai):
    def reply(req):
        if req.schema and "verb" in (req.schema.get("properties") or {}):
            return fa.answer({"intent": "act", "filter": "from:lin", "verb": "archive"})
    ai.responder = reply
    opened(page)
    since = world.mark()
    page.keyboard.press("/")
    page.get_by_role("textbox", name="search").fill("!archive what Lin sent")
    plan = page.locator("#doPanel .do-plan")
    expect(plan).to_contain_text("from:lin")
    expect(plan.get_by_role("button", name=re.compile("^Archive"))).to_be_visible()
    assert world.writes(since) == []                      # the preview moved nothing
    plan.get_by_role("button", name="Cancel").click()
    expect(plan).to_have_count(0)
    assert world.writes(since) == []
    assert mailboxes(world, LUNCH) == ["INBOX", "INBOX"]
    clean(page)


def test_do_confirmed_archives_then_undo(page, world, ai):
    def reply(req):
        if req.schema and "verb" in (req.schema.get("properties") or {}):
            return fa.answer({"intent": "act", "filter": "from:lin", "verb": "archive"})
    ai.responder = reply
    opened(page)
    page.keyboard.press("/")
    page.get_by_role("textbox", name="search").fill("!archive what Lin sent")
    plan = page.locator("#doPanel .do-plan")
    plan.get_by_role("button", name=re.compile("^Archive")).click()
    expect(plan).to_have_count(0)
    expect(rows_titled(page, "Lunch on Thursday?")).to_have_count(0)
    until(page, lambda: mailboxes(world, LUNCH) == ["Archive", "Archive"], "both archived on the server")
    page.locator("#undoFloat").get_by_role("button", name=re.compile("^Undo")).click()   # focus is in the search box
    expect(rows_titled(page, "Lunch on Thursday?")).not_to_have_count(0)
    until(page, lambda: mailboxes(world, LUNCH) == ["INBOX", "INBOX"], "both back on the server")
    clean(page)


def test_the_sync_menu_says_what_ai_cost_today(page, world, ai):
    import time
    from mailmind.assistant.application.gateway import local_day
    from mailmind.assistant.application.ports import Spend, Usage
    from mailmind.bootstrap import assistant
    now = time.time()                                  # the server's frozen clock
    assistant.LEDGER.record(Spend(local_day(now), "triage", "claude-opus-5-5", "claude-opus-5-5", "ok",
                                  Usage(input_tokens=3000, output_tokens=200), 16_000, int(now)))
    opened(page)
    page.locator("#statusBtn").click()
    expect(page.get_by_role("dialog", name="Sync status")).to_contain_text(
        "Claude API · claude-opus-5-5 · $0.02 of $15.00 today")
    clean(page)


def spent_the_daily_limit():
    import time
    from mailmind.assistant.application.gateway import local_day
    from mailmind.assistant.application.ports import Spend, Usage
    from mailmind.bootstrap import assistant
    now = time.time()                                  # the server's frozen clock
    assistant.LEDGER.record(Spend(local_day(now), "ask", "claude-opus-5-5", "claude-opus-5-5", "ok",
                                  Usage(), 15_000_000, int(now)))           # the default limit, $15


def lunch_needs_a_reply(world):
    """Lin's lunch message, triaged as needing a reply, so the brief has something to say."""
    import store
    c = store.conn()
    try:
        store.save_summary(c, world.pk(LUNCH[1]), "Lin confirms lunch on Thursday.", 5, "action-needed",
                           "Reply", "claude-opus-5-5", "2026-11-02T11:00:00+00:00")
        c.commit()
    finally:
        c.close()


BRIEF = "Lin is waiting to hear back about lunch on Thursday."


def brief_replies(ai):
    ai.responder = lambda req: (fa.answer({"narrative": BRIEF, "groups": []})
                                if "narrative" in ((req.schema or {}).get("properties") or {}) else None)


def briefs(ai):
    return [r for r in ai.requests if "narrative" in ((r.schema or {}).get("properties") or {})]


def test_the_brief_is_written_once_when_the_inbox_opens(page, world, ai):
    lunch_needs_a_reply(world)
    brief_replies(ai)
    page.goto("/")
    expect(page.locator(".bf-narr")).to_have_text(BRIEF)
    page.reload()
    expect(page.locator(".bf-narr")).to_have_text(BRIEF)
    assert len(briefs(ai)) == 1                        # the second load read what the first wrote
    clean(page)


def test_at_the_daily_limit_the_brief_waits_for_a_click(page, world, ai):
    lunch_needs_a_reply(world)
    spent_the_daily_limit()
    brief_replies(ai)
    page.goto("/")
    expect(page.locator(".bf-wait")).to_contain_text("Today’s AI limit is reached, so the brief isn’t written yet.")
    ask = page.get_by_role("alertdialog", name="Today’s AI limit is reached")
    expect(ask).to_be_hidden()                         # opening the app asks nothing
    assert briefs(ai) == []
    page.get_by_role("button", name="Write it").click()
    ask.get_by_role("button", name="Go over the limit").click()
    expect(page.locator(".bf-narr")).to_have_text(BRIEF)
    assert len(briefs(ai)) == 1
    assert all("status of 402" in p for p in page.problems)
    page.problems.clear()
    clean(page)


def test_with_ai_off_the_brief_and_the_review_say_so_and_ask_for_nothing(page, world, anthropic_api):
    lunch_needs_a_reply(world)
    page.goto("/")
    expect(page.locator(".bf-aioff")).to_contain_text("AI is off.")
    page.get_by_role("button", name="Your week").click()
    expect(page.locator("#rvBody")).to_have_text(
        "AI is off, so there is no review: Claude writes it, and MailMind has no Anthropic API key.")
    assert anthropic_api.requests == []
    assert all("status of 409" in p for p in page.problems)    # the review it was asked for
    assert len(page.problems) == 1                             # and nothing the page asked for itself
    page.problems.clear()
    clean(page)


def test_at_the_daily_limit_an_ask_goes_only_when_you_say_so(page, world, ai):
    spent_the_daily_limit()
    ai.responder = lambda req: fa.answer({"answer": "Lunch is on Thursday at noon.", "cited_pks": []})
    opened(page)
    ask = page.get_by_role("alertdialog", name="Today’s AI limit is reached")
    page.keyboard.press("/")
    page.get_by_role("textbox", name="search").fill("?When is lunch with Lin?")
    expect(ask).to_contain_text("MailMind has spent $15.00 on AI today, and its daily limit is $15.00.")
    expect(ask.get_by_role("button", name="Not now")).to_be_focused()
    ask.get_by_role("button", name="Not now").click()
    expect(ask).to_be_hidden()
    expect(page.locator("#rows")).to_contain_text("ask failed")
    assert ai.requests == []
    page.get_by_role("textbox", name="search").fill("?When is lunch with Lin, again?")
    ask.get_by_role("button", name="Go over the limit").click()
    expect(page.locator(".ask-answer")).to_have_text("Lunch is on Thursday at noon.")
    assert [r.prompt.count("When is lunch with Lin, again?") for r in ai.requests] == [1]
    assert all("status of 402" in p for p in page.problems)    # the two refusals the page asked about
    page.problems.clear()
    clean(page)


# ── accessibility ───────────────────────────────────────────────────────

def axe(page):
    """axe's violations on the page as {rule: {"impact": ..., "nodes": count}}. Mail bodies are
    left out: they are the sender's markup, in frames axe isn't injected into."""
    from axe_playwright_python.sync_playwright import Axe
    found = Axe().run(page.pw, options={"resultTypes": ["violations"], "iframes": False}).response
    return {v["id"]: {"impact": v["impact"], "nodes": len(v["nodes"])} for v in found["violations"]}


def test_accessibility_does_not_get_worse(page, world, request):
    opened(page)
    got = {"inbox": axe(page)}
    row(page, world, RECEIPT).click()
    expect(page.locator("#reader iframe.rd-iframe")).to_be_attached()
    got["reading"] = axe(page)
    page.keyboard.press("c")
    expect(page.get_by_role("textbox", name="subject")).to_be_visible()
    got["composing"] = axe(page)
    if request.config.getoption("--update-golden"):
        os.makedirs(os.path.dirname(BASELINE), exist_ok=True)
        with open(BASELINE, "w", encoding="utf-8") as f:
            json.dump(got, f, indent=2, sort_keys=True)
            f.write("\n")
    assert os.path.exists(BASELINE), "no axe baseline; run pytest --browser --update-golden"
    with open(BASELINE, encoding="utf-8") as f:
        want = json.load(f)
    worse = {s: {r: v for r, v in got[s].items() if v["nodes"] > want.get(s, {}).get(r, {"nodes": 0})["nodes"]}
             for s in got}
    better = {s: {r: v for r, v in want.get(s, {}).items() if v["nodes"] > got[s].get(r, {"nodes": 0})["nodes"]}
              for s in got}
    assert not any(worse.values()), f"new accessibility findings: {worse}"
    assert not any(better.values()), f"fewer findings than the baseline, so lower it with --update-golden: {better}"
