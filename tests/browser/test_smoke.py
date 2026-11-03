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


@pytest.mark.xfail(strict=True, reason="loadFolders bumps state.cseq, then loadCMessages bumps it again, "
                                       "so the folder counts loadFolders fetched are thrown away")
def test_the_rail_counts_a_message_just_snoozed(page, world):
    opened(page)
    expect(folder(page, "__snoozed__").locator(".unread")).to_have_count(0)
    receipt = row(page, world, RECEIPT)
    receipt.click()
    page.keyboard.press("h")
    page.keyboard.press("1")
    expect(receipt).to_have_count(0)
    expect(folder(page, "__snoozed__").locator(".unread")).to_have_text("1", timeout=3000)


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


@pytest.mark.xfail(strict=True, reason="outbox.drain returns from a failed attempt before calling "
                                       "_after_send, so no outbox event reaches the page")
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
