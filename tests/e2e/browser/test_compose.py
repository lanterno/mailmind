"""The composer, on the fictional world.

The rules these lean on are also pinned without a browser in web/src/features/compose/machine.test.ts.
"""
import base64
import email
import email.policy
import re
from datetime import timedelta

import pytest
from playwright.sync_api import expect

from fakes import anthropic_api as fa
from fixtures import world as w
from mailmind.bootstrap.assistant import answers

pytestmark = pytest.mark.browser

RECEIPT = "ada-inbox-05-receipt.eml"
LUNCH = ("ada-inbox-01-lunch.eml", "ada-inbox-02-lunch-confirm.eml")
INVITE = "ada-inbox-06-outlook-invite.eml"
FORWARD = "ada-inbox-07-forward-rfc822.eml"
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


def body(page):
    return page.get_by_role("textbox", name="message body")


def subject(page):
    return page.get_by_role("textbox", name="subject")


def to(page):
    return page.get_by_role("combobox", name="To")


def send_button(page):
    return page.locator("#cmpSend")


def writing(page, addr, subj, text):
    page.keyboard.press("c")
    to(page).fill(addr)
    subject(page).fill(subj)
    body(page).fill(text)


def compose(page, addr, subj, text):
    writing(page, addr, subj, text)
    send_button(page).click()


def let_undo_pass(page):
    expect(page.locator("#sendFloat")).to_contain_text("Sending in 10s")
    page.clock.run_for(11_000)
    expect(page.locator("#sendFloat")).to_contain_text("Sent")


def due(app):
    from mailmind.bootstrap.compose import outbox
    app.clock.shift(timedelta(seconds=outbox.SEND_DELAY + 1))
    outbox.drain()


def copies(world, subj):
    with world.server.lock:
        return [m.raw for m in world.server.mailbox(w.ADA, "Drafts").messages.values() if subj.encode() in m.raw]


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


def open_folder(page, account, folder):
    page.goto(f"/m/{account}/{folder}")


def spent_the_daily_limit():
    import time
    from mailmind.assistant.application.gateway import local_day
    from mailmind.assistant.application.ports import Spend, Usage
    from mailmind.bootstrap import assistant
    now = time.time()
    assistant.LEDGER.record(Spend(local_day(now), "ask", "claude-opus-5-5", "claude-opus-5-5", "ok", Usage(), 15_000_000, int(now)))


# ── sending ─────────────────────────────────────────────────────────────

def test_compose_send_undo_then_send(page, app, world):
    opened(page)
    compose(page, LIN, "Fabric order", "Hi Lin, the linen came in.")
    float_ = page.locator("#sendFloat")
    expect(float_).to_contain_text("Sending in")
    float_.get_by_role("button", name="Undo").click()
    expect(subject(page)).to_have_value("Fabric order")
    expect(body(page)).to_have_value(re.compile("the linen came in"))
    assert [o["state"] for o in app.get("/api/outbox").json["items"]] == []
    send_button(page).click()
    let_undo_pass(page)
    due(app)
    [sent] = world.smtp.messages
    assert sent.rcpts == [LIN] and b"Subject: Fabric order" in sent.data
    clean(page)


def test_the_compose_button_opens_a_new_message_at_to_and_keys_stay_out_of_the_way(page, world):
    opened(page)
    page.locator("#composeBtn").click()
    expect(page.get_by_role("heading", name="New message")).to_be_visible()
    assert page.evaluate("document.activeElement.classList.contains('ci')")
    page.keyboard.type("e")                                 # into To, and no archive
    expect(to(page)).to_have_value("e")
    page.keyboard.press("Backspace")                        # half an address would be saved on closing, and refused
    page.keyboard.press("Escape")
    expect(subject(page)).to_be_hidden()
    clean(page)


def test_opening_the_composer_never_takes_focus_back(page, world):
    opened(page)
    page.keyboard.press("c")
    subject(page).focus()
    page.wait_for_timeout(120)
    assert page.evaluate("document.activeElement.id") == "cmpSubject"
    clean(page)


def test_a_reply_answers_the_sender_and_threads(page, app, world):
    opened(page)
    row(page, world, LUNCH[1]).click()
    expect(page.locator(".rd-subj")).to_be_visible()
    page.locator('#reader [data-tca="reply"],#reader [data-ca="reply"]').click()
    expect(page.get_by_role("heading", name="Reply", exact=True)).to_be_visible()
    expect(subject(page)).to_have_value(re.compile("^Re: "))
    expect(page.locator("#cmpTo .cm-chip")).to_have_count(1)
    assert page.evaluate("document.activeElement.id") == "cmpBody"
    body(page).fill("See you there.")
    send_button(page).click()
    let_undo_pass(page)
    due(app)
    [sent] = world.smtp.messages
    assert b"In-Reply-To:" in sent.data and b"See you there." in sent.data
    clean(page)


def test_keys_reply_reply_all_and_forward(page, world):
    opened(page)
    row(page, world, LUNCH[1]).click()
    expect(page.locator(".rd-subj")).to_be_visible()
    page.keyboard.press("r")
    expect(page.get_by_role("heading", name="Reply", exact=True)).to_be_visible()
    page.keyboard.press("Escape")
    expect(subject(page)).to_be_hidden()
    page.keyboard.press("a")
    expect(page.get_by_role("heading", name="Reply all")).to_be_visible()
    page.keyboard.press("Escape")
    expect(subject(page)).to_be_hidden()
    page.keyboard.press("f")
    expect(page.get_by_role("heading", name="Forward")).to_be_visible()
    expect(subject(page)).to_have_value(re.compile("^Fwd: "))
    assert page.locator("#cmpTo .cm-chip").count() == 0
    clean(page)


def test_the_palette_offers_compose_and_reply(page, world):
    opened(page)
    row(page, world, RECEIPT).click()
    expect(page.locator(".rd-subj")).to_be_visible()
    page.keyboard.press("ControlOrMeta+k")
    page.get_by_role("combobox", name="Command").fill("reply all")
    page.keyboard.press("Enter")
    expect(page.get_by_role("heading", name="Reply all")).to_be_visible()
    clean(page)


def test_a_forward_saved_once_shows_its_files_as_its_own(page, world):
    opened(page)
    row(page, world, FORWARD).click()
    expect(page.locator(".rd-subj")).to_be_visible()
    page.keyboard.press("f")
    quote = page.locator("#cmpQToggle")
    expect(quote).to_contain_text("attachment")
    body(page).fill("For you.")
    page.keyboard.press("Control+s")
    expect(page.locator("#cmpAttach .cmp-att")).to_have_count(1)
    expect(quote).not_to_contain_text("attachment")
    clean(page)


def test_a_reply_saved_once_goes_out_after_its_originals_row_went(page, app, world):
    from mailmind.bootstrap.mailbox import store
    opened(page)
    pk = world.pk(LUNCH[1])
    row(page, world, LUNCH[1]).click()
    expect(page.locator(".rd-subj")).to_be_visible()
    page.keyboard.press("r")
    body(page).fill("See you there.")
    page.keyboard.press("Control+s")
    expect(page.locator("#cmpSaved")).to_contain_text("Saved")
    c = store.conn()
    store.delete_message(c, pk)
    c.commit()
    c.close()
    send_button(page).click()
    let_undo_pass(page)
    due(app)
    [sent] = world.smtp.messages
    assert b"In-Reply-To:" in sent.data and b"See you there." in sent.data
    clean(page)


def test_a_quoted_message_folds_and_opens(page, world):
    opened(page)
    row(page, world, LUNCH[1]).click()
    expect(page.locator(".rd-subj")).to_be_visible()
    page.keyboard.press("r")
    toggle = page.locator("#cmpQToggle")
    expect(toggle).to_contain_text("▸ quoted message")
    expect(page.locator("#cmpQBody")).to_be_hidden()
    toggle.click()
    expect(toggle).to_contain_text("▾ quoted message")
    expect(page.locator("#cmpQBody")).to_be_visible()
    clean(page)


# ── saving ──────────────────────────────────────────────────────────────

def test_an_untouched_reply_closes_with_nothing_saved(page, world):
    opened(page)
    row(page, world, LUNCH[1]).click()
    expect(page.locator(".rd-subj")).to_be_visible()
    asked = []
    page.on("request", lambda r: asked.append(r.url) if r.url.endswith("/api/draft") else None)
    page.keyboard.press("r")
    expect(body(page)).to_be_visible()
    page.keyboard.press("Escape")
    expect(body(page)).to_be_hidden()
    page.wait_for_timeout(150)
    assert asked == []
    clean(page)


def test_a_message_whose_closing_save_fails_stays_a_click_away(page, world):
    opened(page)
    writing(page, LIN, "Thread colours", "Which blue did you mean?")
    page.pw.route("**/api/draft", lambda route: route.fulfill(status=500, body="{}"))   # a server away wouldn't do it
    page.keyboard.press("Escape")
    float_ = page.locator("#sendFloat")
    expect(float_).to_contain_text("Your message couldn’t be saved")
    expect(body(page)).to_be_hidden()
    expect(page.get_by_role("status").filter(has=float_)).to_contain_text("Your message couldn’t be saved")
    assert page.problems and all("500" in x for x in page.problems)   # the save's answer, as the console logs it
    page.problems.clear()
    page.pw.unroute("**/api/draft")
    float_.get_by_role("button", name="Edit").click()
    expect(body(page)).to_have_value("Which blue did you mean?")
    expect(subject(page)).to_have_value("Thread colours")
    expect(float_).to_have_count(0)
    page.keyboard.press("Escape")                         # nothing else holds it, so closing it saves it
    until(page, lambda: len(copies(world, "Thread colours")) == 1, "the reopened message saved")
    clean(page)


def test_a_draft_the_server_wont_take_waits_in_the_tray_and_opens_from_it(page, app, world):
    from mailmind.bootstrap.compose import drafts
    opened(page)
    world.server.fail("APPEND", user=w.ADA, times=None)
    writing(page, LIN, "Thread colours", "Which blue did you mean?")
    page.keyboard.press("Escape")                         # closing saves: here, and the server says no
    button = page.locator("#outboxBtn")
    expect(button).to_be_visible()
    button.click()
    fly = page.locator("#outboxFly")
    expect(fly).to_contain_text("Drafts waiting")
    expect(fly).to_contain_text("Thread colours")
    fly.get_by_role("button", name="Edit").click()
    expect(body(page)).to_have_value("Which blue did you mean?")
    page.keyboard.press("Escape")
    world.server.clear_faults()
    app.clock.shift(timedelta(minutes=2))

    def pushed():                                          # the closing save may still be on its way
        drafts.push_round()
        return len(copies(world, "Thread colours")) == 1
    until(page, pushed, "the job pushed it")
    expect(button).to_be_hidden()
    clean(page)


def test_typing_during_a_save_is_saved_when_the_composer_closes(page, world):
    opened(page)
    writing(page, LIN, "Thread colours", "Which blue")
    held = Held(page, "/api/draft")
    page.keyboard.press("Control+s")
    until(page, lambda: held.routes, "the save reached the network")
    body(page).press_sequentially(" did you mean?")
    held.let_go()
    expect(page.locator("#cmpSaved")).to_contain_text("Saved")
    until(page, lambda: len(copies(world, "Thread colours")) == 1, "the first save landed")
    page.keyboard.press("Escape")
    until(page, lambda: any(b"did you mean?" in c for c in copies(world, "Thread colours")), "the typing saved")
    assert len(copies(world, "Thread colours")) == 1
    clean(page)


def test_removing_a_recipient_is_an_edit_a_close_saves(page, world):
    opened(page)
    writing(page, f"{LIN}, noor@example.test", "Thread colours", "Which blue did you mean?")
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
    writing(page, LIN, "Thread colours", "Which blue did you mean?")
    held = Held(page, "/api/draft")
    page.keyboard.press("Control+s")
    until(page, lambda: held.routes, "the first save reached the network")
    body(page).press_sequentially(" The dark one?")
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
    writing(page, LIN, "Thread colours", "Which blue did you mean?")
    held = Held(page, "/api/draft")
    page.keyboard.press("Control+s")
    until(page, lambda: held.routes, "the save reached the network")
    asked = []
    page.on("request", lambda r: asked.append(r.post_data_json) if r.url.endswith("/api/send") else None)
    send_button(page).click()
    page.wait_for_timeout(200)
    assert asked == []                                    # the send waits to learn which copy it replaces
    held.let_go()
    until(page, lambda: asked, "the send went")
    assert asked[0]["draft_id"]
    let_undo_pass(page)
    due(app)
    assert copies(world, "Thread colours") == []
    assert [m.rcpts for m in world.smtp.messages] == [[LIN]]
    clean(page)


def test_autosave_writes_it_down_here_and_the_server_gets_it_a_minute_on(page, app, world):
    from mailmind.bootstrap.compose import drafts
    opened(page)
    writing(page, LIN, "Thread colours", "Which blue did you mean?")
    page.clock.run_for(8_500)
    expect(page.locator("#cmpSaved")).to_contain_text("Saved")
    assert copies(world, "Thread colours") == []
    app.clock.shift(timedelta(minutes=2))
    drafts.push_round()
    assert len(copies(world, "Thread colours")) == 1
    clean(page)


def test_a_message_reopened_by_undo_send_is_saved_when_closed(page, world):
    opened(page)
    compose(page, LIN, "Thread colours", "Which blue did you mean?")
    page.locator("#sfUndo").click()
    expect(body(page)).to_have_value("Which blue did you mean?")
    page.keyboard.press("Escape")
    until(page, lambda: len(copies(world, "Thread colours")) == 1, "the reopened message saved")
    clean(page)


# ── drafts ──────────────────────────────────────────────────────────────

def test_reopen_a_saved_draft(page, world):
    open_folder(page, w.BEA, "Drafts")
    draft = row(page, world, "bea-drafts-01-apple-mail.eml")
    expect(draft).to_contain_text("Re: Contract draft v2")
    draft.click()
    page.locator('#reader [data-ca="edit"]').click()
    expect(subject(page)).to_have_value("Re: Contract draft v2")
    expect(body(page)).to_have_value(re.compile("my comments are in the attached file"))
    expect(page.locator("#cmpTo")).to_contain_text("ivo@harbor.example.test")
    clean(page)


def test_a_draft_replaced_or_discarded_leaves_the_list_at_once(page, world):
    open_folder(page, w.BEA, "Drafts")
    row(page, world, "bea-drafts-01-apple-mail.eml").click()
    page.locator('#reader [data-ca="edit"]').click()
    subject(page).fill("Re: Contract draft v3")
    page.keyboard.press("Escape")
    rows = page.locator("#rows")
    expect(rows).to_contain_text("Re: Contract draft v3")
    expect(rows).not_to_contain_text("Re: Contract draft v2")
    expect(page.locator("#reader")).to_contain_text("Re: Contract draft v3")   # no reader left on the copy that went
    page.locator('#reader [data-ca="edit"]').click()
    page.get_by_role("button", name="Discard").click()
    expect(rows).not_to_contain_text("Re: Contract draft")
    clean(page)


# ── the send-time check ─────────────────────────────────────────────────

def test_nobody_to_send_to_holds_it_with_no_way_past(page, world):
    opened(page)
    page.keyboard.press("c")
    subject(page).fill("Fabric order")
    body(page).fill("Hi")
    send_button(page).click()
    guard = page.locator("#cmpGuard")
    expect(guard).to_contain_text("Not sent yet")
    expect(page.locator("#cmpGuardGo")).to_have_count(0)
    assert page.evaluate("document.activeElement.id") == "cmpGuard"
    page.keyboard.press("Escape")                         # keeps editing; a second Esc closes
    expect(guard).to_have_count(0)
    expect(body(page)).to_be_visible()
    assert world.smtp.messages == []
    clean(page)


def test_a_chip_that_isnt_an_address_goes_back_to_be_fixed(page, world):
    opened(page)
    page.keyboard.press("c")
    to(page).fill("not an address")
    to(page).press("Enter")
    subject(page).fill("x")
    send_button(page).click()
    guard = page.locator("#cmpGuard")
    expect(guard).to_be_visible()
    guard.locator(".cg-fix").click()
    expect(guard).to_have_count(0)
    expect(to(page)).to_have_value("not an address")
    expect(to(page)).to_be_focused()
    clean(page)


def test_an_empty_subject_is_asked_about_and_send_anyway_arms_after_a_moment(page, app, world):
    opened(page)
    writing(page, LIN, "", "Hi Lin")
    send_button(page).click()
    go = page.locator("#cmpGuardGo")
    expect(go).to_have_attribute("aria-disabled", "true")
    page.clock.run_for(800)
    expect(go).not_to_have_attribute("aria-disabled", "true")
    page.wait_for_timeout(800)   # the arm delay reads the page's real clock too
    go.click()
    expect(body(page)).to_be_hidden()
    let_undo_pass(page)
    due(app)
    assert len(world.smtp.messages) == 1
    clean(page)


def test_a_mention_of_an_attachment_with_none_is_asked_about_and_an_edit_retires_the_check(page, world):
    opened(page)
    writing(page, LIN, "Fabric order", "The order is attached.")
    send_button(page).click()
    guard = page.locator("#cmpGuard")
    expect(guard).to_be_visible()
    body(page).fill("The order is in the post.")
    expect(guard).to_have_count(0)
    clean(page)


# ── later ───────────────────────────────────────────────────────────────

def test_a_send_that_failed_says_its_retrying_and_the_tray_tries_it_now(page, app, world):
    from mailmind.bootstrap.compose import outbox
    opened(page)
    writing(page, LIN, "Fabric order", "Hi Lin")
    send_button(page).click()
    until(page, lambda: app.get("/api/outbox").json["items"], "it's queued")
    world.smtp.fail("MAIL")
    app.clock.shift(timedelta(seconds=outbox.SEND_DELAY + 1))
    outbox.drain()
    page.locator("#outboxBtn").click()
    fly = page.locator("#outboxFly")
    expect(fly).to_contain_text("retrying")
    expect(fly.get_by_role("button", name="Cancel")).to_be_visible()
    fly.get_by_role("button", name="Try now").click()
    until(page, lambda: not app.get("/api/outbox").json["items"][0]["retrying"], "it's asked for now")
    outbox.drain()
    assert len(world.smtp.messages) == 1
    clean(page)


def test_send_later_schedules_and_the_tray_cancels_it(page, app, world):
    opened(page)
    writing(page, LIN, "Fabric order", "Hi Lin")
    page.locator("#cmpSchedule").click()
    page.get_by_role("button", name="Tomorrow 8 AM").click()
    expect(page.locator("#sendFloat")).to_contain_text("Scheduled")
    expect(body(page)).to_be_hidden()
    button = page.locator("#outboxBtn")
    expect(button).to_be_visible()
    button.click()
    fly = page.locator("#outboxFly")
    expect(fly).to_contain_text("scheduled")
    expect(fly).to_contain_text("Fabric order")
    fly.get_by_role("button", name="Cancel").click()
    expect(page.locator("#toast")).to_contain_text("Send cancelled")
    until(page, lambda: app.get("/api/outbox").json["items"] == [], "the row went")
    clean(page)


def test_a_digit_picks_a_later_time(page, world):
    opened(page)
    writing(page, LIN, "Fabric order", "Hi Lin")
    page.locator("#cmpSchedule").click()
    expect(page.get_by_role("button", name="In 1 hour")).to_be_focused()
    page.keyboard.press("1")
    expect(page.locator("#sendFloat")).to_contain_text("Scheduled")
    clean(page)


# ── the tray ────────────────────────────────────────────────────────────

def test_a_send_with_no_answer_says_it_may_have_gone_and_only_a_person_sends_it_again(page, app, world):
    from mailmind.bootstrap.compose import outbox
    world.smtp.drop_after_data()
    opened(page)
    compose(page, LIN, "Thread colours", "Which blue did you mean?")
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
    page.locator("#listTitle").click()                    # the tray stays up while it's open, whatever it holds
    expect(fly).to_be_hidden()
    expect(button).to_be_hidden()
    clean(page)


def give_up_on_a_send(page, app, world):
    from mailmind.bootstrap.compose import outbox
    world.smtp.fail("RCPT", "451 4.3.0 try again later", times=None)
    opened(page)
    compose(page, LIN, "Thread colours", "Which blue did you mean?")
    let_undo_pass(page)
    due(app)
    for _ in range(outbox.MAX_ATTEMPTS - 1):
        app.clock.shift(timedelta(seconds=outbox._BACKOFF_CAP))
        outbox.drain()
    assert [o["state"] for o in app.get("/api/outbox").json["items"]] == ["error"]
    return page.locator("#outboxBtn")


def test_a_failed_send_offers_retry_and_retry_sends(page, app, world):
    from mailmind.bootstrap.compose import outbox
    button = give_up_on_a_send(page, app, world)
    expect(button).to_have_class(re.compile("has-fail"), timeout=3000)
    button.click()
    fly = page.locator("#outboxFly")
    expect(fly).to_contain_text("Thread colours")
    expect(fly).to_contain_text("recipient refused")
    world.smtp.clear_faults()
    fly.get_by_role("button", name="Retry").click()
    expect(fly).to_contain_text("queued")
    outbox.drain()
    [sent] = world.smtp.messages
    assert b"Subject: Thread colours" in sent.data
    page.locator("#listTitle").click()                    # the tray stays up while it's open, whatever it holds
    expect(fly).to_be_hidden()
    expect(button).to_be_hidden()                         # a sent message leaves the outbox
    clean(page)


def test_a_change_that_cant_reach_the_server_waits_in_the_tray_and_discard_puts_it_back(page, app, world):
    from mailmind.bootstrap.mailbox import commands
    opened(page)
    receipt = row(page, world, RECEIPT)
    receipt.click()
    expect(page.locator(".rd-subj")).to_be_visible()
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
    expect(receipt).to_have_count(1)
    world.server.clear_faults()
    fly.get_by_role("button", name="Discard").click()
    until(page, lambda: app.get("/api/pending").json["items"] == [], "the change went")
    expect(receipt).to_have_count(1)
    assert world.where(RECEIPT)["server"][0][1] == "INBOX"
    clean(page)


def test_retry_in_the_tray_sends_the_change_again(page, world):
    from mailmind.bootstrap.mailbox import commands
    opened(page)
    receipt = row(page, world, RECEIPT)
    receipt.click()
    expect(page.locator(".rd-subj")).to_be_visible()
    world.server.drop("SELECT", user=w.ADA, times=None)
    page.keyboard.press("e")
    expect(receipt).to_have_count(0)
    button = page.locator("#outboxBtn")
    expect(button).to_be_visible()                        # the click's own try found no server, so the worker has it
    world.server.clear_faults()
    world.server.fail("UID MOVE", user=w.ADA, times=1)
    world.server.fail("UID COPY", user=w.ADA, times=1)
    commands.drain()
    button.click()
    fly = page.locator("#outboxFly")
    expect(fly).to_contain_text("stuck")
    expect(receipt).to_have_count(1)                      # the refusal gave it back
    assert world.where(RECEIPT)["server"][0][1] == "INBOX"
    fly.get_by_role("button", name="Retry").click()
    expect(fly).to_contain_text("waiting")
    expect(receipt).to_have_count(0)
    commands.drain()
    until(page, lambda: world.where(RECEIPT)["server"][0][1] == "Archive", "archived on the server")
    page.locator("#listTitle").click()                    # the tray stays up while it's open, whatever it holds
    expect(fly).to_be_hidden()
    expect(button).to_be_hidden()
    clean(page)


# ── recipients and files ────────────────────────────────────────────────

def test_commas_make_chips_and_backspace_takes_the_last_away(page, world):
    opened(page)
    page.keyboard.press("c")
    to(page).press_sequentially("lin@example.test,noor@example.test,")
    expect(page.locator("#cmpTo .cm-chip")).to_have_count(2)
    to(page).press("Backspace")
    expect(page.locator("#cmpTo .cm-chip")).to_have_count(1)
    clean(page)


def test_addresses_you_know_are_offered_as_you_type(page, world):
    opened(page)
    page.keyboard.press("c")
    to(page).press_sequentially("lin", delay=20)
    menu = page.get_by_role("listbox", name="Addresses for To")
    expect(menu).to_be_visible()
    to(page).press("ArrowDown")
    to(page).press("Enter")
    expect(menu).to_have_count(0)
    expect(page.locator("#cmpTo .cm-chip")).to_contain_text("@")
    expect(to(page)).to_have_value("")
    clean(page)


def test_someone_new_is_marked_and_someone_known_isnt(page, world):
    opened(page)
    page.route("**/api/contacts/known", lambda route: route.fulfill(json={"ok": True, "thin": False, "bad": [], "items": {
        "stranger@example.test": {"known": False, "self": False}, "friend@example.test": {"known": True, "self": False}}}))
    page.keyboard.press("c")
    to(page).press_sequentially("stranger@example.test,friend@example.test,")
    chips = page.locator("#cmpTo .cm-chip")
    expect(chips.nth(0)).to_have_class(re.compile(r"\bnew\b"))
    expect(chips.nth(0).get_by_role("button")).to_have_accessible_name("Remove stranger@example.test, new to you")
    expect(chips.nth(1)).not_to_have_class(re.compile(r"\bnew\b"))
    expect(page.locator("#srLive")).to_have_text("New to you: stranger@example.test.")
    clean(page)


def test_a_file_attaches_and_goes_out_with_the_mail(page, app, world):
    opened(page)
    writing(page, LIN, "Fabric order", "The order is attached.")
    with page.expect_file_chooser() as chooser:
        page.locator("#cmpAttachBtn").click()
    chooser.value.set_files({"name": "order.txt", "mimeType": "text/plain", "buffer": b"one bolt of linen"})
    expect(page.locator("#cmpAttach .cmp-att")).to_contain_text("order.txt")
    send_button(page).click()
    let_undo_pass(page)
    due(app)
    [sent] = world.smtp.messages
    assert b'filename="order.txt"' in sent.data or b"filename=order.txt" in sent.data
    clean(page)


# ── the AI's words ──────────────────────────────────────────────────────

def test_ai_off_hides_the_ai_in_the_composer(page, world):
    opened(page)
    row(page, world, LUNCH[1]).click()
    expect(page.locator(".rd-subj")).to_be_visible()
    page.keyboard.press("r")
    expect(body(page)).to_be_visible()
    expect(page.locator("#cmpAiRow")).to_have_count(0)
    expect(page.locator("#cmpAiDraft")).to_have_count(0)
    clean(page)


def test_ai_drafts_a_reply_in_a_tone_and_says_what_to_say(page, world, ai):
    ai.responder = lambda req: fa.answer({"body": "Thursday works."})
    opened(page)
    row(page, world, LUNCH[1]).click()
    expect(page.locator(".rd-subj")).to_be_visible()
    page.keyboard.press("r")
    expect(page.locator("#cmpAiDraft")).to_be_visible()
    page.locator("#cmpAiMore").click()
    page.get_by_role("button", name="Warm").click()
    page.get_by_role("textbox", name="What should it say?").fill("say yes")
    page.locator("#dmGo").click()
    expect(body(page)).to_have_value(re.compile("Thursday works"))
    expect(page.locator("#cmpAiDraft")).to_contain_text("warm")
    clean(page)


def test_a_rewrite_acts_on_the_message_and_undo_puts_it_back(page, world, ai):
    ai.responder = lambda req: fa.answer({"text": "Kindly confirm."})
    opened(page)
    writing(page, LIN, "Fabric order", "confirm pls")
    page.get_by_role("button", name="Formal").click()
    expect(body(page)).to_have_value("Kindly confirm.")
    page.locator("#cmpRwUndo").click()
    expect(body(page)).to_have_value("confirm pls")
    expect(page.locator("#cmpRwUndo")).to_have_count(0)
    clean(page)


def test_at_the_limit_a_rewrite_goes_only_when_you_say_so(page, world, ai):
    spent_the_daily_limit()
    ai.responder = lambda req: fa.answer({"text": "Over the limit, as asked."})
    opened(page)
    writing(page, LIN, "Fabric order", "confirm pls")
    page.get_by_role("button", name="Formal").click()
    ask = page.get_by_role("alertdialog", name="Today’s AI limit is reached")
    ask.get_by_role("button", name="Not now").click()
    expect(ask).to_be_hidden()
    expect(body(page)).to_have_value("confirm pls")
    assert ai.requests == []
    assert page.problems and all("402" in x for x in page.problems)   # the first answer, as the console logs it
    page.problems.clear()
    page.get_by_role("button", name="Formal").click()
    ask.get_by_role("button", name="Go over the limit").click()
    expect(body(page)).to_have_value("Over the limit, as asked.")
    page.problems.clear()
    clean(page)


def test_a_quick_reply_starts_as_its_line(page, world):
    opened(page)
    row(page, world, LUNCH[1]).click()
    expect(page.locator(".rd-subj")).to_be_visible()
    page.get_by_role("button", name="Thanks!").click()
    expect(body(page)).to_have_value(re.compile("^Thanks!"))
    expect(page.get_by_role("heading", name="Reply", exact=True)).to_be_visible()
    until(page, lambda: page.problems, "a quick reply asks the AI for a version of its own, which AI being off refuses")
    assert all("status of 409" in p for p in page.problems), page.problems
    expect(body(page)).to_have_value(re.compile("^Thanks!"))      # with no AI the line stays
    page.problems.clear()
    clean(page)


def test_no_quick_replies_on_a_receipt(page, world):
    opened(page)
    row(page, world, RECEIPT).click()
    expect(page.locator(".rd-subj")).to_be_visible()
    expect(page.locator(".qr-row")).to_have_count(0)
    clean(page)


def lunch_needs_a_reply(world):
    from mailmind.bootstrap.mailbox import store
    c = store.conn()
    try:
        answers.save_summary(c, world.pk(LUNCH[1]), "Lin confirms lunch on Thursday.", 5, "action-needed", "Reply", "claude-opus-5-5", "2026-11-02T11:00:00+00:00")
        c.commit()
    finally:
        c.close()


def test_a_draft_written_ahead_shows_on_the_reply_button_and_opens_for_review(page, world, ai):
    lunch_needs_a_reply(world)
    ai.responder = lambda req: fa.answer({"body": "Written ahead: Thursday works."})
    page.goto(f"/m/{w.ADA}/INBOX?key={world.key(LUNCH[1])}&folder=INBOX&uid={world.uid(LUNCH[1])}")
    expect(page.locator(".rd-subj")).to_be_visible()
    page.clock.run_for(1500)
    ready = page.locator('#reader [data-ca="draftready"]')
    expect(ready).to_be_visible()
    assert ai.requests
    ready.click()
    expect(body(page)).to_have_value(re.compile("^Written ahead: Thursday works"))
    expect(page.locator("#toast")).to_contain_text("review and edit before sending")
    clean(page)


# ── an invitation ───────────────────────────────────────────────────────

def test_a_chosen_undo_window_is_what_the_float_counts_down(page, app, world):
    from mailmind.bootstrap import accounts
    accounts.config.set_preferences({"undo_send_seconds": 30})
    opened(page)
    compose(page, LIN, "Fabric order", "Hi Lin, the linen came in.")
    expect(page.locator("#sendFloat")).to_contain_text("Sending in 30s")
    page.clock.run_for(5_000)
    expect(page.locator("#sendFloat")).to_contain_text("Sending in 25s")
    clean(page)


def test_a_new_send_counts_what_preferences_says_now_though_the_page_loaded_before(page, app, world):
    opened(page)
    from mailmind.bootstrap import accounts
    accounts.config.set_preferences({"undo_send_seconds": 7})
    compose(page, LIN, "Fabric order", "Hi Lin, the linen came in.")
    expect(page.locator("#sendFloat")).to_contain_text("Sending in 7s")
    clean(page)


def test_rsvp_goes_out_after_its_ten_seconds_and_undo_stops_it(page, app, world):
    opened(page)
    row(page, world, INVITE).click()
    card = page.locator(".invite-card")
    expect(card).to_be_visible()
    card.get_by_role("button", name="Accept").click()
    expect(card).to_contain_text("Accepted")
    float_ = page.locator("#sendFloat")
    expect(float_).to_contain_text("Sending in")
    float_.get_by_role("button", name="Undo").click()
    expect(page.locator("#toast")).to_contain_text("RSVP not sent")
    until(page, lambda: app.get("/api/outbox").json["items"] == [], "the row went")
    due(app)
    assert world.smtp.messages == []
    clean(page)


# ── accessibility ───────────────────────────────────────────────────────

def test_axe_finds_nothing_in_the_composer_with_its_check_and_its_menu_open(page, world, ai, every_theme):
    from axe_playwright_python.sync_playwright import Axe

    def findings():
        found = Axe().run(page.pw, options={"resultTypes": ["violations"], "iframes": False}).response
        return {v["id"]: len(v["nodes"]) for v in found["violations"]}

    opened(page)
    row(page, world, LUNCH[1]).click()
    expect(page.locator(".rd-subj")).to_be_visible()
    page.keyboard.press("a")
    expect(body(page)).to_be_visible()
    assert findings() == {}
    subject(page).fill("")
    send_button(page).click()
    expect(page.locator("#cmpGuard")).to_be_visible()
    assert findings() == {}
    page.keyboard.press("Escape")
    clean(page)


PASTE_INTO = """(el, text) => { const data = new DataTransfer(); data.setData("text/plain", text);
  el.dispatchEvent(new ClipboardEvent("paste", { bubbles: true, cancelable: true, clipboardData: data })); }"""


def test_a_pasted_link_goes_in_without_its_tracking(page, world):
    opened(page)
    page.keyboard.press("c")
    box = body(page)
    box.fill("Read this: ")
    box.press("End")
    box.evaluate(PASTE_INTO, "https://shop.example.test/a?id=7&utm_source=news and "
                             "https://www.google.com/url?q=https%3A%2F%2Freal.example.test%2Fb%3Ffbclid%3Dx&sa=D")
    expect(box).to_have_value("Read this: https://shop.example.test/a?id=7 and https://real.example.test/b")
    clean(page)


def test_a_paste_with_nothing_to_clean_is_left_to_the_browser(page, world):
    opened(page)
    page.keyboard.press("c")
    box = body(page)
    box.fill("x")
    box.press("End")
    untouched = box.evaluate("""(el) => { const data = new DataTransfer(); data.setData("text/plain", "plain words");
      const e = new ClipboardEvent("paste", { bubbles: true, cancelable: true, clipboardData: data });
      el.dispatchEvent(e); return !e.defaultPrevented; }""")
    assert untouched
    expect(box).to_have_value("x")                       # a script's event inserts nothing by itself: the browser's paste would
    clean(page)


# ── Markdown ─────────────────────────────────────────────────────────────

def mode(page, name):
    return page.get_by_role("button", name=name, exact=True)


def test_preview_shows_the_markdown_in_a_frame_with_nothing_allowed_and_write_goes_back(page, app, world):
    opened(page)
    writing(page, LIN, "Notes", "**Bold** and <script>no</script>")
    mode(page, "Preview").click()
    frame = page.locator("#cmpPreview iframe")
    expect(frame).to_have_attribute("sandbox", "")
    expect(frame).to_have_attribute("srcdoc", re.compile("<strong>Bold</strong> and &lt;script&gt;"))
    expect(body(page)).to_be_hidden()
    expect(page.locator("#cmpPreview")).not_to_contain_text("plain text")
    mode(page, "Write").click()
    expect(body(page)).to_be_visible()
    expect(body(page)).to_have_value("**Bold** and <script>no</script>")
    expect(body(page)).to_be_focused()
    clean(page)


def test_preview_says_so_when_it_goes_as_plain_text(page, app, world):
    opened(page)
    writing(page, LIN, "Notes", "Just words")
    mode(page, "Preview").click()
    expect(page.locator("#cmpPreview")).to_contain_text("plain text")
    expect(page.locator("#cmpPreview iframe")).to_have_attribute("srcdoc", re.compile("Just words"))
    clean(page)


PNG_1PX = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==")


def picture_file(name="chart.png", mime="image/png"):
    return {"name": name, "mimeType": mime, "buffer": PNG_1PX}


def test_a_picture_goes_into_the_text_shows_in_the_preview_and_goes_out_inline(page, app, world):
    opened(page)
    writing(page, LIN, "Fabric picture", "Here it is")
    body(page).press("End")
    page.locator("#cmpPicture").set_input_files(picture_file())
    expect(body(page)).to_have_value(re.compile(r"^Here it is!\[chart\.png\]\(cid:\w+\)$"))
    expect(page.locator(".cmp-att")).to_contain_text("chart.png")
    expect(page.locator(".cmp-att")).to_contain_text("in the text")
    mode(page, "Preview").click()
    frame = page.locator("#cmpPreview iframe")
    expect(frame).to_have_attribute("srcdoc", re.compile("data:image/png;base64,iVBOR"))
    assert page.problems == [] and page.blocked == []    # the frame's policy lets a picture's data in, and nothing else
    send_button(page).click()
    let_undo_pass(page)
    due(app)
    [sent] = world.smtp.messages
    m = email.message_from_bytes(sent.data, policy=email.policy.default)
    assert m.get_content_type() == "multipart/alternative"
    related = m.get_payload(1)
    assert related.get_content_type() == "multipart/related"
    html, img = related.iter_parts()
    assert img.get_content() == PNG_1PX and img.get_content_disposition() == "inline"
    assert f'src="cid:{img["Content-ID"][1:-1]}"' in html.get_content()
    assert m.get_body(preferencelist=("plain",)).get_content().strip() == "Here it is[chart.png]"
    clean(page)


def test_a_chip_taken_off_takes_its_picture_out_of_the_text(page, app, world):
    opened(page)
    writing(page, LIN, "Fabric picture", "")
    page.locator("#cmpPicture").set_input_files(picture_file())
    expect(body(page)).to_have_value(re.compile("cid:"))
    page.get_by_role("button", name="Remove chart.png").click()
    expect(body(page)).to_have_value("")
    expect(page.locator(".cmp-att")).to_have_count(0)
    clean(page)


def test_a_picture_pasted_goes_into_the_text(page, app, world):
    opened(page)
    writing(page, LIN, "Paste", "")
    page.evaluate("""() => { const el = document.querySelector("#cmpBody"), data = new DataTransfer();
      data.items.add(new File([new Uint8Array([137, 80])], "pasted.png", { type: "image/png" }));
      el.dispatchEvent(new ClipboardEvent("paste", { bubbles: true, cancelable: true, clipboardData: data })); }""")
    expect(body(page)).to_have_value(re.compile("pasted.png"))
    clean(page)


def test_a_markdown_message_goes_out_as_text_and_html_and_reopens_as_typed(page, app, world):
    opened(page)
    writing(page, LIN, "Fabric notes", "**Linen** came in")
    mode(page, "Preview").click()
    expect(page.locator("#cmpPreview iframe")).to_have_attribute("srcdoc", re.compile("<strong>Linen</strong>"))
    send_button(page).click()
    let_undo_pass(page)
    due(app)
    [sent] = world.smtp.messages
    m = email.message_from_bytes(sent.data, policy=email.policy.default)
    assert m.get_content_type() == "multipart/alternative"
    assert m.get_body(preferencelist=("plain",)).get_content().strip() == "**Linen** came in"
    assert "<strong>Linen</strong>" in m.get_body(preferencelist=("html",)).get_content()
    clean(page)
