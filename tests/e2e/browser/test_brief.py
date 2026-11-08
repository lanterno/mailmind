"""Today's brief: what the Inbox opens on, and the lists and panels it opens."""
import re
import time

import pytest
from playwright.sync_api import expect

from fakes import anthropic_api as fa
from fixtures import world as w
from mailmind.platform import sse
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


def judge(world, *verdicts):
    from mailmind.bootstrap.mailbox import store
    c = store.conn()
    try:
        for name, importance, category, action in verdicts:
            answers.save_summary(c, world.pk(name), "A summary.", importance, category, action, "claude-opus-5-5", NOW)
        c.commit()
    finally:
        c.close()


def lunch_needs_a_reply(world):
    judge(world, (LUNCH[1], 5, "action-needed", "Reply"))


def brief_replies(ai, groups=()):
    ai.responder = lambda req: (fa.answer({"narrative": BRIEF, "groups": list(groups)})
                                if "narrative" in ((req.schema or {}).get("properties") or {}) else None)


def briefs(ai):
    return [r for r in ai.requests if "narrative" in ((r.schema or {}).get("properties") or {})]


def spent_the_daily_limit():
    from mailmind.assistant.application.gateway import local_day
    from mailmind.assistant.application.ports import Spend, Usage
    from mailmind.bootstrap import assistant
    now = time.time()
    assistant.LEDGER.record(Spend(local_day(now), "ask", "claude-opus-5-5", "claude-opus-5-5", "ok", Usage(), 15_000_000, int(now)))


def card(page, world, name):
    return page.locator(f'.bf-need[data-key="{world.key(name)}"]')


def box(world, name):
    return world.where(name)["server"][0][1]


def test_the_brief_is_written_once_when_the_inbox_opens(page, world, ai):
    lunch_needs_a_reply(world)
    brief_replies(ai)
    page.goto("/")
    expect(page.locator(".bf-narr")).to_have_text(BRIEF)
    expect(page.locator(".brief-row")).to_contain_text("1 needs you")
    expect(page.get_by_role("heading", level=1, name=re.compile("Good "))).to_be_visible()
    page.reload()
    expect(page.locator(".bf-narr")).to_have_text(BRIEF)
    assert len(briefs(ai)) == 1
    clean(page)


def test_at_the_daily_limit_the_brief_waits_for_a_click(page, world, ai):
    lunch_needs_a_reply(world)
    spent_the_daily_limit()
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


def test_with_ai_off_the_brief_and_the_review_say_so_and_ask_for_nothing(page, world, anthropic_api):
    lunch_needs_a_reply(world)
    page.goto("/")
    expect(page.locator(".bf-aioff")).to_contain_text("AI is off.")
    page.get_by_role("button", name="Your week").click()
    expect(page.locator("#rvBody")).to_have_text(
        "AI is off, so there is no review: Claude writes it, and MailMind has no Anthropic API key.")
    assert anthropic_api.requests == []
    assert all("409" in p for p in page.problems)      # the review it was asked for
    assert len(page.problems) == 1
    page.problems.clear()
    clean(page)


def test_refresh_writes_it_again(page, world, ai):
    lunch_needs_a_reply(world)
    brief_replies(ai)
    page.goto("/")
    expect(page.locator(".bf-narr")).to_have_text(BRIEF)
    page.get_by_role("button", name="Refresh brief").click()
    until(page, lambda: len(briefs(ai)) == 2, "the refresh never asked")
    expect(page.locator(".bf-narr")).to_have_text(BRIEF)
    clean(page)


def test_a_card_answers_snoozes_and_files_without_opening_the_message(page, world, ai):
    judge(world, (LUNCH[1], 5, "action-needed", "Reply"), (RECEIPT, 4, "finance", "Check the total"))
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


def test_read_opens_the_message_and_escape_comes_back_to_the_brief(page, world, ai):
    lunch_needs_a_reply(world)
    brief_replies(ai)
    page.goto("/")
    card(page, world, LUNCH[1]).get_by_role("button", name="Read").click()
    expect(page.locator("#reader .brief-pane, #reader.brief-pane")).to_have_count(0)
    expect(page.locator(".brief-row")).not_to_have_class(re.compile(r"\bsel\b"))
    page.keyboard.press("Escape")
    expect(page.locator("#reader")).to_have_class(re.compile("brief-pane"))
    clean(page)


def test_k_above_the_first_row_comes_back_to_the_brief(page, world, ai):
    lunch_needs_a_reply(world)
    brief_replies(ai)
    page.goto("/")
    expect(page.locator("#rows .row[data-key]").first).to_be_visible()
    page.locator("body").press("j")
    expect(page.locator("#reader")).not_to_have_class(re.compile("brief-pane"))
    page.locator("body").press("k")
    expect(page.locator("#reader")).to_have_class(re.compile("brief-pane"))
    clean(page)


def test_needs_you_opens_a_list_of_its_own_with_a_way_back(page, world, ai):
    judge(world, (LUNCH[1], 5, "action-needed", "Reply"), (RECEIPT, 4, "finance", "Check the total"))
    brief_replies(ai)
    page.goto("/")
    page.locator(".bf-tally[data-brief='needs']").click()
    expect(page.locator("#listTitle")).to_have_text("Needs you")
    expect(page.locator("#clientCtx")).to_contain_text("from today’s brief")
    expect(page.locator("#rows .row[data-key]")).to_have_count(2)
    expect(page.locator("#count")).to_have_text("2 messages")
    assert "/brief/needs" in page.url
    page.reload()                                      # the URL says where you are
    expect(page.locator("#rows .row[data-key]")).to_have_count(2)
    page.get_by_role("button", name="‹ Inbox").click()
    expect(page.locator("#listTitle")).to_have_text("Inbox")
    clean(page)


def test_a_topic_from_the_brief_lists_its_mail(page, world, ai):
    lunch_needs_a_reply(world)
    brief_replies(ai, [{"label": "Lunch", "count": 1, "pks": [world.pk(LUNCH[1])]}])
    page.goto("/")
    page.locator(".bf-topic").click()
    expect(page.locator("#listTitle")).to_have_text("Lunch")
    expect(page.locator("#clientCtx")).to_contain_text("from today’s brief")
    expect(page.locator("#rows .row[data-key]")).to_have_count(1)
    page.reload()                                      # the stored brief still names it
    expect(page.locator("#rows .row[data-key]")).to_have_count(1)
    clean(page)


def test_tidy_asks_first_and_files_under_one_undo(page, world, ai):
    judge(world, (RECEIPT, 1, "info", ""), ("ada-inbox-03-newsletter.eml", 1, "info", ""))
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


def test_commitments_lists_the_to_dos_and_done_files_one(page, world, ai):
    judge(world, (RECEIPT, 4, "finance", "Check the total"))
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


def test_your_week_is_written_when_you_open_it_and_an_item_opens(page, world, ai):
    lunch_needs_a_reply(world)
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


def test_mail_landing_elsewhere_says_so_and_takes_you_there(page, world, ai):
    brief_replies(ai)
    page.goto("/")
    page.locator('.frow[data-folder="Sent"]').first.click()
    expect(page.locator("#listTitle")).not_to_have_text("Inbox")
    sse.publish("new-mail", {"account": w.ADA, "folder": "INBOX", "count": 2})
    pill = page.locator("#newPill")
    expect(pill).to_contain_text("2 new")
    pill.click()
    expect(page.locator("#listTitle")).to_have_text("Inbox")
    expect(pill).to_have_count(0)
    clean(page)


def test_the_page_has_nothing_axe_finds(page, world, ai):
    judge(world, (LUNCH[1], 5, "action-needed", "Reply"), (RECEIPT, 1, "info", ""))
    brief_replies(ai)
    page.goto("/")
    expect(page.locator(".bf-narr")).to_have_text(BRIEF)
    from axe_playwright_python.sync_playwright import Axe
    assert [v["id"] for v in Axe().run(page.pw).response["violations"]] == []
    clean(page)
