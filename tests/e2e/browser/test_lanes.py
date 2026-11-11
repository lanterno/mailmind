"""Feed and Paper Trail: two built-in views that read as a river and as a ledger."""
import re

import pytest
from axe_playwright_python.sync_playwright import Axe
from playwright.sync_api import expect

from fixtures import world as w

pytestmark = pytest.mark.browser

NEWS = "ada-inbox-03-newsletter.eml"
RECEIPT = "ada-inbox-05-receipt.eml"


# Axe judges contrast as it finds it, so fade-ins must end first. A looping animation never ends: waiting for one hangs
# for good, since evaluate has no timeout.
FADES_DONE = """Promise.all(document.getAnimations()
    .filter(a => a.effect.getComputedTiming().iterations !== Infinity)
    .map(a => a.finished.catch(() => null)))"""


def clean(page):
    assert page.problems == [], page.problems
    assert page.blocked == [], page.blocked


def until(page, check, what):
    for _ in range(100):
        if check():
            return
        page.wait_for_timeout(50)
    assert check(), what


def lane(page, view):
    page.goto(f"/m/{w.ADA}/INBOX")
    page.locator(f'a.frow[data-view="__lane_{view}__"]').click()
    page.expect_where(f"lane={view}")
    return page


def test_the_feed_reads_as_a_river_and_a_reload_keeps_it(page, world):
    lane(page, "feed")
    expect(page.locator(".feed-river .fr-card")).to_have_count(2)
    card = page.locator(".fr-card", has_text="five new patterns")
    expect(card.locator(".fr-pub")).to_have_text("The Weekly Loom")
    expect(card.locator(".fr-head")).to_have_text("This week: five new patterns")
    expect(page.locator("#listTitle")).to_have_text("Feed")
    expect(page.locator("#count")).to_have_text("2 newsletters")
    expect(page.locator("#clientCtx")).to_contain_text("a calm reading river")
    page.reload()
    expect(page.locator(".feed-river .fr-card")).to_have_count(2)
    expect(page.locator("#listTitle")).to_have_text("Feed")
    clean(page)


def test_a_card_opens_and_says_so(page, world):
    lane(page, "feed")
    page.get_by_role("button", name="This week: five new patterns", exact=True).click()
    expect(page.locator(".rd-subj")).to_have_text("This week: five new patterns")
    expect(page.locator(".fr-card.reading")).to_have_count(1)
    clean(page)


def test_archiving_a_card_files_it_and_undo_brings_it_back(page, world):
    lane(page, "feed")
    page.get_by_role("button", name="Archive “This week: five new patterns”").click()
    expect(page.locator(".fr-card", has_text="five new patterns")).to_have_count(0)
    expect(page.locator("#count")).to_have_text("1 newsletter")
    until(page, lambda: world.where(NEWS)["server"][0][1] != "INBOX", "it left the Inbox")
    expect(page.locator("#ufUndo")).to_be_visible()
    page.locator("#ufUndo").click()
    expect(page.locator(".fr-card", has_text="five new patterns")).to_have_count(1)
    until(page, lambda: world.where(NEWS)["server"][0][1] == "INBOX", "it came back")
    clean(page)


def test_an_empty_feed_says_so(page, world):
    page.pw.route(re.compile(r"/api/search\?"), lambda route: route.fulfill(json={"ok": True, "items": [], "has_more": False, "total": 0}))
    lane(page, "feed")
    expect(page.locator(".nores")).to_have_text("Your Feed is clear — no newsletters waiting.")
    expect(page.locator("#count")).to_have_text("0 newsletters")
    clean(page)


def test_the_paper_trail_is_a_ledger_by_month_with_a_subtotal(page, world):
    world.deliver(w.ADA, "INBOX", w.variant(RECEIPT, Message_ID="<order-1043@thimble.example.test>", Subject="Your receipt: $12.50 paid", Date="Fri, 30 Oct 2026 11:05:00 +0000"))
    world.deliver(w.ADA, "INBOX", w.variant(RECEIPT, Message_ID="<order-1044@thimble.example.test>", Subject="Refund of $2.50 for order 1043", Date="Sat, 31 Oct 2026 11:05:00 +0000"))
    world.deliver(w.ADA, "INBOX", w.variant(RECEIPT, Message_ID="<order-0901@thimble.example.test>", Subject="Your receipt: €30.00 paid", Date="Wed, 30 Sep 2026 11:05:00 +0000"))
    lane(page, "papertrail")
    expect(page.locator("#listTitle")).to_have_text("Paper Trail")
    month = lambda name: page.locator(".pt-month", has_text=name)
    expect(month("October 2026").locator(".pt-mtot")).to_contain_text("$10.00")     # the refund comes off
    expect(month("September 2026").locator(".pt-mtot")).to_contain_text("€30.00")
    refund = page.locator(".pt-row", has_text="Refund of $2.50")
    expect(refund.locator(".pt-amount")).to_have_text("−$2.50")
    expect(refund.locator(".pt-desc")).to_contain_text("↩")
    clean(page)


def test_a_receipt_with_no_amount_says_so_and_opens(page, world):
    lane(page, "papertrail")
    row = page.locator(".pt-row", has_text="Your receipt for order 1042")
    expect(row.locator(".pt-amount")).to_have_text("—")
    row.click()
    expect(page.locator(".rd-subj")).to_have_text("Your receipt for order 1042")
    expect(page.locator(".pt-row.reading")).to_have_count(1)
    page.keyboard.press("Escape")
    expect(page.locator(".pt-row.reading")).to_have_count(0)
    clean(page)


def test_j_moves_through_a_lane(page, world):
    lane(page, "papertrail")
    rows = page.locator(".pt-row")
    expect(rows.first).to_be_visible()
    subjects = rows.locator(".pt-desc").all_text_contents()
    assert len(subjects) > 1
    page.locator("#rows").hover()
    page.keyboard.press("j")
    expect(page.locator(".pt-row.reading .pt-desc")).to_have_text(subjects[0])
    page.keyboard.press("j")
    expect(page.locator(".pt-row.reading .pt-desc")).to_have_text(subjects[1])
    clean(page)


def test_a_lane_in_the_palette_opens_as_a_lane(page, world):
    page.goto(f"/m/{w.ADA}/INBOX")
    expect(page.locator("#monos")).not_to_be_empty()
    page.keyboard.press("ControlOrMeta+k")
    page.get_by_role("combobox", name="Command").fill("Feed (lane)")
    page.keyboard.press("Enter")
    expect(page.locator(".feed-river")).to_be_visible()
    clean(page)


@pytest.mark.parametrize("view,mark", [("feed", ".fr-card"), ("papertrail", ".pt-row")])
def test_a_lane_passes_axe(page, world, view, mark, every_theme):
    lane(page, view)
    expect(page.locator(mark).first).to_be_visible()
    page.pw.evaluate(FADES_DONE)
    found = Axe().run(page.pw, options={"iframes": False}).response["violations"]
    assert found == [], [(v["id"], [n["html"][:90] for n in v["nodes"]]) for v in found]
    clean(page)
