"""Ask and Do: a question changes nothing, and a bulk action passes its preview."""
import re
import time
from datetime import timedelta

import pytest
from playwright.sync_api import expect

from fakes import anthropic_api as fa

pytestmark = pytest.mark.browser

LUNCH = ("ada-inbox-01-lunch.eml", "ada-inbox-02-lunch-confirm.eml")


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


def mailbox(world, name):
    return world.where(name)["server"][0][1]


def mailboxes(world, names):
    return [mailbox(world, n) for n in names]


def spent_the_daily_limit():
    from mailmind.assistant.application.gateway import local_day
    from mailmind.assistant.application.ports import Spend, Usage
    from mailmind.bootstrap import assistant
    now = time.time()
    assistant.LEDGER.record(Spend(local_day(now), "ask", "claude-opus-5-5", "claude-opus-5-5", "ok", Usage(), 15_000_000, int(now)))


def type_in_search(page, words):
    page.keyboard.press("/")
    box = page.get_by_role("combobox", name="Search")
    box.fill(words)
    box.press("Enter")


def test_ask_answers_cites_and_changes_nothing(page, world, ai):
    pk = world.pk(LUNCH[1])

    def reply(req):
        if req.schema and "answer" in (req.schema.get("properties") or {}):
            return fa.answer({"answer": "Lunch is on Thursday at noon.", "cited_pks": [pk]})
    ai.responder = reply
    opened(page)
    since = world.mark()
    type_in_search(page, "?When is lunch with Lin?")
    expect(page.locator(".ask-answer")).to_have_text("Lunch is on Thursday at noon.")
    expect(page.locator("#count")).to_have_text("1 source")
    assert any("When is lunch with Lin?" in r.prompt for r in ai.requests)
    assert world.writes(since) == []
    page.locator(".ask-src").click()
    expect(page.locator(".ask-src")).to_have_class(re.compile("reading"))
    expect(page.locator(".ask-answer")).to_be_visible()   # the answer stays beside what you opened
    page.get_by_role("combobox", name="Search").click()
    page.get_by_role("combobox", name="Search").press("Escape")
    expect(page.locator(".ask-answer")).to_have_count(0)
    expect(page.locator("#rows .row[data-key]").first).to_be_visible()
    clean(page)


def test_at_the_daily_limit_an_ask_goes_only_when_you_say_so(page, world, ai):
    spent_the_daily_limit()
    ai.responder = lambda req: fa.answer({"answer": "Lunch is on Thursday at noon.", "cited_pks": []})
    opened(page)
    dialog = page.get_by_role("alertdialog", name="Today’s AI limit is reached")
    type_in_search(page, "?When is lunch with Lin?")
    expect(dialog).to_contain_text("MailMind has spent $15.00 on AI today, and its daily limit is $15.00.")
    expect(dialog.get_by_role("button", name="Not now")).to_be_focused()
    dialog.get_by_role("button", name="Not now").click()
    expect(dialog).to_be_hidden()
    expect(page.locator("#rows")).to_contain_text("ask failed")
    asks = lambda: [r for r in ai.requests if "narrative" not in ((r.schema or {}).get("properties") or {})]
    assert asks() == []
    box = page.get_by_role("combobox", name="Search")
    box.fill("?When is lunch with Lin, again?")
    box.press("Enter")
    dialog.get_by_role("button", name="Go over the limit").click()
    expect(page.locator(".ask-answer")).to_have_text("Lunch is on Thursday at noon.")
    assert [r.prompt.count("When is lunch with Lin, again?") for r in asks()] == [1]
    assert all("402" in p for p in page.problems)
    page.problems.clear()
    clean(page)


def do_plan_for(page, ai, flt, verb="archive", intent="act"):
    def reply(req):
        if req.schema and "verb" in (req.schema.get("properties") or {}):
            return fa.answer({"intent": intent, "filter": flt, "verb": verb})
    ai.responder = reply
    opened(page)
    type_in_search(page, "!do that")
    return page.locator(".do-plan")


def test_do_previews_and_moves_nothing_until_confirmed(page, world, ai):
    plan = do_plan_for(page, ai, "from:lin")
    since = world.mark()
    expect(plan).to_contain_text("from:lin")
    expect(plan.get_by_role("button", name=re.compile("^Archive"))).to_be_visible()
    plan.get_by_role("button", name="Cancel").click()
    expect(plan).to_have_count(0)
    assert world.writes(since) == []
    assert mailboxes(world, LUNCH) == ["INBOX", "INBOX"]
    clean(page)


def test_do_confirmed_archives_and_one_undo_brings_it_all_back(page, world, ai):
    plan = do_plan_for(page, ai, "from:lin")
    plan.get_by_role("button", name=re.compile("^Archive")).click()
    expect(plan).to_have_count(0)
    until(page, lambda: mailboxes(world, LUNCH) == ["Archive", "Archive"], "both archived on the server")
    expect(page.get_by_role("combobox", name="Search")).to_have_value("")
    page.locator("#ufUndo").click()
    until(page, lambda: mailboxes(world, LUNCH) == ["INBOX", "INBOX"], "both back on the server")
    clean(page)


def test_a_destructive_plan_takes_a_second_press(page, world, ai):
    plan = do_plan_for(page, ai, "from:lin", verb="trash")
    since = world.mark()
    plan.locator(".do-confirm").click()
    expect(plan.locator(".do-confirm")).to_have_text("Click again to confirm")
    assert world.writes(since) == []
    plan.locator(".do-confirm").click()
    until(page, lambda: mailboxes(world, LUNCH) == ["Trash", "Trash"], "both trashed on the server")
    clean(page)


def test_do_says_when_its_preview_has_expired_and_moves_nothing(page, world, ai, app):
    plan = do_plan_for(page, ai, "from:lin")
    expect(plan.get_by_role("button", name=re.compile("^Archive"))).to_be_visible()
    since = world.mark()
    app.clock.shift(timedelta(minutes=11))
    plan.get_by_role("button", name=re.compile("^Archive")).click()
    expect(page.locator("#toast")).to_contain_text("expired")
    assert world.writes(since) == []
    assert mailboxes(world, LUNCH) == ["INBOX", "INBOX"]
    assert all("410" in p for p in page.problems)
    page.problems.clear()
    clean(page)


def test_a_plan_thats_no_action_is_a_search(page, world, ai):
    do_plan_for(page, ai, "from:lin", intent="search")
    expect(page.locator("#listTitle")).to_have_text("Search")
    assert "q=from%3Alin" in page.url
    clean(page)


def test_show_as_search_runs_the_plans_filter(page, world, ai):
    plan = do_plan_for(page, ai, "from:lin")
    plan.get_by_role("button", name="Show as search").click()
    expect(page.locator("#listTitle")).to_have_text("Search")
    assert "q=from%3Alin" in page.url
    clean(page)


def test_a_bare_bang_explains_itself(page, world):
    opened(page)
    type_in_search(page, "!")
    expect(page.locator(".do-intro")).to_contain_text("before anything happens")
    page.get_by_role("button", name="Close").click()
    expect(page.locator(".do-intro")).to_have_count(0)
    clean(page)
