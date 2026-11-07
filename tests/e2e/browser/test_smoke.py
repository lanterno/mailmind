"""Smoke tests: the page in Chromium, on the app fixture's server over the fake IMAP world.
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
from mailmind.bootstrap.assistant import answers
from mailmind.bootstrap import mailbox
from mailmind.platform import perimeter

pytestmark = pytest.mark.browser

RECEIPT = "ada-inbox-05-receipt.eml"
LUNCH = ("ada-inbox-01-lunch.eml", "ada-inbox-02-lunch-confirm.eml")
DRAFT = "ada-drafts-02-thunderbird-v2.eml"
BASELINE = os.path.join(os.path.dirname(__file__), "..", "..", "golden", "browser", "axe_baseline.json")


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


def box_of(world, name):
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
    return [box_of(world, n) for n in names]


def switch_account(page, acct):
    page.locator("#acctSw").click()
    page.locator(f'#acctMenu [role="menuitemradio"][data-acct="{acct}"]').click()


def clean(page):
    assert page.problems == []
    assert page.blocked == []


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
    from mailmind.bootstrap.compose import outbox
    app.clock.shift(timedelta(seconds=outbox.SEND_DELAY + 1))
    outbox.drain()


# ── reading and filing ──────────────────────────────────────────────────

def test_the_inbox_loads_without_errors(page, world):
    opened(page)
    expect(row(page, world, RECEIPT)).to_contain_text("Your receipt for order 1042")
    clean(page)


def test_a_script_the_page_didnt_ship_breaks_its_policy(page, world):
    """The page's own scripts run under its content security policy without a word; an event
    handler that gets in later doesn't run, and the browser says so."""
    opened(page)
    clean(page)
    page.evaluate("""() => document.body.insertAdjacentHTML("beforeend",
        '<img hidden src="data:," onerror="document.body.dataset.injected = 1">')""")
    until(page, lambda: page.problems, "the policy said nothing")   # Chromium may say it more than once
    assert all("inline event handler violates" in m and "script-src" in m for m in page.problems), page.problems
    assert page.evaluate("() => document.body.dataset.injected") is None
    page.problems.clear()


def test_a_script_block_the_page_didnt_ship_doesnt_run_either(page, world):
    opened(page)
    clean(page)
    page.evaluate("""() => {
        const s = document.createElement("script");
        s.textContent = "document.body.dataset.injected = 1";
        document.body.appendChild(s);
    }""")
    until(page, lambda: page.problems, "the policy said nothing")
    assert page.evaluate("() => document.body.dataset.injected") is None
    page.problems.clear()


def test_a_mail_frame_stays_a_srcdoc_under_the_pages_policy(page, world):
    """The React app's frame loads a URL. This page's doesn't, and its policy still lets asked-for images in."""
    tracker = "https://tracker.example.test/open.png"
    uid = world.deliver(w.ADA, "INBOX", (
        "From: Mallory <mallory@evil.example.test>\r\nTo: Ada Park <ada@example.test>\r\nSubject: Tracked\r\n"
        "Date: Sun, 01 Nov 2026 09:00:00 +0000\r\nMessage-ID: <tracked@evil.example.test>\r\nMIME-Version: 1.0\r\n"
        f'Content-Type: text/html; charset=utf-8\r\n\r\n<p>Hello</p><img src="{tracker}" alt="tracker">').encode())
    documents = []
    page.pw.on("request", lambda r: documents.append(r.url) if "/body.html" in r.url else None)
    answer = page.goto("/")
    assert answer.header_value("content-security-policy") == perimeter.page_policy()
    assert "https:" in answer.header_value("content-security-policy")
    page.get_by_role("tab", name=re.compile("^Everything")).click()
    c = mailbox.store.conn()
    pk = c.execute("SELECT pk FROM messages WHERE account=? AND folder='INBOX' AND uid=?", (w.ADA, uid)).fetchone()["pk"]
    c.close()
    page.locator(f'#rows .row[data-pk="{pk}"]').click()
    frame = page.locator("iframe.rd-iframe")
    expect(frame).to_be_visible()
    assert frame.get_attribute("src") is None
    assert "default-src 'none'" in frame.get_attribute("srcdoc") and tracker not in frame.get_attribute("srcdoc")
    clean(page)
    page.get_by_role("button", name="Load images").click()
    until(page, lambda: page.blocked == [tracker], "the image was never asked for")
    assert tracker in frame.get_attribute("srcdoc")
    assert documents == []
    page.blocked.clear()
    clean(page)


def test_archive_from_the_list_then_undo(page, world):
    opened(page)
    receipt = row(page, world, RECEIPT)                   # the pk, taken while the row exists
    receipt.click()
    page.keyboard.press("e")
    expect(receipt).to_have_count(0)
    until(page, lambda: box_of(world, RECEIPT) == "Archive", "archived on the server")
    page.keyboard.press("z")
    expect(rows_titled(page, "Your receipt for order 1042")).to_have_count(1)
    until(page, lambda: box_of(world, RECEIPT) == "INBOX", "back in the inbox on the server")
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
    assert world.writes(since) == [] and box_of(world, RECEIPT) == "INBOX"
    page.locator("#statusBtn").click()
    expect(page.locator("#statusMenu")).to_contain_text("MailMind is running read-only")
    clean(page)


def test_an_undo_in_another_tab_brings_the_row_back(page, app, world):
    opened(page)
    receipt = row(page, world, RECEIPT)
    receipt.click()
    page.keyboard.press("e")
    expect(receipt).to_have_count(0)
    until(page, lambda: box_of(world, RECEIPT) == "Archive", "archived on the server")
    assert app.post("/api/undo", {}).json["ok"]           # another tab's Undo: this one hears undo-done
    expect(rows_titled(page, "Your receipt for order 1042")).to_have_count(1)
    clean(page)


def test_mail_deleted_elsewhere_leaves_an_open_folder_list(page, world):
    from mailmind.bootstrap import accounts
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
        mailbox.syncing._auto_reconcile_one(accounts.get(w.ADA))
        return world.pk(gone) is None
    until(page, pruned, "the ghost pruned")
    expect(listed).to_have_count(0)
    clean(page)


def test_mail_marked_unread_elsewhere_shows_on_an_open_list(page, world):
    from mailmind.bootstrap import accounts
    from mailmind.bootstrap.mailbox import ingest
    name = "ada-archive-01-old-thread.eml"
    opened(page)
    folder(page, "Archive").click()
    listed = row(page, world, name)
    expect(listed).to_have_count(1)
    assert "unread" not in listed.get_attribute("class")
    with world.server.lock:
        world.server.mailbox(w.ADA, "Archive").messages[world.uid(name)].flags.discard(w.SEEN)
    ingest.sync_folder(accounts.get(w.ADA), "Archive")
    until(page, lambda: "unread" in (row(page, world, name).get_attribute("class") or ""), "the row marked unread")
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
    assert box_of(world, "ada-sent-01-lunch-reply.eml") == "Sent"   # only the anchor's folder files
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
    from mailmind.bootstrap.mailbox import store
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
    assert box_of(world, RECEIPT) == "INBOX"             # snoozing is local; the server copy stays
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
    from mailmind.bootstrap.mailbox import store
    ci = "ada-inbox-04-ci.eml"
    assert world.row(ci)["bucket"] == "automated-notifications"
    c = store.conn()
    try:
        answers.save_summary(c, world.pk(ci), "Quilt's build 412 passed.", 2, "info", "", "test",
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
    from mailmind.bootstrap.accounts import config
    where = f"127.0.0.1:{world.server.port}"

    def bea(raw):
        return next(a for a in raw["accounts"] if a["email"] == w.BEA)
    raw = config._raw()
    bea(raw)["tls_pins"] = {where: "sha256:" + "0" * 64}
    config.save(raw)                                      # as if Bridge had been reinstalled
    mailbox.syncing.sync_accounts({w.BEA})
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
    from mailmind.bootstrap.compose import outbox
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


def test_a_send_that_failed_says_its_retrying_and_the_tray_tries_it_now(page, app, world):
    from mailmind.bootstrap.compose import outbox
    opened(page)
    compose(page, "lin@example.test", "Thread colours", "Which blue did you mean?")
    let_undo_pass(page)
    world.smtp.fail("MAIL")
    due(app)
    page.locator("#outboxBtn").click()
    fly = page.locator("#outboxFly")
    expect(fly).to_contain_text("retrying")
    expect(fly.get_by_role("button", name="Cancel")).to_be_visible()
    fly.get_by_role("button", name="Try now").click()
    until(page, lambda: not app.get("/api/outbox").json["items"][0]["retrying"], "it's asked for now")
    outbox.drain()
    assert len(world.smtp.messages) == 1


def test_a_send_with_no_answer_says_it_may_have_gone_and_only_a_person_sends_it_again(page, app, world):
    from mailmind.bootstrap.compose import outbox
    world.smtp.drop_after_data()
    opened(page)
    compose(page, "lin@example.test", "Thread colours", "Which blue did you mean?")
    let_undo_pass(page)
    due(app)
    assert len(world.smtp.messages) == 1
    button = page.locator("#outboxBtn")
    expect(button).to_be_visible()
    button.click()
    fly = page.locator("#outboxFly")
    expect(fly).to_contain_text("May have gone out")
    expect(fly).to_contain_text("unsure")
    app.clock.shift(timedelta(seconds=outbox._BACKOFF_CAP + 1))
    outbox.drain()
    assert len(world.smtp.messages) == 1
    fly.get_by_role("button", name="Send again").click()
    until(page, lambda: app.get("/api/outbox").json["items"][0]["state"] == "queued", "queued again")
    outbox.drain()
    assert len(world.smtp.messages) == 2
    expect(button).to_be_hidden()
    clean(page)


def test_a_change_that_cant_reach_the_server_waits_in_the_tray_and_discard_puts_it_back(page, app, world):
    from mailmind.bootstrap.mailbox import commands
    opened(page)
    receipt = row(page, world, RECEIPT)
    receipt.click()
    world.server.fail("UID MOVE", user=w.ADA, times=None)
    world.server.fail("UID COPY", user=w.ADA, times=None)
    world.server.drop("SELECT", user=w.ADA, times=None)
    page.keyboard.press("e")
    expect(receipt).to_have_count(0)
    button = page.locator("#outboxBtn")
    expect(button).to_be_visible()                        # the queueing told this tab
    button.click()
    fly = page.locator("#outboxFly")
    expect(fly).to_contain_text("Archive: Your receipt for order 1042")
    expect(fly).to_contain_text("waiting")
    world.server.clear_faults()
    world.server.fail("UID MOVE", user=w.ADA, times=None)
    world.server.fail("UID COPY", user=w.ADA, times=None)
    commands.drain()
    expect(fly).to_contain_text("stuck")                  # told by the event, with the panel open
    expect(fly).to_contain_text("the server refused it")
    expect(receipt).to_have_count(1)                      # it's listed again, as the server has it
    world.server.clear_faults()
    fly.get_by_role("button", name="Discard").click()
    expect(button).to_be_hidden()
    expect(receipt).to_have_count(1)
    assert app.get("/api/pending").json["items"] == []
    assert box_of(world, RECEIPT) == "INBOX"
    clean(page)


def test_retry_in_the_tray_sends_the_change_again(page, app, world):
    from mailmind.bootstrap.mailbox import commands
    opened(page)
    receipt = row(page, world, RECEIPT)
    receipt.click()
    world.server.drop("SELECT", user=w.ADA, times=None)
    page.keyboard.press("e")
    expect(receipt).to_have_count(0)
    world.server.clear_faults()
    world.server.fail("UID MOVE", user=w.ADA, times=1)
    world.server.fail("UID COPY", user=w.ADA, times=1)
    commands.drain()
    page.locator("#outboxBtn").click()
    fly = page.locator("#outboxFly")
    expect(fly).to_contain_text("stuck")
    fly.get_by_role("button", name="Retry").click()
    expect(fly).to_contain_text("waiting")
    expect(receipt).to_have_count(0)
    commands.drain()
    until(page, lambda: box_of(world, RECEIPT) == "Archive", "archived on the server")
    expect(page.locator("#outboxBtn")).to_be_hidden()
    clean(page)


def test_a_failed_send_offers_retry_and_retry_sends(page, app, world):
    from mailmind.bootstrap.compose import outbox
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


def writing(page, to, subject, body):
    page.keyboard.press("c")
    page.get_by_role("textbox", name="name@example.com").fill(to)
    page.get_by_role("textbox", name="subject").fill(subject)
    page.get_by_role("textbox", name="message body").fill(body)


def copies(world, subject):
    """The copies in Ada's Drafts whose bytes carry `subject`."""
    with world.server.lock:
        return [m.raw for m in world.server.mailbox(w.ADA, "Drafts").messages.values() if subject.encode() in m.raw]


class Held:
    """The page's requests to `path` wait here until let go, as they would on a slow server."""

    def __init__(self, page, path):
        self.routes, self.holding = [], True
        page.route(f"**{path}", self._arrived)

    def _arrived(self, route):
        if self.holding:
            self.routes.append(route)
        else:
            route.fallback()

    def let_go(self):
        self.holding = False
        for r in self.routes:
            r.fallback()
        self.routes.clear()


def test_opening_the_composer_focuses_at_once_and_never_takes_focus_back(page, world):
    opened(page)
    page.evaluate("composeOpen({mode: 'new'})")
    assert page.evaluate("document.activeElement.classList.contains('ci')")   # keys typed now land in To
    page.keyboard.press("Escape")
    expect(page.get_by_role("textbox", name="subject")).to_be_hidden()
    assert page.evaluate("""() => { composeOpen({mode: 'new'}); document.querySelector('#cmpSubject').focus();
                                    return new Promise(done => setTimeout(() => done(document.activeElement.id), 120)); }""") == "cmpSubject"
    clean(page)


def test_a_message_whose_closing_save_fails_stays_a_click_away(page, world):
    opened(page)
    writing(page, "lin@example.test", "Thread colours", "Which blue did you mean?")
    page.pw.route("**/api/draft", lambda route: route.fulfill(status=500, body="{}"))   # a server away wouldn't do it
    page.keyboard.press("Escape")
    box, float_ = page.get_by_role("textbox", name="message body"), page.locator("#sendFloat")
    expect(float_).to_contain_text("Your message couldn’t be saved")
    expect(box).to_be_hidden()
    expect(page.locator("#srLive")).to_have_text("Your message couldn’t be saved")
    assert page.problems and all("500" in x for x in page.problems)   # the save's answer, as the console logs it
    page.problems.clear()
    page.pw.unroute("**/api/draft")
    float_.get_by_role("button", name="Edit").click()
    expect(box).to_have_value("Which blue did you mean?")
    expect(page.get_by_role("textbox", name="subject")).to_have_value("Thread colours")
    expect(float_).to_be_hidden()
    page.keyboard.press("Escape")                         # nothing else holds it, so closing it saves it
    until(page, lambda: len(copies(world, "Thread colours")) == 1, "the reopened message saved")
    clean(page)


def test_autosave_writes_it_down_here_and_the_server_gets_it_a_minute_on(page, app, world):
    from mailmind.bootstrap.compose import drafts
    opened(page)
    writing(page, "lin@example.test", "Thread colours", "Which blue did you mean?")
    page.clock.run_for(8_500)
    expect(page.locator("#cmpSaved")).to_contain_text("Saved")
    assert copies(world, "Thread colours") == []
    app.clock.shift(timedelta(minutes=2))
    drafts.push_round()
    assert len(copies(world, "Thread colours")) == 1
    clean(page)


def test_a_draft_the_server_wont_take_waits_in_the_tray_and_opens_from_it(page, app, world):
    from mailmind.bootstrap.compose import drafts
    opened(page)
    world.server.fail("APPEND", user=w.ADA, times=None)
    writing(page, "lin@example.test", "Thread colours", "Which blue did you mean?")
    page.keyboard.press("Escape")                         # closing saves: here, and the server says no
    button = page.locator("#outboxBtn")
    expect(button).to_be_visible()
    button.click()
    fly = page.locator("#outboxFly")
    expect(fly).to_contain_text("Drafts waiting")
    expect(fly).to_contain_text("Thread colours")
    fly.get_by_role("button", name="Edit").click()
    expect(page.get_by_role("textbox", name="message body")).to_have_value("Which blue did you mean?")
    page.keyboard.press("Escape")
    world.server.clear_faults()
    app.clock.shift(timedelta(minutes=2))
    drafts.push_round()
    assert len(copies(world, "Thread colours")) == 1
    expect(button).to_be_hidden()
    clean(page)


def test_typing_during_a_save_is_saved_when_the_composer_closes(page, world):
    opened(page)
    writing(page, "lin@example.test", "Thread colours", "Which blue")
    held = Held(page, "/api/draft")
    page.keyboard.press("Control+s")
    until(page, lambda: held.routes, "the save reached the network")
    page.get_by_role("textbox", name="message body").press_sequentially(" did you mean?")
    held.let_go()
    expect(page.locator("#cmpSaved")).to_contain_text("Saved")
    until(page, lambda: len(copies(world, "Thread colours")) == 1, "the first save landed")
    page.keyboard.press("Escape")
    until(page, lambda: any(b"did you mean?" in c for c in copies(world, "Thread colours")), "the typing saved")
    assert len(copies(world, "Thread colours")) == 1
    clean(page)


def test_removing_a_recipient_is_an_edit_a_close_saves(page, world):
    opened(page)
    writing(page, "lin@example.test, noor@example.test", "Thread colours", "Which blue did you mean?")
    page.keyboard.press("Control+s")
    until(page, lambda: len(copies(world, "Thread colours")) == 1, "the save landed")
    expect(page.locator("#cmpSaved")).to_contain_text("Saved")
    page.get_by_role("button", name=re.compile("^Remove noor@example.test")).click()
    page.keyboard.press("Escape")
    until(page, lambda: all(b"noor@" not in c for c in copies(world, "Thread colours")), "the removal saved")
    assert len(copies(world, "Thread colours")) == 1
    clean(page)


def test_saves_of_one_composer_go_in_turn(page, world):
    opened(page)
    writing(page, "lin@example.test", "Thread colours", "Which blue did you mean?")
    held = Held(page, "/api/draft")
    page.keyboard.press("Control+s")
    until(page, lambda: held.routes, "the first save reached the network")
    page.get_by_role("textbox", name="message body").press_sequentially(" The dark one?")
    page.keyboard.press("Control+s")
    page.wait_for_timeout(200)
    assert len(held.routes) == 1                          # the second waits for the first's answer
    asked = []
    page.on("request", lambda r: asked.append(r.post_data_json) if r.url.endswith("/api/draft") else None)
    held.let_go()
    until(page, lambda: asked, "the second save went")
    assert asked[0]["draft_id"]                           # and names the copy the first left
    until(page, lambda: any(b"The dark one?" in c for c in copies(world, "Thread colours")), "the second save landed")
    assert len(copies(world, "Thread colours")) == 1
    clean(page)


def test_a_send_during_a_first_save_leaves_no_copy_behind(page, app, world):
    opened(page)
    writing(page, "lin@example.test", "Thread colours", "Which blue did you mean?")
    held = Held(page, "/api/draft")
    page.keyboard.press("Control+s")
    until(page, lambda: held.routes, "the save reached the network")
    asked = []
    page.on("request", lambda r: asked.append(r.post_data_json) if r.url.endswith("/api/send") else None)
    page.get_by_role("button", name="Send ▸").click()
    page.wait_for_timeout(200)
    assert asked == []                                    # the send waits to learn which copy it replaces
    held.let_go()
    until(page, lambda: asked, "the send went")
    assert asked[0]["draft_id"]
    let_undo_pass(page)
    due(app)
    assert copies(world, "Thread colours") == []
    assert [m.rcpts for m in world.smtp.messages] == [["lin@example.test"]]
    clean(page)


def test_a_reply_saved_once_goes_out_after_its_originals_row_went(page, app, world):
    from mailmind.bootstrap.mailbox import store
    opened(page)
    pk = world.pk(LUNCH[1])
    page.evaluate(f"replyTo({pk},false)")
    box = page.get_by_role("textbox", name="message body")
    box.fill("See you there.")
    assert page.evaluate("composeSaveDraft(true)") is True
    assert page.evaluate("!!state.compose.resume")              # it names its quote and threading itself now
    c = store.conn()
    store.delete_message(c, pk)
    c.commit()
    c.close()
    page.get_by_role("button", name="Send ▸").click()
    let_undo_pass(page)
    due(app)
    [sent] = world.smtp.messages
    assert b"In-Reply-To:" in sent.data and b"See you there." in sent.data
    clean(page)


def test_a_forward_saved_once_shows_its_files_as_its_own(page, world):
    opened(page)
    page.evaluate(f"forwardMsg({world.pk('ada-inbox-07-forward-rfc822.eml')})")
    quote = page.locator("#cmpQToggle")
    expect(quote).to_contain_text("attachment")
    page.get_by_role("textbox", name="message body").fill("For you.")
    assert page.evaluate("composeSaveDraft(true)") is True
    expect(page.locator("#cmpAttach .cmp-att")).to_have_count(1)
    expect(quote).not_to_contain_text("attachment")
    clean(page)


def test_a_message_reopened_by_undo_send_is_saved_when_closed(page, world):
    opened(page)
    compose(page, "lin@example.test", "Thread colours", "Which blue did you mean?")
    page.locator("#sfUndo").click()
    box = page.get_by_role("textbox", name="message body")
    expect(box).to_have_value("Which blue did you mean?")
    page.keyboard.press("Escape")
    until(page, lambda: len(copies(world, "Thread colours")) == 1, "the reopened message saved")
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
    from gate import held_elsewhere
    monkeypatch.setattr(mailbox.folders, "FIRST_SYNC_WAIT", 0.2)
    opened(page)
    world.server.deliver(w.ADA, "Junk", w.raw("ada-inbox-12-scam.eml"))
    with held_elsewhere(w.ADA):
        folder(page, "Junk").click()
        expect(page.locator("#rows")).to_contain_text("Reading this folder for the first time…")
    expect(page.locator("#rows .row[data-pk]")).to_have_count(1)
    clean(page)


def test_a_folder_opened_again_shows_what_its_catch_up_brings(page, app, world, monkeypatch):
    from gate import held_elsewhere
    monkeypatch.setattr(mailbox.folders, "CATCH_UP_WAIT", 0.1)
    opened(page)
    folder(page, "Archive").click()
    expect(page.locator("#rows .row[data-pk]")).to_have_count(1)
    folder(page, "INBOX").click()
    world.server.deliver(w.ADA, "Archive", w.variant("ada-inbox-03-newsletter.eml",
                                                     Message_ID="<issue-219@example.test>"))
    monkeypatch.setattr(mailbox.folders, "CATCH_UP_EVERY", 0)
    with held_elsewhere(w.ADA):                                  # the catch-up waits behind a long op
        folder(page, "Archive").click()
        expect(page.locator("#rows .row[data-pk]")).to_have_count(1)
    expect(page.locator("#rows .row[data-pk]")).to_have_count(2)
    clean(page)


def test_a_draft_replaced_or_discarded_leaves_the_list_at_once(page, world):
    opened(page)
    switch_account(page, w.BEA)
    folder(page, "Drafts").click()
    row(page, world, "bea-drafts-01-apple-mail.eml").click()
    page.locator('#reader [data-ca="edit"]').click()
    page.get_by_role("textbox", name="subject").fill("Re: Contract draft v3")
    page.keyboard.press("Escape")
    rows = page.locator("#rows")
    expect(rows).to_contain_text("Re: Contract draft v3")
    expect(rows).not_to_contain_text("Re: Contract draft v2")
    expect(page.locator("#reader")).to_contain_text("Re: Contract draft v3")   # no reader left on the copy that went
    page.locator('#reader [data-ca="edit"]').click()
    page.get_by_role("button", name="Discard").click()
    expect(rows).not_to_contain_text("Re: Contract draft")
    clean(page)


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


def do_plan_for(page, ai, flt, verb="archive"):
    def reply(req):
        if req.schema and "verb" in (req.schema.get("properties") or {}):
            return fa.answer({"intent": "act", "filter": flt, "verb": verb})
    ai.responder = reply
    opened(page)
    page.keyboard.press("/")
    page.get_by_role("textbox", name="search").fill("!do that")
    return page.locator("#doPanel .do-plan")


def test_do_files_what_its_preview_counted_and_not_the_rest_of_the_conversation(page, world, ai):
    plan = do_plan_for(page, ai, "from:lin@example.test in:INBOX subject:re")
    expect(plan).to_contain_text("1 message")
    plan.get_by_role("button", name=re.compile("^Archive 1")).click()
    until(page, lambda: box_of(world, LUNCH[0]) == "Archive" or box_of(world, LUNCH[1]) == "Archive", "one archived")
    expect(page.locator("#undoFloat")).to_contain_text("Archived 1")
    assert sorted(mailboxes(world, LUNCH)) == ["Archive", "INBOX"]
    clean(page)


def test_do_says_when_its_preview_has_expired_and_moves_nothing(page, world, ai, app):
    from datetime import timedelta
    plan = do_plan_for(page, ai, "from:lin")
    expect(plan.get_by_role("button", name=re.compile("^Archive"))).to_be_visible()
    since = world.mark()
    app.clock.shift(timedelta(minutes=11))
    plan.get_by_role("button", name=re.compile("^Archive")).click()
    expect(page.locator("#toast")).to_contain_text("expired")
    assert world.writes(since) == []
    assert mailboxes(world, LUNCH) == ["INBOX", "INBOX"]
    assert all("status of 410" in p for p in page.problems)      # the refusal itself
    page.problems.clear()
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
    from mailmind.bootstrap.mailbox import store
    c = store.conn()
    try:
        answers.save_summary(c, world.pk(LUNCH[1]), "Lin confirms lunch on Thursday.", 5, "action-needed",
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


def test_the_page_takes_what_a_verdict_asks_from_the_server(page, world, anthropic_api):
    from mailmind.bootstrap.mailbox import store
    c = store.conn()
    try:
        for name, importance, category, action in (
                (LUNCH[1], 5, "action-needed", "Reply to Lin"),         # needs you, and on the agenda
                ("ada-inbox-09-rfc2047-folded.eml", 1, "info", ""),     # safe to file
                ("ada-inbox-05-receipt.eml", 3, "finance", "")):        # neither
            answers.save_summary(c, world.pk(name), "A summary.", importance, category, action, "claude-opus-5-5",
                               "2026-11-02T11:00:00+00:00")
        c.commit()
    finally:
        c.close()
    page.goto("/")
    expect(page.locator('.bf-tally[data-brief="needs"]')).to_contain_text("1 needs you")
    expect(page.locator('.bf-tally[data-brief="commits"]')).to_contain_text("1 commitment")
    expect(page.locator(".bf-tidy")).to_contain_text("1 low-priority message the AI rates as safe to file")
    expect(page.locator("#rows .row")).to_have_count(1)                 # the Needs you tab
    clean(page)


def test_a_rule_changes_how_the_mail_it_covers_reads_at_once(page, world):
    news = "ada-inbox-03-newsletter.eml"
    opened(page)
    r = row(page, world, news)
    expect(r.locator(".jb")).to_be_visible()
    page.evaluate("openDrawer(true)")
    page.locator("#ruleInput").fill(world.row(news)["from_addr"])
    page.locator("#ruleInput").press("Enter")
    expect(r.locator(".jb")).to_have_count(0)
    clean(page)


def _hub_files(world, n):
    from mailmind.bootstrap.mailbox import store
    c = store.conn()
    try:
        for i in range(n):
            c.execute("INSERT INTO attachments(message_pk, part_id, filename, content_type, size, is_inline) "
                      "VALUES (?,?,?,'application/pdf',1000,0)", (world.pk(RECEIPT), f"f.{i}", f"report-{i:03}.pdf"))
        c.commit()
    finally:
        c.close()


def test_the_document_hub_shows_more_and_keeps_your_place(page, world):
    _hub_files(world, 450)
    opened(page)
    page.evaluate("openDocs()")
    rows = page.locator("#docList .doc-row")
    expect(rows).to_have_count(200)
    more = page.get_by_role("button", name=re.compile(r"Show more · \d+ left"))
    more.click()
    expect(rows).to_have_count(400)
    expect(page.locator("#docList [data-doc-dl]").nth(200)).to_be_focused()
    more.click()
    expect(rows).to_have_count(page.evaluate("_docMatched"))
    expect(more).to_have_count(0)
    names = rows.locator(".doc-fn").all_text_contents()
    assert len(names) == len(set(names))
    clean(page)


def test_the_document_hub_searches_files_it_has_not_loaded(page, world):
    _hub_files(world, 450)
    from mailmind.bootstrap.mailbox import store
    c = store.conn()
    try:
        c.execute("INSERT INTO attachments(message_pk, part_id, filename, content_type, size, is_inline) "
                  "VALUES (?,'late','needle.pdf','application/pdf',1000,0)", (world.pk("ada-inbox-09-rfc2047-folded.eml"),))
        c.commit()
    finally:
        c.close()
    opened(page)
    page.evaluate("openDocs()")
    expect(page.locator("#docList .doc-row")).to_have_count(200)
    page.locator("#docSearch").fill("needle")
    expect(page.locator("#docList .doc-fn")).to_have_text(["needle.pdf"])
    expect(page.locator("#docCount")).to_have_text("· 1")
    page.locator("#docSearch").fill("nothing-is-called-this")
    expect(page.locator("#docList .doc-empty")).to_have_text("No files match.")
    clean(page)


def test_the_document_hub_shows_a_file_once_when_new_mail_moves_the_list(page, world):
    _hub_files(world, 250)
    opened(page)
    page.evaluate("openDocs()")
    rows = page.locator("#docList .doc-row")
    expect(rows).to_have_count(200)
    every = page.evaluate("_docMatched")
    from mailmind.bootstrap.mailbox import store
    c = store.conn()
    try:
        newer = c.execute("SELECT pk FROM messages WHERE ts > (SELECT ts FROM messages WHERE pk=?) ORDER BY ts DESC LIMIT 1",
                          (world.pk(RECEIPT),)).fetchone()["pk"]
        c.execute("INSERT INTO attachments(message_pk, part_id, filename, content_type, size, is_inline) "
                  "VALUES (?,'new','arrived.pdf','application/pdf',1000,0)", (newer,))
        c.commit()
    finally:
        c.close()
    page.get_by_role("button", name=re.compile(r"Show more")).click()
    expect(rows).to_have_count(every)                                   # all it had, each once
    expect(page.get_by_role("button", name=re.compile(r"Show more|Loading"))).to_have_count(0)
    names = rows.locator(".doc-fn").all_text_contents()
    assert len(names) == len(set(names))
    clean(page)


def test_a_slow_answer_does_not_overwrite_a_newer_search(page, world):
    _hub_files(world, 3)
    opened(page)
    page.evaluate("openDocs()")
    expect(page.locator("#docList .doc-fn").first).to_be_visible()
    held = []
    slow = re.compile(r"/api/documents\?.*q=report")
    page.pw.route(slow, lambda route: held.append(route))
    page.locator("#docSearch").fill("report")
    while not held:
        page.wait_for_timeout(50)
    page.locator("#docSearch").fill("nothing-is-called-this")
    expect(page.locator("#docList .doc-empty")).to_have_text("No files match.")
    with page.pw.expect_response(slow):
        held[0].continue_()
    page.wait_for_timeout(100)
    expect(page.locator("#docList .doc-empty")).to_have_text("No files match.")
    clean(page)


def test_when_showing_more_fails_the_hub_keeps_what_it_has(page, world):
    _hub_files(world, 250)
    opened(page)
    page.evaluate("openDocs()")
    rows = page.locator("#docList .doc-row")
    expect(rows).to_have_count(200)
    page.pw.route(re.compile(r"/api/documents\?.*offset=200"), lambda route: route.fulfill(status=500, body="{}"))
    page.get_by_role("button", name=re.compile(r"Show more")).click()
    expect(page.locator("#toast")).to_have_text("Couldn’t load more files.")
    expect(rows).to_have_count(200)
    every = page.evaluate("_docMatched")
    page.pw.unroute(re.compile(r"/api/documents\?.*offset=200"))
    page.get_by_role("button", name=re.compile(r"Show more")).click()
    expect(rows).to_have_count(every)


def test_the_document_hub_filters_by_kind(page, world):
    _hub_files(world, 3)
    from mailmind.bootstrap.mailbox import store
    c = store.conn()
    try:
        c.execute("INSERT INTO attachments(message_pk, part_id, filename, content_type, size, is_inline) "
                  "VALUES (?,'pic','photo.png','image/png',1000,0)", (world.pk(RECEIPT),))
        c.commit()
    finally:
        c.close()
    opened(page)
    page.evaluate("openDocs()")
    expect(page.locator("#docList .doc-fn").first).to_be_visible()
    page.locator('#docFilters [data-dk="image"]').click()
    expect(page.locator("#docList .doc-fn")).to_have_text(["photo.png"])
    page.locator('#docFilters [data-dk="all"]').click()
    expect(page.locator("#docList .doc-fn").filter(has_text="report-")).to_have_count(3)
    clean(page)


def test_the_sync_menu_says_when_mail_is_out_in_a_batch(page, world, ai):
    from mailmind.bootstrap.mailbox import store
    c = store.conn()
    keys = [r["message_key"] for r in answers.needs_summary(c, 3)]
    answers.batch_begin(c, "triage-batch", "claude-opus-5-5", keys, 10, 0, 1)
    c.close()
    page.goto("/")
    expect(page.locator("body")).to_be_visible()
    assert "3 out in a batch" in page.evaluate("""async()=>{ while(!state.ai)await new Promise(r=>setTimeout(r,20)); return aiLineHtml(); }""")
    clean(page)


def test_a_tab_on_an_older_build_offers_a_reload_and_a_current_one_doesnt(page, app):
    opened(page)
    float_ = page.locator("#buildFloat")
    assert float_.is_hidden()
    page.pw.route("**/", lambda route: route.fulfill(
        response=(r := route.fetch()), body=re.sub(r'(name="mailmind-build" content=")[^"]+', r"\1older", r.text())))
    page.pw.reload()
    float_.wait_for(state="visible")
    assert "MailMind was updated" in float_.inner_text()
    page.pw.unroute("**/")
    page.locator("#buildReload").click()
    float_.wait_for(state="hidden")
    clean(page)


def test_undo_after_an_rsvp_stops_the_rsvp_and_leaves_what_was_filed(page, app, world):
    from mailmind.bootstrap.compose import outbox
    opened(page)
    row(page, world, RECEIPT).click()
    page.keyboard.press("e")
    until(page, lambda: world.where(RECEIPT)["server"][0][1] != "INBOX", "the receipt was archived")
    filed = world.where(RECEIPT)["server"][0][1]
    row(page, world, "ada-inbox-06-outlook-invite.eml").click()
    page.locator('.inv-rsvp [data-rsvp="accept"]').click()
    float_ = page.locator("#sendFloat")
    expect(float_).to_contain_text("Sending in")
    float_.get_by_role("button", name="Undo").click()
    expect(page.locator("#toast")).to_contain_text("RSVP not sent")
    until(page, lambda: app.get("/api/outbox").json["items"] == [], "the row went")
    app.clock.shift(timedelta(seconds=outbox.SEND_DELAY + 1))
    outbox.drain()
    assert world.smtp.messages == []
    assert world.where(RECEIPT)["server"][0][1] == filed
    clean(page)
