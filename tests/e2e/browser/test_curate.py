"""The curate drawer: flooding senders, each inbox, the learned rules and the audit trail."""
import re

import pytest
from axe_playwright_python.sync_playwright import Axe
from playwright.sync_api import expect

from fixtures import world as w

pytestmark = pytest.mark.browser

NEWS = "ada-inbox-03-newsletter.eml"
LOOM = "news@loom.example.test"
SCAM = "ada-inbox-12-scam.eml"


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


def issues(world, n=2):
    for i in range(n):
        world.deliver(w.ADA, "INBOX", w.variant(NEWS, Message_ID=f"<issue-{300 + i}@loom.example.test>", Subject=f"Issue {300 + i}", Date=f"Fri, {i + 10:02} Oct 2026 07:00:00 +0000"))


def drawer(page, where=None):
    page.goto(where or f"/m/{w.ADA}/INBOX")
    page.locator("#statusBtn").click()
    page.get_by_role("button", name=re.compile("^Curate")).click()
    expect(page.get_by_role("dialog", name="Curate")).to_be_visible()
    return page.get_by_role("dialog", name="Curate")


def test_the_status_menu_opens_the_drawer_and_esc_closes_it(page, world):
    d = drawer(page)
    expect(d.get_by_role("heading", name="Learned rules")).to_be_visible()
    page.keyboard.press("Escape")
    expect(d).to_have_count(0)
    clean(page)


def test_a_rule_changes_how_the_mail_it_covers_reads_at_once(page, world):
    page.goto(f"/m/{w.ADA}/INBOX")
    page.get_by_role("tab", name=re.compile("^Everything")).click()
    row = page.get_by_role("option").filter(has_text="This week: five new patterns")
    expect(row.locator(".jb")).to_be_visible()
    page.locator("#statusBtn").click()
    page.get_by_role("button", name=re.compile("^Curate")).click()
    page.locator("#ruleInput").fill(LOOM)
    page.locator("#ruleInput").press("Enter")
    expect(page.locator(f'#keepList [data-token="{LOOM}"]')).to_be_visible()
    expect(page.locator("#ruleInput")).to_have_value("")
    expect(row.locator(".jb")).to_have_count(0)
    clean(page)


def test_shift_enter_blocks_and_a_chip_removes_its_rule(page, world):
    from mailmind.bootstrap.curation import rules
    drawer(page)
    page.locator("#ruleInput").fill("pests.example.test")
    page.locator("#ruleInput").press("Shift+Enter")
    chip = page.locator('#blockList [data-token="pests.example.test"]')
    expect(chip).to_be_visible()
    assert "pests.example.test" in rules.load()["block_domains"]
    chip.click()
    expect(chip).to_have_count(0)
    assert "pests.example.test" not in rules.load()["block_domains"]
    clean(page)


def test_each_inbox_says_what_it_holds_and_a_sweep_asks_first(page, world):
    d = drawer(page)
    mod = d.locator(f'.acctmod[data-acct="{w.ADA}"]')
    sweep = mod.get_by_role("button", name=re.compile(r"^Sweep \d+"))
    sweep.click()
    confirm = mod.get_by_role("button", name="Confirm")
    expect(confirm).to_be_focused()
    before = world.mark()
    mod.get_by_role("button", name="Abort").click()
    expect(confirm).to_have_count(0)
    assert world.writes(before) == []
    sweep.click()
    confirm.click()
    expect(page.locator("#toast")).to_contain_text("Swept")
    until(page, lambda: world.writes(before), "the sweep moved mail")
    clean(page)


def test_the_flood_list_cleans_up_a_sender_after_two_clicks_and_undo_reverses_it(page, world):
    from mailmind.bootstrap.curation import rules
    issues(world)
    d = drawer(page)
    row = d.locator(f'.flood-row[data-flood="{LOOM}"]')
    expect(row.locator(".flood-chip").first).to_contain_text("3 msgs")
    row.get_by_role("button", name=f"Clean up {LOOM}").click()
    plan = page.get_by_role("dialog", name="Clean up a sender")
    expect(plan.locator(".do-hd")).to_contain_text(LOOM)
    expect(plan.locator(".do-line").first).to_contain_text(LOOM)
    plan.locator(".do-unsubopt input").uncheck()
    go = plan.locator(".do-confirm")
    expect(go).to_have_text("Clean up")
    before = world.mark()
    go.click()
    expect(go).to_have_text("Click again to confirm")
    assert world.writes(before) == []
    assert LOOM not in rules.load()["block_senders"]
    go.click()
    expect(plan).to_have_count(0)
    expect(page.locator("#toast")).to_contain_text("Blocked · swept 3")
    assert LOOM in rules.load()["block_senders"]
    until(page, lambda: world.where(NEWS)["server"][0][1] == "Trash", "it went to the Trash")
    expect(page.locator(f'.flood-row[data-flood="{LOOM}"]')).to_have_count(0)
    page.locator("#ufUndo").click()         # over the drawer, which stays open for the next sender
    expect(d).to_be_visible()
    until(page, lambda: LOOM not in rules.load()["block_senders"], "the block is gone")
    until(page, lambda: world.where(NEWS)["server"][0][1] == "INBOX", "it came back")
    clean(page)


def test_the_whole_domain_can_be_chosen_and_changes_what_the_plan_says(page, world):
    issues(world)
    d = drawer(page)
    d.get_by_role("button", name=f"Clean up {LOOM}").click()
    plan = page.get_by_role("dialog", name="Clean up a sender")
    go = plan.locator(".do-confirm")
    go.click()
    expect(go).to_have_text("Click again to confirm")
    plan.get_by_role("button", name="whole domain").click()
    expect(plan.locator(".do-line").first).to_contain_text("loom.example.test")
    expect(go).to_have_text("Clean up")                                 # a changed plan asks again
    plan.get_by_role("button", name="Cancel").click()
    expect(plan).to_have_count(0)
    expect(d).to_be_visible()
    clean(page)


def test_the_audit_trail_shows_what_was_done(page, world):
    issues(world)
    d = drawer(page)
    d.get_by_role("button", name=f"Clean up {LOOM}").click()
    plan = page.get_by_role("dialog", name="Clean up a sender")
    plan.locator(".do-unsubopt input").uncheck()
    go = plan.locator(".do-confirm")
    go.click()
    expect(go).to_have_text("Click again to confirm")
    go.click()
    expect(page.locator("#console .ln").first).to_be_visible()
    clean(page)


def test_the_drawer_and_the_plan_pass_axe(page, world, every_theme):
    found = lambda: [(v["id"], [n["html"][:90] for n in v["nodes"]]) for v in Axe().run(page.pw, options={"iframes": False}).response["violations"]]
    issues(world)
    d = drawer(page)
    expect(d.locator(".flood-row").first).to_be_visible()
    expect(d.locator(".acctmod").first).to_be_visible()
    page.pw.evaluate(FADES_DONE)
    assert found() == []
    d.get_by_role("button", name=f"Clean up {LOOM}").click()
    expect(page.get_by_role("dialog", name="Clean up a sender")).to_be_visible()
    page.pw.evaluate(FADES_DONE)
    assert found() == []
    clean(page)
