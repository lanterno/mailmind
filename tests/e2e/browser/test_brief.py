"""Today's brief: what the Inbox opens on, and the lists and panels it opens."""
import re

import pytest
from playwright.sync_api import expect

from fakes import anthropic_api as fa
from fixtures import world as w
from mailmind.bootstrap.assistant import answers

pytestmark = pytest.mark.browser

RECEIPT = "ada-inbox-05-receipt.eml"
LUNCH = ("ada-inbox-01-lunch.eml", "ada-inbox-02-lunch-confirm.eml")
BRIEF = "Lin is waiting to hear back about lunch on Thursday."
NOW = "2026-11-02T11:00:00+00:00"


def clean(page):
    assert page.problems == [], page.problems
    assert page.blocked == [], page.blocked


def until(page, check, what):
    for _ in range(100):
        if check():
            return
        page.wait_for_timeout(50)
    assert check(), what


def judge(app, world, *verdicts):
    """What triage said of just these messages. They are stored while the server is stopped, since a triage pass would
    say something of every message."""
    from mailmind.bootstrap.mailbox import store
    with app.stopped():
        c = store.conn()
        try:
            for name, importance, category, action in verdicts:
                answers.save_summary(c, world.pk(name), "A summary.", importance, category, action, "claude-opus-5-5", NOW)
            c.commit()
        finally:
            c.close()


def lunch_needs_a_reply(app, world):
    judge(app, world, (LUNCH[1], 5, "action-needed", "Reply"))


def brief_replies(ai, groups=()):
    ai.responder = lambda req: (fa.answer({"narrative": BRIEF, "groups": list(groups)})
                                if "narrative" in ((req.schema or {}).get("properties") or {}) else None)


def briefs(ai):
    return [r for r in ai.requests if "narrative" in ((r.schema or {}).get("properties") or {})]


def spent_the_daily_limit(app, ai):
    """The day's $15 spent, by one call the fake API says read 3.75 million tokens, at $4 a million."""
    status, reply = fa.answer({"text": "the"})
    ai.queue((status, {**reply, "usage": {"input_tokens": 3_750_000, "output_tokens": 0}}))
    assert app.post("/api/ai/rewrite", {"op": "fix", "text": "teh"}).status == 200
    ai.requests.clear()


def tidied(app):
    """A run of Tidy by itself, for someone who turned it on: the worker's pass, with the moves it queued carried out."""
    assert app.post("/api/preferences", {"auto_tidy": True}).status == 200
    app.work("tidy")
    app.settle()


def card(page, world, name):
    return page.locator(f'.bf-need[data-key="{world.key(name)}"]')


def box(world, name):
    return world.where(name)["server"][0][1]


def test_the_brief_is_written_once_when_the_inbox_opens(page, app, world, ai):
    lunch_needs_a_reply(app, world)
    brief_replies(ai)
    page.goto("/")
    expect(page.locator(".bf-narr")).to_have_text(BRIEF)
    expect(page.locator(".brief-row")).to_contain_text("1 needs you")
    expect(page.get_by_role("heading", level=1, name=re.compile("Good "))).to_be_visible()
    page.reload()
    expect(page.locator(".bf-narr")).to_have_text(BRIEF)
    assert len(briefs(ai)) == 1
    clean(page)


def test_while_its_mail_waits_for_summaries_the_brief_says_it_is_reading_it_then_what_needs_you(page, app, world, ai):
    """Nothing is summarized yet, as after an account's first sync: nothing says that nothing needs you. Once the round
    lands, the brief is read again and written."""
    def answer(req):
        if "narrative" in ((req.schema or {}).get("properties") or {}):
            return fa.answer({"narrative": BRIEF, "groups": []})
        if req.prompt.startswith("You are triaging ONE email"):
            return fa.answer({"summary": "For your information.", "importance": 2, "category": "info", "action": ""})
        return None
    ai.responder = answer
    page.goto("/")
    reading = "Reading your new mail…"
    expect(page.locator("[data-brief-row] small")).to_have_text(reading)
    expect(page.locator(".bf-calm")).to_have_text(reading)
    expect(page.locator("p.bf-wait")).to_have_text(reading)
    assert briefs(ai) == []
    app.work("assess")
    expect(page.locator("[data-brief-row] small")).to_have_text("Nothing needs you")
    expect(page.locator(".bf-calm")).to_have_text("Nothing needs you right now.")
    expect(page.locator(".bf-narr")).to_have_text(BRIEF)
    assert len(briefs(ai)) == 1
    clean(page)


def test_at_the_daily_limit_the_brief_waits_for_a_click(page, app, world, ai):
    lunch_needs_a_reply(app, world)
    spent_the_daily_limit(app, ai)
    brief_replies(ai)
    page.goto("/")
    expect(page.locator(".bf-wait")).to_contain_text("Today’s AI limit is reached, so the brief isn’t written yet.")
    ask = page.get_by_role("alertdialog", name="Today’s AI limit is reached")
    expect(ask).to_be_hidden()
    assert briefs(ai) == []
    page.get_by_role("button", name="Write it").click()
    ask.get_by_role("button", name="Go over the limit").click()
    expect(page.locator(".bf-narr")).to_have_text(BRIEF)
    assert len(briefs(ai)) == 1
    assert all("402" in p for p in page.problems)
    page.problems.clear()
    clean(page)


def test_with_ai_off_the_brief_and_the_review_say_so_and_ask_for_nothing(page, app, world, anthropic_api):
    assert app.post("/api/preferences", {"ai_enabled": True}).status == 200     # AI mode on, with no key: off for want of one
    lunch_needs_a_reply(app, world)
    page.goto("/")
    expect(page.locator(".bf-aioff")).to_contain_text("AI is off: there’s no key for Claude.")
    expect(page.locator(".bf-aioff")).to_contain_text("the sender, subject and opening text of each message in your Inbox go to "
                                                      "Anthropic to be summarized, automatically: the last 30 days’")
    page.get_by_role("button", name="Your week").click()
    expect(page.locator("#rvBody")).to_have_text("AI is off: there’s no key for Claude, so there is no review. Set up AI in Settings")
    page.locator("#rvBody").get_by_role("button", name="Set up AI in Settings").click()
    expect(page.locator("#rvBody")).to_have_count(0)
    expect(page.get_by_role("dialog", name="AI")).to_be_visible()
    expect(page.locator("#aiKey")).to_be_visible()
    assert anthropic_api.requests == []
    assert all("409" in p for p in page.problems)      # the review it was asked for
    assert len(page.problems) == 1
    page.problems.clear()
    clean(page)


def test_turning_ai_on_writes_the_brief_that_ai_off_left_unwritten(page, app, world, anthropic_api, fake_keychain):
    lunch_needs_a_reply(app, world)                     # a fresh install: AI mode off, and no key
    brief_replies(anthropic_api)
    page.goto("/")
    expect(card(page, world, LUNCH[1])).to_be_visible()
    for none in (".bf-narr", ".bf-aioff", ".bf-wait"):
        expect(page.locator(none)).to_have_count(0)

    page.locator("#settingsBtn").click()
    page.get_by_role("tab", name="AI").click()
    dialog = page.get_by_role("dialog", name="AI")
    dialog.get_by_label("AI mode", exact=True).check()
    dialog.get_by_label("API key", exact=True).fill(anthropic_api.KEY)
    dialog.get_by_role("button", name="Keep key").click()
    expect(page.locator("#aiStanding")).to_have_text(re.compile("^AI is on: Claude"))
    page.locator("#setClose").click()
    expect(page.locator(".bf-narr")).to_have_text(BRIEF)
    assert len(briefs(anthropic_api)) == 1
    clean(page)


def test_refresh_writes_it_again(page, app, world, ai):
    lunch_needs_a_reply(app, world)
    brief_replies(ai)
    page.goto("/")
    expect(page.locator(".bf-narr")).to_have_text(BRIEF)
    page.get_by_role("button", name="Refresh brief").click()
    until(page, lambda: len(briefs(ai)) == 2, "the refresh never asked")
    expect(page.locator(".bf-narr")).to_have_text(BRIEF)
    clean(page)


def test_a_card_answers_snoozes_and_files_without_opening_the_message(page, app, world, ai):
    judge(app, world, (LUNCH[1], 5, "action-needed", "Reply"), (RECEIPT, 4, "finance", "Check the total"))
    brief_replies(ai)
    page.goto("/")
    lunch, receipt = card(page, world, LUNCH[1]), card(page, world, RECEIPT)
    expect(lunch.locator(".rchip.need")).to_have_text("Reply")
    lunch.get_by_role("button", name="Reply").click()
    expect(page.locator("#cmpSubject")).to_have_value(re.compile("^Re:"))
    page.keyboard.press("Escape")
    expect(page.locator("#composeModal")).to_be_hidden()

    receipt.get_by_role("button", name=re.compile("^Archive")).click()
    expect(receipt).to_have_count(0)
    until(page, lambda: box(world, RECEIPT) != "INBOX", "the server never archived it")
    expect(page.locator(".bf-tally[data-brief='needs']")).to_contain_text("1 needs you")
    page.locator("#ufUndo").click()
    expect(receipt).to_be_visible()   # a new row, and the same key
    until(page, lambda: box(world, RECEIPT) == "INBOX", "the server never put it back")

    lunch.get_by_role("button", name=re.compile("^Snooze")).click()
    page.get_by_role("button", name="Tomorrow 8am").click()
    expect(lunch).to_have_count(0)
    clean(page)


def ai_turned_off(page):
    """AI mode turned off in Settings, which then closes: the page has read the digest again."""
    page.locator("#settingsBtn").click()
    page.get_by_role("tab", name="AI").click()
    with page.pw.expect_response(lambda r: r.url.endswith("/api/digest") and r.request.method == "GET"):
        page.get_by_role("dialog", name="AI").get_by_label("AI mode", exact=True).uncheck()
    page.locator("#setClose").click()
    expect(page.get_by_role("dialog", name="AI")).to_have_count(0)


def test_with_ai_turned_off_a_card_and_r_open_a_plain_reply_though_one_was_written_ahead(page, app, world, ai):
    lunch_needs_a_reply(app, world)
    assert app.post("/api/preferences", {"draft_ahead": True}).status == 200
    ahead = "Written ahead: Thursday works."

    def reply(req):
        wants = (req.schema or {}).get("properties") or {}
        if "narrative" in wants:
            return fa.answer({"narrative": BRIEF, "groups": []})
        return fa.answer({"body": ahead}) if "body" in wants else None
    ai.responder = reply
    page.goto(f"/m/{w.ADA}/INBOX?key={world.key(LUNCH[1])}&folder=INBOX&uid={world.uid(LUNCH[1])}")
    expect(page.locator(".rd-subj")).to_be_visible()
    page.clock.run_for(1500)                            # stayed on: a reply is written ahead
    expect(page.locator('#reader [data-ca="draftready"]')).to_be_visible()
    page.keyboard.press("Escape")                       # back to the brief, whose card has it too
    lunch = card(page, world, LUNCH[1])
    expect(lunch.get_by_role("button", name="Use draft")).to_be_visible()

    ai_turned_off(page)
    asked = []
    page.on("request", lambda r: asked.append(r.url) if "/api/ai/" in r.url else None)
    calls = len(ai.requests)
    lunch.get_by_role("button", name="Reply").click()   # the card's: a plain reply
    expect(page.locator("#cmpSubject")).to_have_value(re.compile("^Re:"))
    page.heard_all_asked()
    expect(page.get_by_role("textbox", name="message body")).not_to_have_value(re.compile(ahead))
    page.keyboard.press("Escape")
    expect(page.locator("#composeModal")).to_be_hidden()

    lunch.get_by_role("button", name="Read").click()
    expect(page.locator('#reader [data-ca="reply"], #reader [data-tca="reply"]')).to_be_visible()
    page.keyboard.press("r")                            # the key: a plain reply too
    expect(page.locator("#cmpSubject")).to_have_value(re.compile("^Re:"))
    page.heard_all_asked()
    expect(page.get_by_role("textbox", name="message body")).not_to_have_value(re.compile(ahead))
    assert asked == []
    assert len(ai.requests) == calls
    clean(page)


def test_read_opens_the_message_and_escape_comes_back_to_the_brief(page, app, world, ai):
    lunch_needs_a_reply(app, world)
    brief_replies(ai)
    page.goto("/")
    card(page, world, LUNCH[1]).get_by_role("button", name="Read").click()
    expect(page.locator("#reader .brief-pane, #reader.brief-pane")).to_have_count(0)
    expect(page.locator(".brief-row")).not_to_have_class(re.compile(r"\bsel\b"))
    page.keyboard.press("Escape")
    expect(page.locator("#reader")).to_have_class(re.compile("brief-pane"))
    clean(page)


def test_k_above_the_first_row_comes_back_to_the_brief(page, app, world, ai):
    lunch_needs_a_reply(app, world)
    brief_replies(ai)
    page.goto("/")
    expect(page.locator("#rows .row[data-key]").first).to_be_visible()
    page.locator("body").press("j")
    expect(page.locator("#reader")).not_to_have_class(re.compile("brief-pane"))
    page.locator("body").press("k")
    expect(page.locator("#reader")).to_have_class(re.compile("brief-pane"))
    clean(page)


def test_needs_you_opens_a_list_of_its_own_with_a_way_back(page, app, world, ai):
    judge(app, world, (LUNCH[1], 5, "action-needed", "Reply"), (RECEIPT, 4, "finance", "Check the total"))
    brief_replies(ai)
    page.goto("/")
    page.locator(".bf-tally[data-brief='needs']").click()
    expect(page.locator("#listTitle")).to_have_text("Needs you")
    expect(page.locator("#clientCtx")).to_contain_text("from today’s brief")
    expect(page.locator("#rows .row[data-key]")).to_have_count(2)
    expect(page.locator("#count")).to_have_text("2 messages")
    assert page.where().startswith("/brief/needs?")      # what is open in it makes it a place
    page.reload()                                      # the URL says where you are
    expect(page.locator("#rows .row[data-key]")).to_have_count(2)
    page.get_by_role("button", name="‹ Inbox").click()
    expect(page.locator("#listTitle")).to_have_text("Inbox")
    clean(page)


def test_the_tally_of_the_waiting_place_says_waiting_of_both_kinds_and_opens_it(page, app, world, ai):
    """Waiting holds what waits on others and what others wait on you for, so its tally says waiting, of both."""
    from mailmind.bootstrap.mailbox import store
    brief_replies(ai)
    with app.stopped():
        c = store.conn()
        try:
            for name, kind in ((LUNCH[0], "received"), (RECEIPT, "sent")):
                c.execute("INSERT OR REPLACE INTO followups(account, thread_id, kind, state, first_sent_ts, last_msg_ts, "
                          "due_ts, created_at) VALUES(?,?,?,'due',1793500000,1793500000,1793600000,?)",
                          (w.ADA, world.row(name)["thread_id"], kind, NOW))
            c.commit()
        finally:
            c.close()
    page.goto("/")
    tally = page.locator(".bf-tally[data-brief='__waiting__']")
    expect(tally).to_have_text("2 waiting")
    tally.click()
    expect(page.locator("#listTitle")).to_have_text("Waiting")
    expect(page.locator("#rows .row[data-key]")).to_have_count(2)
    clean(page)


def test_see_all_under_who_youre_waiting_on_says_how_many_waiting_holds(page, app, world, ai):
    """The card lists who you're waiting on, three of them; See all opens Waiting, which holds what others wait on you
    for too, so it says how many Waiting holds, as the tally does."""
    from mailmind.bootstrap.mailbox import store
    brief_replies(ai)
    sent = ("ada-inbox-03-newsletter.eml", "ada-inbox-04-ci.eml", RECEIPT, "ada-inbox-06-outlook-invite.eml")
    with app.stopped():
        c = store.conn()
        try:
            for name, kind in ((LUNCH[0], "received"), *((name, "sent") for name in sent)):
                c.execute("INSERT OR REPLACE INTO followups(account, thread_id, kind, state, first_sent_ts, last_msg_ts, "
                          "due_ts, created_at) VALUES(?,?,?,'due',1793500000,1793500000,1793600000,?)",
                          (w.ADA, world.row(name)["thread_id"], kind, NOW))
            c.commit()
        finally:
            c.close()
    page.goto("/")
    expect(page.locator(".bs-wt")).to_have_count(3)
    more = page.locator(".bs-more")
    expect(more).to_have_text("See all 5")
    expect(page.locator(".bf-tally[data-brief='__waiting__']")).to_have_text("5 waiting")
    more.click()
    expect(page.locator("#listTitle")).to_have_text("Waiting")
    expect(page.locator("#rows .row[data-key]")).to_have_count(5)
    clean(page)


def test_a_topic_from_the_brief_lists_its_mail(page, app, world, ai):
    lunch_needs_a_reply(app, world)
    brief_replies(ai, [{"label": "Lunch", "count": 1, "pks": [world.pk(LUNCH[1])]}])
    page.goto("/")
    page.locator(".bf-topic").click()
    expect(page.locator("#listTitle")).to_have_text("Lunch")
    expect(page.locator("#clientCtx")).to_contain_text("from today’s brief")
    expect(page.locator("#rows .row[data-key]")).to_have_count(1)
    page.reload()                                      # the stored brief still names it
    expect(page.locator("#rows .row[data-key]")).to_have_count(1)
    clean(page)


def test_tidy_asks_first_and_files_under_one_undo(page, app, world, ai):
    judge(app, world, (RECEIPT, 1, "info", ""), ("ada-inbox-03-newsletter.eml", 1, "info", ""))
    brief_replies(ai)
    page.goto("/")
    tidy = page.locator(".bf-tidy")
    expect(tidy).to_contain_text("safe to file")
    n = int(tidy.locator("b").inner_text())
    page.locator(".bf-tally[data-brief='tidy']").click()
    ask = page.get_by_role("alertdialog", name=re.compile("low-priority message"))
    ask.get_by_role("button", name="Not now").click()
    assert box(world, RECEIPT) == "INBOX"
    page.locator(".bf-tally[data-brief='tidy']").click()
    ask.get_by_role("button", name="File to Archive").click()
    until(page, lambda: box(world, RECEIPT) != "INBOX", "the server never archived it")
    expect(page.locator("#undoFloat")).to_contain_text(f"Archived {n}")
    expect(page.locator(".bf-tidy")).to_have_count(0)
    page.locator("#ufUndo").click()
    until(page, lambda: box(world, RECEIPT) == "INBOX", "one undo should put them all back")
    clean(page)


def test_commitments_lists_the_to_dos_and_done_files_one(page, app, world, ai):
    judge(app, world, (RECEIPT, 4, "finance", "Check the total"))
    brief_replies(ai)
    page.goto("/")
    page.locator(".bf-tally[data-brief='commits']").click()
    panel = page.get_by_role("dialog", name="Commitments")
    expect(panel.locator(".commit-row")).to_have_count(1)
    expect(panel.locator(".cm-action")).to_have_text("Check the total")
    panel.get_by_role("button", name="Done").click()
    expect(panel.locator(".commit-empty")).to_be_visible()
    until(page, lambda: box(world, RECEIPT) != "INBOX", "the server never archived it")
    page.keyboard.press("Escape")
    expect(panel).to_be_hidden()
    clean(page)


def test_the_week_says_what_went_to_archive_and_what_the_invites_hold(page, app, world, ai):
    lunch_needs_a_reply(app, world)
    assert app.post("/api/archive", world.copy(RECEIPT)).status == 200
    app.settle()
    page.goto("/")
    page.get_by_role("button", name="Your week").click()
    panel = page.get_by_role("dialog", name="Weekly review")
    expect(panel.locator(".rv-stats")).to_contain_text("handled")
    expect(panel.locator(".rv-item", has_text="Design review")).to_contain_text("Nov")
    clean(page)


def test_the_brief_says_what_tidy_filed_shows_it_and_takes_it_all_back(page, app, world, ai):
    judge(app, world, (LUNCH[0], 1, "info", ""), (LUNCH[1], 1, "info", ""))
    tidied(app)
    assert not any(box == "INBOX" for _, box, *_ in world.where(LUNCH[0])["server"])
    page.goto("/")
    run = page.locator("[data-tidy-run]")
    expect(run).to_contain_text("We filed 2 low-priority messages")
    run.get_by_role("button", name="Review").click()
    expect(run.locator("li").first).to_contain_text("Lunch on Thursday?")
    run.get_by_role("button", name="Undo").click()
    until(page, lambda: any(box == "INBOX" for _, box, *_ in world.where(LUNCH[0])["server"]), "it went back")
    expect(run).to_have_count(0)
    clean(page)


def test_the_keys_undo_of_a_run_of_tidy_clears_the_strip(page, app, world, ai):
    judge(app, world, (LUNCH[0], 1, "info", ""))
    tidied(app)
    page.goto("/")
    run = page.locator("[data-tidy-run]")
    expect(run).to_be_visible()
    page.keyboard.press("z")
    until(page, lambda: any(box == "INBOX" for _, box, *_ in world.where(LUNCH[0])["server"]), "it went back")
    expect(run).to_have_count(0)
    clean(page)


def test_a_dismissed_run_of_tidy_stays_dismissed(page, app, world, ai):
    judge(app, world, (LUNCH[0], 1, "info", ""))
    tidied(app)
    page.goto("/")
    run = page.locator("[data-tidy-run]")
    run.get_by_role("button", name="Dismiss").click()
    expect(run).to_have_count(0)
    page.reload()
    expect(page.locator(".bf-card")).to_be_visible()
    expect(run).to_have_count(0)
    clean(page)


def test_your_week_is_written_when_you_open_it_and_an_item_opens(page, app, world, ai):
    lunch_needs_a_reply(app, world)
    page.goto("/")
    page.get_by_role("button", name="Your week").click()
    panel = page.get_by_role("dialog", name="Weekly review")
    expect(panel.locator(".rv-stats")).to_be_visible()
    need = panel.locator(".rv-item.need").first
    expect(need).to_be_visible()
    need.click()
    expect(panel).to_be_hidden()
    expect(page.locator("#reader")).not_to_have_class(re.compile("brief-pane"))
    clean(page)


def test_your_week_read_on_a_later_day_says_the_week_it_looked_back_on(page, app, world, ai):
    from datetime import datetime, timezone
    lunch_needs_a_reply(app, world)
    assert app.post("/api/preferences", {"weekly_review": True}).status == 200
    assert app.post("/api/review", {}).status == 200          # Monday's, as one written ahead would be
    wednesday = datetime(2026, 11, 4, 12, 0, tzinfo=timezone.utc)
    app.clock.move_to(wednesday)
    page.pw.clock.set_system_time(wednesday)
    asked = len(ai.requests)
    page.goto("/")
    page.get_by_role("button", name="Your week").click()
    panel = page.get_by_role("dialog", name="Weekly review")
    expect(panel.locator(".rv-stats")).to_be_visible()
    expect(panel.locator("#rvRange")).to_have_text("\u00b7 the 7 days to Monday")
    assert len([r for r in ai.requests[asked:] if "next" in ((r.schema or {}).get("properties") or {})]) == 0
    clean(page)


def test_mail_landing_elsewhere_says_so_and_takes_you_there(page, app, world, ai):
    brief_replies(ai)
    page.goto("/")
    page.locator('.frow[data-folder="Sent"]').first.click()
    expect(page.locator("#listTitle")).not_to_have_text("Inbox")
    for n in (1, 2):
        world.server.deliver(w.ADA, "INBOX", w.variant(LUNCH[1], Message_ID=f"<lunch-later-{n}@example.test>"))
    assert app.post("/api/refresh", {}).status == 200      # Sync now, which brings both and says so
    pill = page.locator("#newPill")
    expect(pill).to_contain_text("2 new")
    pill.click()
    expect(page.locator("#listTitle")).to_have_text("Inbox")
    expect(pill).to_have_count(0)
    clean(page)


def test_the_page_has_nothing_axe_finds(page, app, world, ai, every_theme):
    judge(app, world, (LUNCH[1], 5, "action-needed", "Reply"), (RECEIPT, 1, "info", ""))
    brief_replies(ai)
    page.goto("/")
    expect(page.locator(".bf-narr")).to_have_text(BRIEF)
    from axe_playwright_python.sync_playwright import Axe
    assert [v["id"] for v in Axe().run(page.pw).response["violations"]] == []
    clean(page)
