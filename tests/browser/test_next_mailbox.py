"""The React app at /next, on the same world the current page's smoke tests use.

It is built once for the session into a folder of its own, so a build on this machine doesn't matter, and every test
here serves that one. Both front doors serve it, so each test runs twice, as app tests do.
"""
import os
import re
import shutil
import subprocess

import pytest
from playwright.sync_api import expect

from fixtures import world as w
from mailmind.platform import sse, webroot

pytestmark = pytest.mark.browser

RECEIPT = "ada-inbox-05-receipt.eml"
LUNCH = ("ada-inbox-01-lunch.eml", "ada-inbox-02-lunch-confirm.eml")


@pytest.fixture(scope="session")
def built(tmp_path_factory):
    out = tmp_path_factory.mktemp("next-dist")
    web = os.path.join(webroot.REPO, "web")
    if not os.path.isdir(os.path.join(web, "node_modules")):
        pytest.skip("web/node_modules isn't installed")
    subprocess.run([shutil.which("npx"), "vite", "build", "--outDir", str(out), "--emptyOutDir"], cwd=web, check=True,
                   capture_output=True)
    return str(out)


@pytest.fixture(autouse=True)
def serve_the_build(built, monkeypatch):
    monkeypatch.setattr(webroot, "DIST", built)


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
    page.goto("/next")
    page.get_by_role("tab", name=re.compile("^Everything")).click()
    expect(page.get_by_role("option").first).to_be_visible()
    return page


def row(page, world, name):
    pk = world.pk(name)
    assert pk is not None, f"{name} has no row in the database"
    return page.locator(f'#rows .row[data-pk="{pk}"]')


def mailbox(world, name):
    return world.where(name)["server"][0][1]


def test_it_opens_the_first_accounts_inbox_with_its_rail_and_list(page, world):
    opened(page)
    assert page.url.endswith(f"/next/m/{w.ADA.replace('@', '%40')}/INBOX")
    expect(page.locator("#listTitle")).to_have_text("Inbox")
    expect(page.locator('#folderrail .frow[data-folder="INBOX"]')).to_have_attribute("aria-current", "page")
    expect(row(page, world, RECEIPT)).to_be_visible()
    clean(page)


def test_a_folder_is_a_link_and_back_comes_back(page, world):
    opened(page)
    page.locator('#folderrail .frow[data-folder="Sent"]').click()
    expect(page.locator("#listTitle")).to_have_text("Sent")
    assert "/Sent" in page.url
    page.go_back()
    expect(page.locator("#listTitle")).to_have_text("Inbox")
    clean(page)


def test_a_deep_link_opens_the_message(page, world):
    pk = world.pk(RECEIPT)
    page.goto(f"/next/m/{w.ADA.replace('@', '%40')}/INBOX?m={pk}")
    expect(page.locator(".rd-subj")).to_be_visible()
    expect(page.locator("iframe.rd-iframe")).to_have_count(1)
    clean(page)


def test_a_mail_frame_is_sandboxed_with_no_scripts_and_is_sized_to_what_it_holds(page, world):
    opened(page)
    row(page, world, RECEIPT).click()
    frame = page.locator("iframe.rd-iframe")
    expect(frame).to_have_attribute("sandbox", "allow-same-origin")
    until(page, lambda: frame.evaluate("f => parseInt(f.style.height) > 0"), "the frame was never sized")
    assert "default-src 'none'" in frame.get_attribute("srcdoc")
    clean(page)


def test_a_script_given_to_a_frame_is_taken_away(page, world):
    opened(page)
    row(page, world, RECEIPT).click()
    frame = page.locator("iframe.rd-iframe")
    expect(frame).to_be_visible()
    seen = []
    page.pw.on("console", lambda m: seen.append(m.text))
    frame.evaluate("f => f.setAttribute('sandbox', 'allow-same-origin allow-scripts')")
    until(page, lambda: frame.get_attribute("sandbox") == "allow-same-origin", "allow-scripts stayed on the frame")
    assert any("SECURITY" in s for s in seen)
    page.problems.clear()     # that console error is what was asked for


def test_opening_marks_read_after_a_moment_on_the_server_too(page, world):
    opened(page)
    r = row(page, world, RECEIPT)
    assert "unread" in r.get_attribute("class")
    r.click()
    expect(r).not_to_have_class(re.compile(r"\bunread\b"), timeout=4000)
    until(page, lambda: "\\Seen" in world.where(RECEIPT)["server"][0][3], "the server never heard")
    clean(page)


def test_archive_files_it_opens_the_next_and_undo_brings_it_back(page, world):
    opened(page)
    r = row(page, world, RECEIPT)
    subject = world.row(RECEIPT)["subject"]
    r.click()
    expect(page.locator(".rd-subj")).to_be_visible()
    first = page.url
    page.keyboard.press("e")
    expect(r).to_have_count(0)
    until(page, lambda: page.url != first, "no next message opened")
    assert page.locator('#rows .row.sel').count() == 1
    until(page, lambda: mailbox(world, RECEIPT) == "Archive", "the server never filed it")
    expect(page.locator("#undoFloat")).to_be_visible()
    page.keyboard.press("z")
    until(page, lambda: mailbox(world, RECEIPT) == "INBOX", "Undo never reached the server")
    expect(page.locator("#rows .row[data-pk]").filter(has_text=subject)).to_have_count(1)
    expect(page.locator("#toast")).to_have_text(re.compile("undone"))
    clean(page)


def test_a_refused_archive_puts_the_row_back_and_says_so(page, world):
    opened(page)
    r = row(page, world, RECEIPT)
    world.server.fail("UID MOVE", "NO refused")
    world.server.fail("UID COPY", "NO refused")
    r.click()
    page.keyboard.press("e")
    expect(page.locator("#toast")).to_have_text("Couldn’t archive")
    expect(r).to_be_visible()
    assert mailbox(world, RECEIPT) == "INBOX"
    page.problems[:] = [p for p in page.problems if "409" not in p and "500" not in p and "502" not in p]
    clean(page)


def test_star_and_unread_reach_the_server(page, world):
    opened(page)
    r = row(page, world, RECEIPT)
    r.click()
    expect(page.locator(".rd-subj")).to_be_visible()
    page.keyboard.press("s")
    until(page, lambda: "\\Flagged" in world.where(RECEIPT)["server"][0][3], "the star never reached the server")
    page.keyboard.press("u")
    expect(r).to_have_class(re.compile(r"\bunread\b"))
    page.wait_for_timeout(1500)     # the dwell doesn't take it back
    expect(r).to_have_class(re.compile(r"\bunread\b"))
    assert "\\Seen" not in world.where(RECEIPT)["server"][0][3]
    clean(page)


def test_j_and_k_walk_the_list_and_the_listbox_says_where_you_are(page, world):
    opened(page)
    box = page.get_by_role("listbox")
    options = page.get_by_role("option")
    page.keyboard.press("j")
    expect(options.nth(0)).to_have_attribute("aria-selected", "true")
    page.keyboard.press("j")
    expect(options.nth(1)).to_have_attribute("aria-selected", "true")
    expect(box).to_have_attribute("aria-activedescendant", options.nth(1).get_attribute("id"))
    page.keyboard.press("k")
    expect(options.nth(0)).to_have_attribute("aria-selected", "true")
    page.keyboard.press("Escape")
    expect(page.locator("#reader")).to_have_class(re.compile("empty-pane"))
    clean(page)


def test_a_conversation_opens_whole_with_its_latest_open_and_files_whole(page, world):
    opened(page)
    r = page.locator("#rows .row[data-pk]").filter(has=page.locator(".thr-count")).filter(has_text="unch").first
    r.click()
    expect(page.locator("#thread .tmsg")).to_have_count(3)
    expect(page.locator("#thread .tmsg").last).to_have_class(re.compile(r"\bopen\b"))
    expect(page.locator("#thread .tmsg").last.locator("iframe.rd-iframe")).to_be_visible()
    page.locator("#thread .tmsg-hd").first.click()
    expect(page.locator("#thread .tmsg").first.locator("iframe.rd-iframe")).to_be_visible()
    page.get_by_role("button", name="Archive conversation").last.click()
    until(page, lambda: all(mailbox(world, n) == "Archive" for n in LUNCH), "the conversation wasn't filed whole")
    expect(page.locator("#toast")).to_have_text("Archived 2 messages")
    clean(page)


def test_a_search_shows_what_it_finds_and_escape_leaves_it(page, world):
    opened(page)
    page.keyboard.press("/")
    box = page.get_by_role("textbox", name="Search")
    expect(box).to_be_focused()
    box.fill("from:lin")
    box.press("Enter")
    expect(page.locator("#listTitle")).to_have_text("Search")
    assert "q=from%3Alin" in page.url
    box.press("Escape")
    expect(page.locator("#listTitle")).to_have_text("Inbox")
    clean(page)


def test_new_mail_comes_into_the_list_by_itself(page, world):
    opened(page)
    before = page.locator("#rows .row[data-pk]").count()
    world.deliver(w.ADA, "INBOX", w.variant(RECEIPT, Message_ID="<fresh@example.test>", Subject="Fresh off the press"))
    sse.publish("new-mail", {"account": w.ADA, "folder": "INBOX", "count": 1})
    until(page, lambda: page.locator("#rows .row[data-pk]").count() > before, "the new mail never showed")
    expect(page.locator("#rows .row[data-pk]").filter(has_text="Fresh off the press")).to_have_count(1)
    clean(page)


def test_a_rows_own_buttons_file_it_with_the_mouse(page, world):
    opened(page)
    r = row(page, world, RECEIPT)
    r.hover()
    r.locator(".ract.del").click()
    until(page, lambda: mailbox(world, RECEIPT) == "Trash", "the server never trashed it")
    expect(r).to_have_count(0)
    expect(page.locator("#reader")).to_have_class(re.compile("empty-pane"))
    clean(page)


def test_the_account_menu_changes_account_and_offers_all_inboxes(page, world):
    opened(page)
    page.locator(".acct-sw").click()
    expect(page.get_by_role("menu")).to_be_visible()
    page.get_by_role("menuitemradio", name=re.compile("^All inboxes")).click()
    expect(page.locator("#listTitle")).to_have_text("All inboxes")
    assert page.url.endswith("/next/all")
    expect(page.locator("#rows .acctchip").first).to_be_visible()
    clean(page)


def test_a_tab_on_an_older_build_offers_a_reload_and_a_current_one_doesnt(page, world):
    opened(page)
    float_ = page.locator("#buildFloat")
    assert float_.is_hidden()
    page.pw.route("**/next**", lambda route: route.fulfill(
        response=(r := route.fetch()), body=re.sub(r'(name="mailmind-build" content=")[^"]+', r"\1older", r.text())))
    page.pw.reload()
    float_.wait_for(state="visible")
    assert "MailMind was updated" in float_.inner_text()
    page.pw.unroute("**/next**")
    page.locator("#buildReload").click()
    float_.wait_for(state="hidden")
    clean(page)


def test_a_narrow_window_shows_one_pane_with_a_way_back_and_a_folder_drawer(page, world):
    page.set_viewport_size({"width": 420, "height": 800})
    page.goto("/next")
    page.get_by_role("tab", name=re.compile("^Everything")).click()
    r = row(page, world, RECEIPT)
    r.click()
    expect(page.locator(".rd-subj")).to_be_visible()
    expect(r).to_be_hidden()
    page.locator(".rd-back").click()
    expect(r).to_be_visible()
    rail = page.locator('#folderrail .frow[data-folder="Sent"]')
    expect(rail).to_be_hidden()
    page.locator("#railToggle").click()
    expect(rail).to_be_visible()
    rail.click()
    expect(page.locator("#listTitle")).to_have_text("Sent")
    expect(rail).to_be_hidden()
    clean(page)


def test_a_narrow_window_folds_search_away_until_asked(page, world):
    page.set_viewport_size({"width": 420, "height": 800})
    page.goto("/next")
    box = page.get_by_role("textbox", name="Search")
    expect(box).to_be_hidden()
    page.locator(".search-tog").click()
    expect(box).to_be_focused()
    page.locator(".search-x").click()
    expect(box).to_be_hidden()
    clean(page)


def test_axe_finds_nothing_on_the_list_or_the_reader(page, world):
    from axe_playwright_python.sync_playwright import Axe
    opened(page)
    row(page, world, RECEIPT).click()
    expect(page.locator("iframe.rd-iframe")).to_be_attached()
    found = Axe().run(page.pw, context={"exclude": [["iframe.rd-iframe"]]}).response["violations"]
    assert {v["id"]: [n["target"] for n in v["nodes"]] for v in found} == {}


def test_it_meets_the_page_policy(page, world):
    opened(page)
    row(page, world, RECEIPT).click()
    expect(page.locator("iframe.rd-iframe")).to_be_visible()
    page.locator(".acct-sw").click()
    page.keyboard.press("Escape")
    clean(page)
