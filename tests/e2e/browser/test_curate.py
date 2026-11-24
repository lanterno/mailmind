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


def blocked(app, kind="block_senders"):
    """What the server's learned rules block, as the drawer reads them."""
    return app.get("/api/rules").json["rules"][kind]


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


def test_marking_senders_cleans_them_up_as_one_and_one_undo_takes_it_back(page, app, world, ai):
    issues(world, 3)
    body = drawer(page)
    row = body.locator(f'.flood-row[data-flood="{LOOM}"]')
    expect(row).to_be_visible()
    row.get_by_role("checkbox").check()
    expect(page.get_by_role("dialog", name="Clean up a sender")).to_have_count(0)       # marking is not the row's click
    assert ai.requests == []                                                            # nor does it ask anyone
    page.locator(".flood-bar").get_by_role("button", name="Clean up 1").click()
    panel = page.get_by_role("dialog", name="Clean up these senders")
    expect(panel.locator(f'[data-batch-sender="{LOOM}"]')).to_contain_text("4 to Trash")
    expect(panel.locator(".batch-why")).to_contain_text("stub text")                    # the model's sentence, as the fake answers
    assert LOOM not in blocked(app)                                                     # a preview decides nothing
    panel.get_by_role("button", name="Clean up 1").click()
    assert LOOM not in blocked(app)                                                     # the first click only arms it
    panel.get_by_role("button", name="Click again to confirm").click()
    until(page, lambda: LOOM in blocked(app), "the block")
    expect(page.locator(".flood-bar")).to_have_count(0)
    until(page, lambda: not any(box == "INBOX" for _, box, *_ in world.where(NEWS)["server"]), "the sweep")
    page.keyboard.press("Escape")
    page.keyboard.press("z")
    until(page, lambda: LOOM not in blocked(app), "one undo reverses the block")
    until(page, lambda: any(box == "INBOX" for _, box, *_ in world.where(NEWS)["server"]), "one undo reverses the sweep")
    clean(page)


def test_with_ai_off_cleaning_up_senders_asks_nothing_of_why_each_is_there(page, app, world):
    issues(world, 3)                                                                    # a fresh install: AI mode off
    asked = []
    page.on("request", lambda r: asked.append(r.url) if "/api/cleanup/rationale" in r.url else None)
    body = drawer(page)
    body.locator(f'.flood-row[data-flood="{LOOM}"]').get_by_role("checkbox").check()
    page.locator(".flood-bar").get_by_role("button", name="Clean up 1").click()
    panel = page.get_by_role("dialog", name="Clean up these senders")
    expect(panel.locator(f'[data-batch-sender="{LOOM}"]')).to_contain_text("4 to Trash")
    expect(panel.locator(".do-scope")).to_have_text(re.compile(r"^\d+ inbox messages? go to Trash$"))
    expect(panel.locator(".batch-why")).to_have_count(0)
    page.heard_all_asked()
    assert asked == []
    expect(panel).not_to_contain_text("Writing why")
    clean(page)


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


def test_shift_enter_blocks_and_a_chip_removes_its_rule(page, app, world):
    drawer(page)
    page.locator("#ruleInput").fill("pests.example.test")
    page.locator("#ruleInput").press("Shift+Enter")
    chip = page.locator('#blockList [data-token="pests.example.test"]')
    expect(chip).to_be_visible()
    assert "pests.example.test" in blocked(app, "block_domains")
    chip.click()
    expect(chip).to_have_count(0)
    assert "pests.example.test" not in blocked(app, "block_domains")
    clean(page)


def test_a_rule_that_names_no_sender_is_refused_in_words_and_stays_to_be_put_right(page, app, world):
    """A number pasted before the address: the server's words say why, and what was typed stays."""
    drawer(page)
    field = page.locator("#ruleInput")
    field.fill("50506 spam@lottery.example.test")
    field.press("Enter")
    expect(page.locator("#toast")).to_contain_text(
        "A rule names one address, such as name@example.com, or one domain, such as example.com.")
    expect(field).to_have_value("50506 spam@lottery.example.test")
    expect(page.locator('#keepList [data-token="50506 spam@lottery.example.test"]')).to_have_count(0)
    assert "50506 spam@lottery.example.test" not in blocked(app, "keep_senders")
    assert all("400" in p for p in page.problems), page.problems    # the refusal is on the console
    page.problems.clear()
    field.fill("spam@lottery.example.test")
    field.press("Shift+Enter")
    expect(page.locator('#blockList [data-token="spam@lottery.example.test"]')).to_be_visible()
    expect(field).to_have_value("")
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


class Held:
    """The page's requests to `path` wait here until let go, as they would on a slow server."""

    def __init__(self, page, path):
        self.routes = []
        page.route(f"**{path}", self._arrived)

    def _arrived(self, route):
        self.routes.append(route)

    def let_go(self):
        routes, self.routes = self.routes, []
        for r in routes:
            r.fallback()


def test_while_a_plan_or_a_sweep_runs_its_row_says_so_and_neither_can_be_pressed_again(page, world):
    d = drawer(page)
    mod = d.locator(f'.acctmod[data-acct="{w.ADA}"]')
    sweep = mod.get_by_role("button", name=re.compile(r"^Sweep \d+"))
    busy = mod.get_by_role("status")
    expect(busy).to_have_text("")
    held = Held(page, "/api/sweep")
    sweep.click()
    expect(busy).to_have_text("Looking at what a sweep would move…")
    expect(sweep).to_be_disabled()
    until(page, lambda: len(held.routes) == 1, "the plan was asked for")
    held.let_go()
    confirm = mod.get_by_role("button", name="Confirm")
    expect(confirm).to_be_enabled()
    expect(busy).to_have_text("")
    expect(sweep).to_be_enabled()
    before = world.mark()
    confirm.click()
    expect(busy).to_have_text("Sweeping 1 to Trash…")
    expect(sweep).to_be_disabled()
    sweep.click(force=True)                                    # pressed again: nothing more is asked
    until(page, lambda: len(held.routes) == 1, "the sweep was asked for")
    page.wait_for_timeout(200)
    assert len(held.routes) == 1
    held.let_go()
    expect(page.locator("#toast")).to_contain_text("Swept 1/1")
    expect(busy).to_have_text("")
    until(page, lambda: world.writes(before), "the sweep moved mail")
    clean(page)


def test_a_sweep_says_what_goes_and_what_stays_before_it_moves_and_one_undo_brings_it_back(page, app, world):
    world.server_flags(NEWS, "\\Seen")                         # read in another client
    world.server_flags(SCAM, "\\Seen", "\\Flagged")            # read, and starred: it stays
    app.work("sync", w.ADA, "INBOX")                            # and MailMind's sync hears of it
    d = drawer(page)
    mod = d.locator(f'.acctmod[data-acct="{w.ADA}"]')
    mod.get_by_role("button", name=re.compile(r"^Sweep \d+")).click()
    going = mod.locator(".sweep-going")
    expect(going.locator(".sg-hd")).to_have_text([re.compile("^Newsletters and marketing"),
                                                  re.compile("^Automatic notifications")])
    for said in (LOOM, "This week: five new patterns", "noreply@ci.example.test", "[quilt] Build #412 passed"):
        expect(going).to_contain_text(said)
    expect(going).not_to_contain_text("prizes.example.test")
    expect(going.locator(".sg-note")).to_have_text("1 starred or unread message stays in your Inbox.")
    expect(going.locator(".sg-undo")).to_have_text("Undo with z puts it all back.")   # each has a Message-ID to find it by
    mod.get_by_role("button", name="Confirm").click()
    expect(page.locator("#toast")).to_contain_text("Swept 2/2")
    until(page, lambda: world.where(NEWS)["server"][0][1] == "Trash", "it went to the Trash")
    assert world.where(SCAM)["server"][0][1] == "INBOX"
    page.locator("#ufUndo").click()
    for name in (NEWS, "ada-inbox-04-ci.eml"):
        until(page, lambda name=name: world.where(name)["server"][0][1] == "INBOX", f"{name} came back")
    clean(page)


def test_the_sweep_button_counts_what_a_sweep_would_move_and_is_gone_when_nothing_would(page, app, world):
    """Of Ada's three junk only the CI mail is read, so a sweep would move one; once she stars it, none, and the card
    still says how much junk there is."""
    mod = drawer(page).locator(f'.acctmod[data-acct="{w.ADA}"]')
    expect(mod.get_by_role("button", name=re.compile(r"^Sweep \d+"))).to_have_text("Sweep 1")
    app.post("/api/flag", {**world.copy("ada-inbox-04-ci.eml"), "flag": "flagged", "on": True})
    mod = drawer(page).locator(f'.acctmod[data-acct="{w.ADA}"]')
    expect(mod).to_contain_text("3 junk")
    expect(mod.get_by_role("button", name=re.compile(r"^Sweep"))).to_have_count(0)
    clean(page)


def test_a_sweep_the_server_stops_before_anything_moved_offers_no_undo(page, world):
    world.server_flags(NEWS, "\\Seen")
    world.server.drop("UID MOVE", user=w.ADA)
    mod = drawer(page).locator(f'.acctmod[data-acct="{w.ADA}"]')
    mod.get_by_role("button", name=re.compile(r"^Sweep \d+")).click()
    mod.get_by_role("button", name="Confirm").click()
    expect(page.locator("#toast")).to_contain_text("Couldn’t reach the mail server, so nothing was swept. Try again in a moment. (")
    expect(page.locator("#ufUndo")).to_be_hidden()
    assert world.where(NEWS)["server"][0][1] == "INBOX"
    clean(page)


def test_a_sweep_stopped_where_the_server_cant_be_asked_again_says_so_and_offers_undo(page, world):
    world.server_flags(NEWS, "\\Seen")
    dropped = []

    def drops(args):                                         # the move goes with its connection, unanswered
        dropped.append(True)
        return True
    world.server.drop("UID MOVE", user=w.ADA, when=drops)
    # ...and the login that would look at the Inbox again is refused: how much went can't be told.
    world.server.fail("LOGIN", "NO [UNAVAILABLE] scripted failure", user=w.ADA, when=lambda args: bool(dropped))
    mod = drawer(page).locator(f'.acctmod[data-acct="{w.ADA}"]')
    mod.get_by_role("button", name=re.compile(r"^Sweep \d+")).click()
    mod.get_by_role("button", name="Confirm").click()
    expect(page.locator("#toast")).to_contain_text(
        "Couldn’t reach the mail server, so the sweep stopped part way and MailMind couldn’t check how much went. Undo "
        "puts back whatever did; sweep again for the rest. (")
    expect(page.locator("#toast")).not_to_contain_text("nothing was swept")
    page.locator("#ufUndo").click()                             # what never left is back already
    expect(page.locator("#toast")).to_contain_text("undone")
    assert world.where(NEWS)["server"][0][1] == "INBOX"
    clean(page)


def test_a_senders_chip_says_how_it_lets_you_unsubscribe_before_any_preview(page, app, world):
    issues(world)
    world.deliver(w.ADA, "INBOX", w.variant(NEWS, Message_ID="<issue-310@loom.example.test>", Subject="Issue 310",
                                            Date="Mon, 02 Nov 2026 07:00:00 +0000", List_Unsubscribe_Post=""))
    row = drawer(page).locator(f'.flood-row[data-flood="{LOOM}"]')
    expect(row.locator(".flood-chip.lu1")).to_have_text("by email")       # a mailto: way out is never called one click
    clean(page)


def test_the_flood_list_cleans_up_a_sender_after_two_clicks_and_undo_reverses_it(page, app, world):
    issues(world)
    d = drawer(page)
    row = d.locator(f'.flood-row[data-flood="{LOOM}"]')
    expect(row.locator(".flood-chip").first).to_contain_text("3 msgs")
    row.get_by_role("button", name=f"Clean up {LOOM}").click()
    plan = page.get_by_role("dialog", name="Clean up a sender")
    expect(plan.locator(".do-hd")).to_contain_text(LOOM)
    expect(plan.locator(".do-line").first).to_contain_text(LOOM)
    plan.locator(".do-unsubopt input").uncheck()
    go = plan.locator(".btn-retry.strong")
    expect(go).to_have_text("Clean up")
    before = world.mark()
    go.click()
    expect(go).to_have_text("Click again to confirm")
    assert world.writes(before) == []
    assert LOOM not in blocked(app)
    go.click()
    expect(plan).to_have_count(0)
    expect(page.locator("#toast")).to_contain_text("Blocked · swept 3")
    assert LOOM in blocked(app)
    until(page, lambda: world.where(NEWS)["server"][0][1] == "Trash", "it went to the Trash")
    expect(page.locator(f'.flood-row[data-flood="{LOOM}"]')).to_have_count(0)
    page.locator("#ufUndo").click()         # over the drawer, which stays open for the next sender
    expect(d).to_be_visible()
    until(page, lambda: LOOM not in blocked(app), "the block is gone")
    until(page, lambda: world.where(NEWS)["server"][0][1] == "INBOX", "it came back")
    clean(page)


def test_the_whole_domain_can_be_chosen_and_changes_what_the_plan_says(page, world):
    issues(world)
    d = drawer(page)
    d.get_by_role("button", name=f"Clean up {LOOM}").click()
    plan = page.get_by_role("dialog", name="Clean up a sender")
    go = plan.locator(".btn-retry.strong")
    go.click()
    expect(go).to_have_text("Click again to confirm")
    plan.get_by_role("button", name="whole domain").click()
    expect(plan.locator(".do-line").first).to_contain_text("loom.example.test")
    expect(go).to_have_text("Clean up")                                 # a changed plan asks again
    plan.get_by_role("button", name="Cancel").click()
    expect(plan).to_have_count(0)
    expect(d).to_be_visible()
    clean(page)


def test_only_an_unsubscribe_that_goes_is_marked_as_what_undo_cant_take_back(page, world):
    issues(world)
    d = drawer(page)
    d.get_by_role("button", name=f"Clean up {LOOM}").click()
    plan = page.get_by_role("dialog", name="Clean up a sender")
    expect(plan.locator(".do-line.irrev")).to_have_count(1)
    expect(plan.locator(".do-cant-undo")).to_have_text("Unsubscribing can’t be undone.")
    plan.locator(".do-unsubopt input").uncheck()
    expect(plan.locator(".do-line.irrev")).to_have_count(0)                  # nothing goes that Undo can't take back
    expect(plan.locator(".do-line").last).to_contain_text("Not unsubscribing — block + sweep only")
    expect(plan.locator(".do-cant-undo")).to_have_count(0)
    expect(plan.locator(".do-undo-note")).to_have_text("Block + sweep undo with z.")
    plan.get_by_role("button", name="Cancel").click()
    expect(plan).to_have_count(0)
    clean(page)


def test_the_audit_trail_shows_what_was_done(page, world):
    issues(world)
    d = drawer(page)
    d.get_by_role("button", name=f"Clean up {LOOM}").click()
    plan = page.get_by_role("dialog", name="Clean up a sender")
    plan.locator(".do-unsubopt input").uncheck()
    go = plan.locator(".btn-retry.strong")
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
