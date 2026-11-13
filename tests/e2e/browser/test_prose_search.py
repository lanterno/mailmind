"""A sentence in the search box: searched as written, then as the search it reads as, under a chip that takes that back. The model
is asked on Enter and only then, and a plain keyword search never asks it."""
import pytest
from playwright.sync_api import expect

from fakes import anthropic_api as fa
from fixtures import world as w

pytestmark = pytest.mark.browser

SENTENCE = "emails from lin about lunch"
FILTER = "from:lin subject:lunch"


def clean(page):
    assert page.problems == [], page.problems
    assert page.blocked == [], page.blocked


def box(page):
    return page.get_by_role("combobox", name="Search")


def inbox(page):
    """The Inbox, with what the page knows of AI loaded: until it knows, a sentence stays literal."""
    with page.pw.expect_response("**/api/digest"):
        page.goto(f"/m/{w.ADA.replace('@', '%40')}/INBOX")
    expect(page.locator("#listTitle")).to_have_text("Inbox")
    expect(page.locator("#statusTxt")).not_to_have_text("Connecting…")     # the page has drawn what it read
    return page


def search(page, words):
    box(page).fill(words)
    box(page).press("Enter")


def asks_the_planner(req):
    return bool(req.schema) and "verb" in (req.schema.get("properties") or {})


def plans(ai):
    return [r for r in ai.requests if asks_the_planner(r)]


def translating_to(ai, flt=FILTER, **more):
    ai.responder = lambda req: fa.answer({"intent": "search", "filter": flt, **more}) if asks_the_planner(req) else None


def planner_requests(page):
    seen = []
    page.on("request", lambda r: seen.append(r.url) if "/api/ai/do-plan" in r.url else None)
    return seen


def chip(page):
    return page.locator("#translatedChip")


def test_a_sentence_is_translated_and_the_chip_gets_the_literal_search_back(page, world, ai):
    translating_to(ai)
    inbox(page)
    search(page, SENTENCE)
    expect(box(page)).to_have_value(FILTER)
    expect(chip(page)).to_contain_text(f"Translated from “{SENTENCE}”")
    assert len(plans(ai)) == 1 and SENTENCE in plans(ai)[0].prompt
    expect(page.locator("#rows .row[data-key]").first).to_be_visible()
    chip(page).get_by_role("button").click()
    expect(chip(page)).to_have_count(0)
    expect(box(page)).to_have_value(SENTENCE)
    page.expect_where(r"/search\?q=emails")
    assert "asked=" not in page.where()
    assert len(plans(ai)) == 1
    clean(page)


def test_a_translation_taken_back_is_not_made_again_on_enter(page, world, ai):
    translating_to(ai)
    inbox(page)
    search(page, SENTENCE)
    expect(chip(page)).to_be_visible()
    chip(page).get_by_role("button").click()
    expect(box(page)).to_have_value(SENTENCE)
    seen = planner_requests(page)
    box(page).press("Enter")
    page.heard_all_asked()                  # a translation is asked for as Enter is pressed
    assert seen == [] and len(plans(ai)) == 1
    expect(chip(page)).to_have_count(0)
    expect(box(page)).to_have_value(SENTENCE)
    clean(page)


def test_the_same_sentence_again_costs_nothing(page, world, ai):
    translating_to(ai)
    inbox(page)
    search(page, SENTENCE)
    expect(chip(page)).to_be_visible()
    search(page, "lunch")
    expect(chip(page)).to_have_count(0)
    search(page, SENTENCE)
    expect(chip(page)).to_be_visible()
    assert len(plans(ai)) == 1
    clean(page)


def test_the_chip_says_so_while_it_is_going(page, world, ai):
    translating_to(ai)
    inbox(page)
    held = []
    page.pw.route("**/api/ai/do-plan", lambda route: held.append(route))
    search(page, SENTENCE)
    expect(page.locator("#clientCtx .ctx-chip")).to_contain_text("Translating")
    page.expect_where(r"/search\?q=emails")     # the literal search is already there
    held[0].continue_()
    expect(chip(page)).to_contain_text("Translated from")
    clean(page)


def test_typing_a_sentence_asks_nothing(page, world, ai):
    translating_to(ai)
    inbox(page)
    seen = planner_requests(page)
    box(page).fill(SENTENCE)
    page.clock.run_for(400)                 # whatever typing set off on a timer has gone
    page.heard_all_asked()
    assert seen == [] and plans(ai) == []
    clean(page)


@pytest.mark.parametrize("words", ["lunch", "quarterly budget review", "from:lin lunch this week", "bank of america"])
def test_a_plain_search_asks_nothing(page, world, ai, words):
    translating_to(ai)
    inbox(page)
    seen = planner_requests(page)
    search(page, words)
    page.heard_all_asked()
    assert seen == [] and plans(ai) == []
    expect(chip(page)).to_have_count(0)
    clean(page)


def test_with_ai_off_a_sentence_stays_literal(page, world):
    inbox(page)
    expect(page.locator("#listTitle")).to_have_text("Inbox")
    seen = planner_requests(page)
    search(page, SENTENCE)
    page.expect_where(r"/search\?q=emails")
    page.heard_all_asked()
    assert seen == []
    expect(box(page)).to_have_value(SENTENCE)
    expect(page.locator("#clientCtx .ctx-chip")).to_have_count(0)
    clean(page)


def test_a_planner_that_fails_leaves_the_literal_search(page, world, ai):
    translating_to(ai)
    inbox(page)
    page.pw.route("**/api/ai/do-plan", lambda route: route.fulfill(status=502, content_type="application/problem+json", body='{"type":"/problems/unreachable","title":"unreachable","status":502}'))
    search(page, SENTENCE)
    page.expect_where(r"/search\?q=emails")
    expect(page.locator("#clientCtx .ctx-chip")).to_have_count(0, timeout=2000)
    expect(box(page)).to_have_value(SENTENCE)
    assert all("502" in p for p in page.problems), page.problems
    page.problems.clear()
    clean(page)


def spent_the_daily_limit(app, ai):
    """The day's $15 spent, by one call the fake API says read 3.75 million tokens, at $4 a million."""
    status, reply = fa.answer({"text": "the"})
    ai.queue((status, {**reply, "usage": {"input_tokens": 3_750_000, "output_tokens": 0}}))
    assert app.post("/api/ai/rewrite", {"op": "fix", "text": "teh"}).status == 200
    ai.requests.clear()


def test_over_the_days_limit_it_asks_nobody_and_stays_literal(page, app, world, ai):
    spent_the_daily_limit(app, ai)
    translating_to(ai)
    inbox(page)
    search(page, SENTENCE)
    page.expect_where(r"/search\?q=emails")
    expect(page.locator("#clientCtx .ctx-chip")).to_have_count(0, timeout=2000)
    expect(page.get_by_role("alertdialog")).to_have_count(0)
    assert plans(ai) == []
    assert all("402" in p for p in page.problems), page.problems
    page.problems.clear()
    clean(page)


def test_a_sentence_that_reads_like_a_command_is_only_a_search(page, world, ai):
    ai.responder = lambda req: fa.answer({"intent": "act", "verb": "trash", "filter": "from:lin"}) if asks_the_planner(req) else None
    inbox(page)
    since = world.mark()
    search(page, "delete everything from lin")
    expect(box(page)).to_have_value("from:lin")
    expect(chip(page)).to_be_visible()
    expect(page.locator(".do-plan")).to_have_count(0)
    assert world.writes(since) == []
    clean(page)


def test_an_empty_answer_leaves_the_literal_search(page, world, ai):
    translating_to(ai, flt="")
    inbox(page)
    search(page, SENTENCE)
    expect(page.locator("#clientCtx .ctx-chip")).to_have_count(0, timeout=2000)
    expect(box(page)).to_have_value(SENTENCE)
    clean(page)


def test_leaving_before_it_answers_leaves_you_where_you_went(page, world, ai):
    translating_to(ai)
    inbox(page)
    held = []
    page.pw.route("**/api/ai/do-plan", lambda route: held.append(route))
    search(page, SENTENCE)
    page.expect_where(r"/search\?q=emails")
    box(page).fill("lunch")
    box(page).press("Enter")
    page.expect_where(r"/search\?q=lunch")
    held[0].continue_()
    page.wait_for_timeout(300)              # time for the late answer to be taken, if it would be: nothing on the page says it was
    expect(box(page)).to_have_value("lunch")
    expect(chip(page)).to_have_count(0)
    clean(page)


def test_a_reload_keeps_the_chip_and_asks_nothing(page, world, ai):
    translating_to(ai)
    inbox(page)
    search(page, SENTENCE)
    expect(chip(page)).to_be_visible()
    page.reload()
    expect(chip(page)).to_contain_text(SENTENCE)
    assert len(plans(ai)) == 1
    clean(page)


def test_back_goes_where_you_were_not_to_the_search_it_replaced(page, world, ai):
    translating_to(ai)
    inbox(page)
    search(page, SENTENCE)
    expect(chip(page)).to_be_visible()
    page.go_back()
    expect(page.locator("#listTitle")).to_have_text("Inbox")
    clean(page)
