"""What a phone's gestures do: Back closes what's on top, a swipe files a row. And the rows after the
one you're reading are made ready."""
import re

import pytest
from playwright.sync_api import expect

from fixtures import world as w

pytestmark = pytest.mark.browser

RECEIPT = "ada-inbox-05-receipt.eml"
NEWS = "ada-inbox-03-newsletter.eml"
LOOM = "news@loom.example.test"


def clean(page):
    assert page.problems == [], page.problems
    assert page.blocked == [], page.blocked


def until(page, check, what):
    for _ in range(100):
        if check():
            return
        page.wait_for_timeout(50)
    assert check(), what


def snoozed_until(row):
    from mailmind.bootstrap.mailbox import store
    c = store.conn()
    try:
        return store.get_message(c, row["pk"])["snooze_until"]
    finally:
        c.close()


def inbox(page):
    page.goto(f"/m/{w.ADA}/INBOX")
    expect(page.locator("#monos")).not_to_be_empty()
    return page


def two_pages(page):
    """The Inbox with Sent behind it in the tab's history, so a wrong step shows in the URL."""
    page.goto(f"/m/{w.ADA}/Sent")
    expect(page.locator("#monos")).not_to_be_empty()
    page.locator('a.frow[data-folder="INBOX"]').click()
    page.expect_where("/INBOX")
    return page


SENT, INBOX = re.compile("/Sent"), re.compile("/INBOX")


def test_back_closes_a_dialog_and_stays_on_the_page(page, world):
    two_pages(page)
    page.keyboard.press("?")
    help_ = page.get_by_role("dialog").first
    expect(help_).to_be_visible()
    page.pw.go_back()
    expect(help_).to_have_count(0)
    page.expect_where(INBOX)
    page.pw.go_back()
    page.expect_where(SENT)
    clean(page)


def test_a_dialog_closed_another_way_gives_its_entry_back(page, world):
    two_pages(page)
    page.keyboard.press("?")
    help_ = page.get_by_role("dialog").first
    expect(help_).to_be_visible()
    page.keyboard.press("Escape")
    expect(help_).to_have_count(0)
    until(page, lambda: page.pw.evaluate("history.state === null || !history.state.mmTrap"), "the entry was spent")
    page.pw.go_back()
    page.expect_where(SENT)
    clean(page)


def test_back_closes_stacked_layers_one_at_a_time(page, world):
    for i in range(2):
        world.deliver(w.ADA, "INBOX", w.variant(NEWS, Message_ID=f"<issue-{300 + i}@loom.example.test>", Subject=f"Issue {300 + i}", Date=f"Fri, {i + 10:02} Oct 2026 07:00:00 +0000"))
    two_pages(page)
    page.locator("#statusBtn").click()
    page.get_by_role("button", name=re.compile("^Curate")).click()
    drawer = page.get_by_role("dialog", name="Curate")
    drawer.get_by_role("button", name=f"Clean up {LOOM}").click()
    plan = page.get_by_role("dialog", name="Clean up a sender")
    expect(plan).to_be_visible()
    page.pw.go_back()
    expect(plan).to_have_count(0)
    expect(drawer).to_be_visible()
    page.expect_where(INBOX)
    page.pw.go_back()
    expect(drawer).to_have_count(0)
    page.expect_where(INBOX)
    page.pw.go_back()
    page.expect_where(SENT)
    clean(page)


def test_going_somewhere_from_a_dialog_leaves_one_back_to_the_page_before(page, world):
    two_pages(page)
    page.keyboard.press("ControlOrMeta+k")
    page.get_by_role("combobox", name="Command").fill("All inboxes")
    page.keyboard.press("Enter")
    page.expect_where("/all")
    page.pw.go_back()
    page.expect_where(INBOX)
    page.pw.go_back()
    page.expect_where(SENT)
    clean(page)


def test_back_closes_the_composer_and_keeps_what_was_typed(page, world):
    two_pages(page)
    page.keyboard.press("c")
    subject = page.get_by_label("Subject")
    subject.fill("Kept by Back")
    page.pw.go_back()
    expect(subject).to_have_count(0)
    page.expect_where(INBOX)
    until(page, lambda: any("Kept by Back" in str(r) for r in drafts(world)), "the draft was saved")
    clean(page)


def drafts(world):
    from mailmind.bootstrap.mailbox import store
    c = store.conn()
    try:
        return [dict(r) for r in c.execute("SELECT subject FROM drafts")]
    finally:
        c.close()


def test_back_on_a_message_returns_to_the_list(page, world):
    inbox(page)
    page.get_by_role("tab", name=re.compile("^Everything")).click()
    page.get_by_role("option").first.click()
    page.expect_where(r"key=\d+&folder=")
    page.pw.go_back()
    page.expect_where(r"key=\d+", present=False)
    clean(page)


SWIPE = """([sel, dx]) => {
    const row = document.querySelector(sel), r = row.getBoundingClientRect();
    const x0 = r.left + r.width / 2, y = r.top + r.height / 2;
    const touch = (x) => new Touch({ identifier: 1, target: row, clientX: x, clientY: y });
    const fire = (type, x, list) => row.dispatchEvent(new TouchEvent(type, { bubbles: true, cancelable: true, touches: list ? [touch(x)] : [], changedTouches: [touch(x)] }));
    fire("touchstart", x0, true);
    for (let i = 1; i <= 4; i++) fire("touchmove", x0 + dx * i / 4, true);
    fire("touchend", x0 + dx, false);
}"""


def row_sel(world, name):
    return '#rows .row.cmsg[data-key="{key}"][data-folder="{folder}"]'.format(**world.copy(name))


def swipeable(page):
    inbox(page)
    page.get_by_role("tab", name=re.compile("^Everything")).click()
    expect(page.get_by_role("option").first).to_be_visible()
    return page


def test_swiping_left_archives_and_undo_brings_it_back(page, world):
    swipeable(page)
    sel = row_sel(world, RECEIPT)
    page.pw.evaluate(SWIPE, [sel, -140])
    expect(page.locator(sel)).to_have_count(0)
    until(page, lambda: world.where(RECEIPT)["server"][0][1] != "INBOX", "it left the Inbox")
    expect(page.locator("#ufUndo")).to_be_visible()
    page.locator("#ufUndo").click()
    until(page, lambda: world.where(RECEIPT)["server"][0][1] == "INBOX", "it came back")
    clean(page)


def test_swiping_right_snoozes_to_tomorrow(page, world):
    swipeable(page)
    sel = row_sel(world, RECEIPT)
    pk = world.pk(RECEIPT)
    page.pw.evaluate(SWIPE, [sel, 140])
    expect(page.locator(sel)).to_have_count(0)
    until(page, lambda: snoozed_until(world.row(RECEIPT)), "it is snoozed")
    assert world.pk(RECEIPT) == pk
    clean(page)


def test_a_short_swipe_does_nothing(page, world):
    swipeable(page)
    sel = row_sel(world, RECEIPT)
    before = world.mark()
    posted = []
    page.on("request", lambda r: posted.append(r.url) if r.method == "POST" else None)
    page.pw.evaluate(SWIPE, [sel, -40])
    expect(page.locator(sel)).to_have_count(1)
    page.heard_all_asked()                              # a swipe that files asks the server as it ends
    assert posted == []
    assert world.writes(before) == []
    assert page.locator(sel).evaluate("r => r.style.transform") == ""
    clean(page)


def test_a_vertical_drag_is_the_lists(page, world):
    swipeable(page)
    sel = row_sel(world, RECEIPT)
    moved = page.pw.evaluate("""(sel) => {
        const row = document.querySelector(sel), r = row.getBoundingClientRect();
        const t = (y) => new Touch({ identifier: 1, target: row, clientX: r.left + 40, clientY: y });
        row.dispatchEvent(new TouchEvent("touchstart", { bubbles: true, touches: [t(r.top)], changedTouches: [t(r.top)] }));
        const e = new TouchEvent("touchmove", { bubbles: true, cancelable: true, touches: [t(r.top + 60)], changedTouches: [t(r.top + 60)] });
        row.dispatchEvent(e);
        row.dispatchEvent(new TouchEvent("touchend", { bubbles: true, touches: [], changedTouches: [t(r.top + 60)] }));
        return { prevented: e.defaultPrevented, transform: row.style.transform };
    }""", sel)
    assert moved == {"prevented": False, "transform": ""}
    clean(page)


def test_opening_a_message_makes_the_next_two_ready_once(page, world):
    asked = []
    page.pw.on("request", lambda r: asked.append(r.url) if "prefetch=1" in r.url else None)
    swipeable(page)
    keys = [int(x) for x in page.locator("#rows .row.cmsg").evaluate_all("rows => rows.map(r => r.dataset.key)")]
    page.locator(f'#rows .row.cmsg[data-key="{keys[0]}"]').click()
    until(page, lambda: len(asked) == 2, "two were made ready")
    assert [re.search(r"[?&]key=(\d+)&", u).group(1) for u in asked] == [str(keys[1]), str(keys[2])]
    page.keyboard.press("j")
    until(page, lambda: len(asked) == 3, "one more")
    assert f"?key={keys[3]}&" in asked[2]
    clean(page)
