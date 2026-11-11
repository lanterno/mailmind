"""The search box as you type: what it offers, how you take one, and holding a search to the conversation you're reading."""
import re

import pytest
from axe_playwright_python.sync_playwright import Axe
from playwright.sync_api import expect

from fixtures import world as w

pytestmark = pytest.mark.browser

LUNCH = ("ada-inbox-01-lunch.eml", "ada-inbox-02-lunch-confirm.eml")
QUILT = "ada-inbox-11-html-cid.eml"
FADES_DONE = """Promise.all(document.getAnimations()
    .filter(a => a.effect.getComputedTiming().iterations !== Infinity)
    .map(a => a.finished.catch(() => null)))"""


def clean(page):
    assert page.problems == [], page.problems
    assert page.blocked == [], page.blocked


def box(page):
    return page.get_by_role("combobox", name="Search")


def listed(page):
    return page.get_by_role("listbox", name="Search suggestions")


def offers(page):
    return listed(page).get_by_role("option")


def inbox(page):
    page.goto(f"/m/{w.ADA.replace('@', '%40')}/INBOX")
    expect(page.locator("#listTitle")).to_have_text("Inbox")
    return page


def rows(page):
    return page.locator("#rows .row[data-key]")


def read_the_lunch_conversation(page):
    inbox(page)
    page.get_by_role("tab", name=re.compile("^Everything")).click()
    rows(page).filter(has=page.locator(".thr-count")).filter(has_text="unch").first.click()
    expect(page.locator("#thread .tmsg")).to_have_count(3)


def test_typing_offers_senders_and_the_keys_take_one_and_search_it(page, world):
    inbox(page)
    box(page).fill("lin")
    expect(offers(page).first).to_contain_text("Lin Mori")
    page.keyboard.press("ArrowDown")
    expect(box(page)).to_have_attribute("aria-activedescendant", offers(page).first.get_attribute("id"))
    page.keyboard.press("Enter")
    page.expect_where(r"/search\?q=from%3Alin%40example\.test")
    expect(box(page)).to_have_value("from:lin@example.test")
    expect(listed(page)).to_have_count(0)
    expect(rows(page).first).to_be_visible()
    clean(page)


def test_a_click_takes_one_too(page, world):
    inbox(page)
    box(page).fill("from:loom")
    offers(page).filter(has_text="everyone at this domain").click()
    page.expect_where(r"/search\?q=from%3Aloom\.example\.test")
    expect(box(page)).to_be_focused()
    clean(page)


def test_an_operator_goes_on_to_its_values(page, world):
    inbox(page)
    box(page).fill("i")
    offers(page).filter(has_text="is:").click()
    expect(box(page)).to_have_value("is:")
    expect(offers(page).first).to_contain_text("is:unread")
    assert "/search" not in page.where()
    clean(page)


def test_tab_finishes_the_word_and_leaves_it_to_you_to_search(page, world):
    inbox(page)
    box(page).fill("is:unr")
    expect(offers(page)).to_have_count(1)
    page.keyboard.press("ArrowDown")
    page.keyboard.press("Tab")
    expect(box(page)).to_have_value("is:unread ")
    expect(box(page)).to_be_focused()
    assert "/search" not in page.where()
    clean(page)


def test_enter_with_nothing_picked_searches_what_is_typed(page, world):
    inbox(page)
    box(page).fill("lin")
    expect(offers(page).first).to_be_visible()
    page.keyboard.press("Enter")
    page.expect_where(r"/search\?q=lin")
    expect(box(page)).to_have_value("lin")
    expect(listed(page)).to_have_count(0)
    clean(page)


def test_escape_takes_the_list_away_before_it_takes_the_search(page, world):
    inbox(page)
    box(page).fill("lin")
    expect(offers(page).first).to_be_visible()
    page.keyboard.press("Escape")
    expect(listed(page)).to_have_count(0)
    expect(box(page)).to_have_value("lin")
    page.keyboard.press("Escape")
    expect(box(page)).to_have_value("")
    clean(page)


def test_a_click_away_takes_the_list_away(page, world):
    inbox(page)
    box(page).fill("lin")
    expect(offers(page).first).to_be_visible()
    box(page).blur()
    expect(listed(page)).to_have_count(0)
    clean(page)


def test_a_question_or_a_command_is_offered_nothing(page, world):
    inbox(page)
    with page.pw.expect_response(re.compile(r"/api/suggest\?")) as answer:
        box(page).fill("? when is lin")
        page.clock.run_for(400)             # past the pause before what is typed is looked up
    assert answer.value.json()["items"] == []
    expect(listed(page)).to_have_count(0)
    clean(page)


def test_the_list_passes_axe(page, world):
    found = lambda: [(v["id"], [n["html"][:90] for n in v["nodes"]]) for v in Axe().run(page.pw, options={"iframes": False}).response["violations"]]
    inbox(page)
    box(page).fill("lin")
    expect(offers(page).first).to_be_visible()
    page.keyboard.press("ArrowDown")
    page.pw.evaluate(FADES_DONE)
    assert found() == []


def open_hit(page, world, name):
    row = page.locator('#rows .row[data-key="{key}"][data-folder="{folder}"]'.format(**world.copy(name)))
    row.click()
    page.expect_where(rf"key={world.key(name)}")


def test_a_search_can_be_held_to_the_conversation_open_and_let_go(page, world):
    inbox(page)
    box(page).fill("from:lin")
    page.keyboard.press("Enter")
    open_hit(page, world, LUNCH[0])
    total = rows(page).count()
    assert total >= 3
    scope = page.locator("#scopeThread")
    expect(scope).to_have_text("this conversation only")
    scope.click()
    page.expect_where(r"[?&]thread=")
    expect(rows(page)).to_have_count(2)
    expect(page.locator("#scopeTog")).to_have_count(0)
    expect(page.locator(".rd-iframe, #thread").first).to_be_visible()      # what you were reading stays open
    scope.click()
    page.expect_where(r"[?&]thread=", present=False)
    expect(rows(page)).to_have_count(total)
    clean(page)


def test_the_box_offers_the_conversation_you_are_reading(page, world):
    read_the_lunch_conversation(page)
    box(page).fill("noodle")
    first = offers(page).first
    expect(first).to_contain_text("in this conversation only")
    first.click()
    page.expect_where(r"/search\?q=noodle&account=.*&thread=")
    expect(page.locator("#thread .tmsg")).to_have_count(3)                  # still reading it
    expect(page.locator("#scopeThread")).to_have_text("this conversation only ✕")
    clean(page)


def test_the_next_search_stays_held_to_the_conversation(page, world):
    read_the_lunch_conversation(page)
    box(page).fill("noodle")
    offers(page).first.click()
    page.expect_where(r"[?&]thread=")
    box(page).fill("thursday")
    page.keyboard.press("Enter")
    page.expect_where(r"/search\?q=thursday&account=.*&thread=")
    clean(page)
