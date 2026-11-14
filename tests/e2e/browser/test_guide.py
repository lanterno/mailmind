"""The first-run guide: offered once when there's mail to show, skippable at every step, and reachable again from the help."""
import re

import pytest
from axe_playwright_python.sync_playwright import Axe
from playwright.sync_api import expect

from fixtures import world as w

pytestmark = pytest.mark.browser

FADES_DONE = """Promise.all(document.getAnimations()
    .filter(a => a.effect.getComputedTiming().iterations !== Infinity)
    .map(a => a.finished.catch(() => null)))"""


def clean(page):
    assert page.problems == [], page.problems
    assert page.blocked == [], page.blocked


@pytest.fixture
def first_run(page, app):
    """A person who hasn't seen the guide: what the page fixture sets for every other test, undone."""
    assert app.post("/api/preferences", {"guide_offered": False}).status == 200


def until(page, check, what):
    """`check` once it holds: the page saves that it offered the guide as it shows it, not before."""
    for _ in range(100):
        if check():
            return
        page.wait_for_timeout(50)
    assert check(), what


def inbox(page):
    page.goto(f"/m/{w.ADA}/INBOX")
    return page.get_by_role("dialog", name="Today’s brief")


def kept_as_offered(app):
    """Whether the server keeps the guide as offered, as the page reads it when it opens."""
    return app.get("/api/preferences").json["preferences"]["guide_offered"]


def test_it_is_offered_once_and_a_reload_does_not_offer_it_again(page, app, world, first_run):
    guide = inbox(page)
    expect(guide).to_be_visible()
    expect(guide.get_by_role("button", name="Next")).to_be_focused()
    until(page, lambda: kept_as_offered(app) is True, "never kept as offered")   # offered, finished or not
    page.reload()
    expect(page.locator("#rows")).to_be_visible()
    expect(page.locator(".guide-card")).to_have_count(0)
    clean(page)


def test_it_walks_every_step_by_keyboard_without_firing_a_shortcut(page, world, first_run):
    guide = inbox(page)
    expect(guide).to_be_visible()
    titles = ["Today’s brief", "Folders, and focus reading", "⌘K opens every command", "The Screener"]
    for title in titles[:-1]:
        expect(page.locator("#guideTitle")).to_have_text(title)
        expect(page.locator(".guide-card").get_by_role("button", name="Next")).to_be_focused()
        page.keyboard.press("j")                     # a single key goes nowhere with the guide up
        expect(page.locator("#reader .rd-subj")).to_have_count(0)
        page.keyboard.press("Enter")
    expect(page.locator("#guideTitle")).to_have_text(titles[-1])
    expect(page.locator(".guide-card").get_by_role("button", name="Done")).to_be_focused()
    page.keyboard.press("Enter")
    expect(page.locator(".guide-card")).to_have_count(0)
    clean(page)


def test_skip_and_escape_each_end_it_at_any_step(page, world, first_run):
    guide = inbox(page)
    guide.get_by_role("button", name="Next").click()
    expect(page.locator("#guideTitle")).to_have_text("Folders, and focus reading")
    guide = page.locator(".guide-card")
    guide.get_by_role("button", name="Skip").click()
    expect(page.locator(".guide-card")).to_have_count(0)
    page.reload()
    page.locator("#helpBtn").click()
    page.locator("#legGuide").click()
    expect(page.locator("#guideTitle")).to_have_text("Today’s brief")
    page.keyboard.press("Escape")
    expect(page.locator(".guide-card")).to_have_count(0)
    clean(page)


def test_the_help_shows_it_again_from_anywhere(page, world):
    page.goto(f"/m/{w.ADA}/INBOX?key={world.key('ada-inbox-05-receipt.eml')}&folder=INBOX&uid={world.uid('ada-inbox-05-receipt.eml')}")
    expect(page.locator(".rd-subj")).to_be_visible()
    page.keyboard.press("?")
    page.locator("#legGuide").click()
    expect(page.locator(".legend.show")).to_have_count(0)
    expect(page.locator("#guideTitle")).to_have_text("Today’s brief")
    expect(page.locator(".brief-pane")).to_be_visible()     # it opened on what it points at
    page.keyboard.press("Escape")
    clean(page)


def test_it_is_not_offered_while_the_inbox_is_empty(page, app, world, first_run):
    page.pw.route(re.compile(r"/api/messages\?"), lambda route: route.fulfill(json={"ok": True, "items": [], "has_more": False, "total": 0}))
    page.goto(f"/m/{w.ADA}/INBOX")
    expect(page.locator("#rows")).to_be_visible()
    expect(page.locator(".guide-card")).to_have_count(0)
    assert kept_as_offered(app) is False
    clean(page)


def test_it_does_not_teach_a_key_that_is_switched_off(page, app, world, first_run):
    assert app.post("/api/preferences", {"single_keys": False}).status == 200
    inbox(page).get_by_role("button", name="Next").click()
    expect(page.locator("#guideTitle")).to_have_text("Folders, and focus reading")
    expect(page.locator(".guide-b")).not_to_contain_text("↵")
    expect(page.locator(".guide-b")).to_contain_text("Focus reading")
    clean(page)


def test_it_leaves_out_what_needs_an_ai_that_is_off(page, world, first_run):
    guide = inbox(page)
    expect(guide).to_be_visible()
    for _ in range(3):
        page.locator(".guide-card").get_by_role("button", name="Next").click()
    expect(page.locator("#guideTitle")).to_have_text("The Screener")     # no key here: asking and telling aren't on the way
    clean(page)


def test_with_ai_on_it_says_what_the_search_box_can_do(page, world, ai, first_run):
    guide = inbox(page)
    expect(guide).to_be_visible()
    for title in ("Folders, and focus reading", "⌘K opens every command", "Ask, or tell it what to do"):
        page.locator(".guide-card").get_by_role("button", name="Next").click()
        expect(page.locator("#guideTitle")).to_have_text(title)
    expect(page.locator(".guide-b")).to_contain_text("Start the search box with ?")
    clean(page)


def test_it_points_at_the_step_and_stays_on_screen(page, world, first_run):
    guide = inbox(page)
    expect(guide).to_be_visible()
    box = page.locator(".guide-card").bounding_box()
    spot = page.locator(".guide-spot").bounding_box()
    reader = page.locator(".reader.brief-pane").bounding_box()
    assert abs(spot["x"] + 6 - reader["x"]) < 2 and abs(spot["width"] - 12 - reader["width"]) < 2, (spot, reader)
    assert box["x"] >= 0 and box["y"] >= 0 and box["x"] + box["width"] <= 1440 and box["y"] + box["height"] <= 900, box
    clean(page)


def test_a_phone_gets_the_words_with_nothing_to_point_at(page, world, first_run):
    page.pw.set_viewport_size({"width": 390, "height": 800})
    guide = inbox(page)
    expect(guide).to_be_visible()
    guide.get_by_role("button", name="Next").click()
    box = page.locator(".guide-card").bounding_box()
    assert box["x"] >= 0 and box["x"] + box["width"] <= 390 and box["y"] + box["height"] <= 800, box
    expect(page.locator(".guide-spot.bare")).to_be_visible()        # the rail is in a drawer, so it just says its words
    clean(page)


def test_reduced_motion_is_kept(page, world, first_run):
    page.pw.emulate_media(reduced_motion="reduce")
    guide = inbox(page)
    expect(guide).to_be_visible()
    assert page.locator(".guide-card").evaluate("e => getComputedStyle(e).animationName") == "none"
    clean(page)


def test_the_guide_passes_axe(page, world, first_run, every_theme):
    guide = inbox(page)
    expect(guide).to_be_visible()
    page.pw.evaluate(FADES_DONE)
    found = [(v["id"], [n["html"][:90] for n in v["nodes"]]) for v in Axe().run(page.pw, options={"iframes": False}).response["violations"]]
    assert found == []
    clean(page)
