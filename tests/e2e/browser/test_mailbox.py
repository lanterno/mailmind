"""The mailbox, on the fictional world: the rail, the list, the reader and filing."""
import re

import pytest
from playwright.sync_api import expect

from fakes import anthropic_api as fa
from fixtures import world as w
from mailmind.platform import sse
from mailmind.bootstrap.assistant import answers

pytestmark = pytest.mark.browser

RECEIPT = "ada-inbox-05-receipt.eml"
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


def row(page, world, name):
    assert world.pk(name) is not None, f"{name} has no row in the database"
    return page.locator('#rows .row[data-key="{key}"][data-folder="{folder}"]'.format(**world.copy(name)))


def mailbox(world, name):
    return world.where(name)["server"][0][1]


def test_a_link_from_when_the_app_lived_at_next_opens_the_same_place(page, world):
    key, uid = world.key(RECEIPT), world.uid(RECEIPT)
    page.goto(f"/next/m/{w.ADA.replace('@', '%40')}/INBOX?key={key}&folder=INBOX&uid={uid}")
    expect(page.locator("#listTitle")).to_have_text("Inbox")
    expect(page.locator("iframe.rd-iframe")).to_have_count(1)
    assert page.url.endswith(f"/m/{w.ADA.replace('@', '%40')}/INBOX?key={key}&folder=INBOX&uid={uid}") and "/next" not in page.url
    clean(page)


def test_it_opens_the_first_accounts_inbox_with_its_rail_and_list(page, world):
    opened(page)
    assert page.url.endswith(f"/m/{w.ADA.replace('@', '%40')}/INBOX")
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
    page.goto(f"/m/{w.ADA.replace('@', '%40')}/INBOX?key={world.key(RECEIPT)}&folder=INBOX&uid={world.uid(RECEIPT)}")
    expect(page.locator(".rd-subj")).to_have_text("Your receipt for order 1042")
    expect(page.locator("iframe.rd-iframe")).to_have_count(1)
    clean(page)


def inbox_pks():
    from mailmind.bootstrap.mailbox import store
    c = store.conn()
    try:
        return {r["pk"] for r in c.execute("SELECT pk FROM messages WHERE account=? AND folder='INBOX'", (w.ADA,))}
    finally:
        c.close()


def test_a_kept_link_still_opens_its_message_after_a_renumber(page, world):
    from mailmind.bootstrap import accounts
    from mailmind.bootstrap.mailbox import ingest
    link = f"/m/{w.ADA.replace('@', '%40')}/INBOX?key={world.key(RECEIPT)}&folder=INBOX&uid={world.uid(RECEIPT)}"
    before = inbox_pks()
    world.server.renumber(w.ADA, "INBOX")       # UIDVALIDITY changes: every row the Inbox had goes, and comes back anew, at the uids it had
    ingest.sync_new(accounts.get(w.ADA))
    assert inbox_pks() and before.isdisjoint(inbox_pks())
    page.goto(link)
    expect(page.locator(".rd-subj")).to_have_text("Your receipt for order 1042")
    expect(page.locator("iframe.rd-iframe")).to_have_count(1)
    clean(page)


def test_a_kept_link_opens_nothing_once_a_renumber_gave_its_copy_another_uid(page, world):
    from mailmind.bootstrap import accounts
    from mailmind.bootstrap.mailbox import ingest
    link = f"/m/{w.ADA.replace('@', '%40')}/INBOX?key={world.key(RECEIPT)}&folder=INBOX&uid={world.uid(RECEIPT)}"
    world.server.renumber(w.ADA, "INBOX", first=500)    # every copy in the Inbox now has a uid no link has seen
    ingest.sync_new(accounts.get(w.ADA))
    page.goto(link)
    expect(page.locator("#listTitle")).to_have_text("Inbox")
    expect(page.locator("#reader .nores")).to_have_text("not found")
    expect(page.locator(".rd-subj")).to_have_count(0)
    expect(page.locator("iframe.rd-iframe")).to_have_count(0)
    page.problems[:] = [p for p in page.problems if "404" not in p]
    clean(page)


def test_a_link_without_a_uid_opens_nothing(page, world):
    page.goto(f"/m/{w.ADA.replace('@', '%40')}/INBOX?key={world.key(RECEIPT)}&folder=INBOX")
    expect(page.locator("#listTitle")).to_have_text("Inbox")
    page.wait_for_timeout(300)
    expect(page.locator(".rd-subj")).to_have_count(0)
    expect(page.locator("iframe.rd-iframe")).to_have_count(0)
    clean(page)


def test_a_link_from_before_keys_opens_nothing(page, world):
    page.goto(f"/m/{w.ADA.replace('@', '%40')}/INBOX?m={world.pk(RECEIPT)}")
    expect(page.locator("#listTitle")).to_have_text("Inbox")
    page.wait_for_timeout(300)
    expect(page.locator(".rd-subj")).to_have_count(0)
    expect(page.locator("iframe.rd-iframe")).to_have_count(0)
    clean(page)


def test_a_mail_frame_is_sandboxed_with_no_scripts_and_is_sized_to_what_it_holds(page, world):
    opened(page)
    row(page, world, RECEIPT).click()
    frame = page.locator("iframe.rd-iframe")
    expect(frame).to_have_attribute("sandbox", "allow-same-origin")
    until(page, lambda: frame.evaluate("f => parseInt(f.style.height) > 0"), "the frame was never sized")
    assert "/body.html?" in frame.get_attribute("src")
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
    expect(page.locator("#rows .row[data-key]").filter(has_text=subject)).to_have_count(1)
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
    expect(page.locator("#reader")).to_have_class(re.compile("brief-pane"))
    clean(page)


def test_a_conversation_opens_whole_with_its_latest_open_and_files_whole(page, world):
    opened(page)
    r = page.locator("#rows .row[data-key]").filter(has=page.locator(".thr-count")).filter(has_text="unch").first
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


def test_e_on_a_conversations_row_files_every_message_its_sent_copy_stays_and_z_brings_them_back(page, world):
    opened(page)
    convo = page.locator("#rows .row[data-key]").filter(has_text="Lunch on Thursday?")
    expect(convo).to_have_count(1)                        # one row for the two inbox messages
    expect(convo.locator(".thr-count")).to_have_text("2")
    convo.click()
    page.keyboard.press("e")
    expect(convo).to_have_count(0)
    until(page, lambda: all(mailbox(world, n) == "Archive" for n in LUNCH), "the conversation wasn't filed whole")
    assert mailbox(world, "ada-sent-01-lunch-reply.eml") == "Sent"   # only the row's folder files
    expect(page.locator("#toast")).to_have_text("Archived 2 messages")
    page.keyboard.press("z")
    expect(convo).to_have_count(1)
    until(page, lambda: all(mailbox(world, n) == "INBOX" for n in LUNCH), "one Undo didn't bring both back")
    clean(page)


def test_an_undo_that_restores_only_part_says_so_and_z_tries_the_rest(page, world):
    opened(page)
    convo = page.locator("#rows .row[data-key]").filter(has_text="Lunch on Thursday?")
    convo.click()
    page.keyboard.press("e")
    until(page, lambda: all(mailbox(world, n) == "Archive" for n in LUNCH), "the conversation wasn't filed whole")
    expect(page.locator("#toast")).to_have_text("Archived 2 messages")
    world.server.fail("UID SEARCH", user=w.ADA)           # the first one undo looks for isn't found
    page.keyboard.press("z")
    expect(page.locator("#toast")).to_have_text("1 couldn’t be restored — press z to try again")
    until(page, lambda: sorted(mailbox(world, n) for n in LUNCH) == ["Archive", "INBOX"], "one back on the server")
    page.keyboard.press("z")
    until(page, lambda: all(mailbox(world, n) == "INBOX" for n in LUNCH), "both back on the server")
    expect(convo).to_have_count(1)
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
    before = page.locator("#rows .row[data-key]").count()
    world.deliver(w.ADA, "INBOX", w.variant(RECEIPT, Message_ID="<fresh@example.test>", Subject="Fresh off the press"))
    sse.publish("new-mail", {"account": w.ADA, "folder": "INBOX", "count": 1})
    until(page, lambda: page.locator("#rows .row[data-key]").count() > before, "the new mail never showed")
    expect(page.locator("#rows .row[data-key]").filter(has_text="Fresh off the press")).to_have_count(1)
    clean(page)


def test_what_changes_elsewhere_shows_on_an_open_list(page, app, world):
    """Nothing here touches the page: each change reaches it only as its event, flags-changed, reconcile and undo-done."""
    from mailmind.bootstrap import accounts
    from mailmind.bootstrap.mailbox import ingest, reconciler
    old = "ada-archive-01-old-thread.eml"
    opened(page)
    page.locator('#folderrail .frow[data-folder="Archive"]').click()
    listed = row(page, world, old)
    expect(listed).to_have_count(1)
    expect(page.locator("iframe.rd-iframe")).to_be_visible()
    page.keyboard.press("Escape")                         # a folder opens on its first message, and an open message is marked read
    expect(page.locator("iframe.rd-iframe")).to_have_count(0)
    expect(listed).not_to_have_class(re.compile(r"\bunread\b"))

    with world.server.lock:                               # marked unread in another client
        world.server.mailbox(w.ADA, "Archive").messages[world.uid(old)].flags.discard(w.SEEN)
    ingest.sync_folder(accounts.get(w.ADA), "Archive")
    expect(listed).to_have_class(re.compile(r"\bunread\b"))

    # Reconcile believes no empty answer, so the folder keeps something on the server.
    world.server.deliver(w.ADA, "Archive", w.variant("ada-inbox-03-newsletter.eml", Message_ID="<issue-219@example.test>"))
    with world.server.lock:                               # deleted in another client
        world.server.mailbox(w.ADA, "Archive").messages.pop(world.uid(old))

    def pruned():                                         # the ghost pruner's round, which yields while the page reads
        reconciler._auto_reconcile_one(accounts.get(w.ADA))
        return world.pk(old) is None
    until(page, pruned, "the ghost was never pruned")
    expect(listed).to_have_count(0)

    page.locator('#folderrail .frow[data-folder="INBOX"]').click()
    receipt = row(page, world, RECEIPT)
    receipt.click()
    page.keyboard.press("e")
    expect(receipt).to_have_count(0)
    until(page, lambda: mailbox(world, RECEIPT) == "Archive", "the server never filed it")
    expect(page.locator("#toast")).to_have_text("Archived")
    assert app.post("/api/undo", {}).json["ok"]           # another tab's Undo
    expect(page.locator("#rows .row[data-key]").filter(has_text="Your receipt for order 1042")).to_have_count(1)
    clean(page)


def test_a_rows_own_buttons_file_it_with_the_mouse(page, world):
    opened(page)
    r = row(page, world, RECEIPT)
    r.hover()
    r.locator(".ract.del").click()
    until(page, lambda: mailbox(world, RECEIPT) == "Trash", "the server never trashed it")
    expect(r).to_have_count(0)
    expect(page.locator("#reader")).to_have_class(re.compile("brief-pane"))
    clean(page)


def test_the_account_menu_changes_account_and_offers_all_inboxes(page, world):
    opened(page)
    page.locator(".acct-sw").click()
    expect(page.get_by_role("menu")).to_be_visible()
    page.get_by_role("menuitemradio", name=re.compile("^All inboxes")).click()
    expect(page.locator("#listTitle")).to_have_text("Inbox")
    assert page.url.endswith("/all")
    expect(page.locator("#rows .acctchip").first).to_be_visible()
    clean(page)


def test_a_tab_on_an_older_build_offers_a_reload_and_a_current_one_doesnt(page, world):
    opened(page)
    float_ = page.locator("#buildFloat")
    assert float_.is_hidden()
    page.pw.route("**/m/**", lambda route: route.fulfill(
        response=(r := route.fetch()), body=re.sub(r'(name="mailmind-build" content=")[^"]+', r"\1older", r.text())))
    page.pw.reload()
    float_.wait_for(state="visible")
    assert "MailMind was updated" in float_.inner_text()
    page.pw.unroute("**/m/**")
    page.locator("#buildReload").click()
    float_.wait_for(state="hidden")
    clean(page)


def test_a_narrow_window_shows_one_pane_with_a_way_back_and_a_folder_drawer(page, world):
    page.set_viewport_size({"width": 420, "height": 800})
    page.goto("/")
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
    page.goto("/")
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
    found = Axe().run(page.pw, options={"iframes": False}).response["violations"]
    assert {v["id"]: [n["target"] for n in v["nodes"]] for v in found} == {}


def test_it_meets_the_page_policy(page, world):
    opened(page)
    row(page, world, RECEIPT).click()
    expect(page.locator("iframe.rd-iframe")).to_be_visible()
    page.locator(".acct-sw").click()
    page.keyboard.press("Escape")
    clean(page)


def test_snooze_takes_it_off_the_list_by_preset_digit_and_undo_brings_it_back(page, world):
    opened(page)
    r = row(page, world, RECEIPT)
    r.click()
    expect(page.locator(".rd-subj")).to_be_visible()
    page.keyboard.press("h")
    expect(page.get_by_role("dialog", name="Snooze until")).to_be_visible()
    page.keyboard.press("2")
    expect(r).to_have_count(0)
    expect(page.locator("#toast")).to_have_text("Snoozed")
    page.locator('#folderrail .frow[data-folder="__snoozed__"]').click()
    expect(page.locator("#listTitle")).to_have_text("Snoozed")
    expect(page.locator("#rows .row[data-key]")).to_have_count(1)
    page.keyboard.press("z")
    expect(page.locator("#rows .row[data-key]")).to_have_count(0)
    clean(page)


def test_mute_files_the_thread_away_and_tells_the_server_it_was_read(page, world):
    opened(page)
    r = page.locator("#rows .row[data-key]").filter(has=page.locator(".thr-count")).filter(has_text="unch").first
    r.click()
    expect(page.locator("#thread .tmsg")).to_have_count(3)
    page.keyboard.press("w")
    expect(page.locator("#toast")).to_have_text("Muted")
    expect(r).to_have_count(0)
    page.locator('#folderrail .frow[data-folder="__muted__"]').click()
    expect(page.locator("#rows .row[data-key]")).to_have_count(1)
    clean(page)


def test_reply_later_keeps_the_row_moves_on_and_done_takes_it_out_of_the_stack(page, world):
    opened(page)
    r = row(page, world, RECEIPT)
    r.click()
    expect(page.locator(".rd-subj")).to_be_visible()
    first = page.url
    page.keyboard.press("l")
    expect(page.locator("#toast")).to_have_text("Saved to Reply later")
    until(page, lambda: page.url != first, "it didn't move on")
    expect(r).to_be_visible()
    page.locator('#folderrail .frow[data-folder="__replylater__"]').click()
    later = page.locator("#rows .row[data-key]")
    expect(later).to_have_count(1)
    later.click()
    expect(page.get_by_role("button", name="Done").first).to_be_visible()
    page.keyboard.press("l")
    expect(later).to_have_count(0)
    clean(page)


def test_marked_rows_file_together_and_one_undo_brings_them_all_back(page, world):
    opened(page)
    rows = page.locator("#rows .row[data-key]")
    before = rows.count()
    names = (RECEIPT, "ada-inbox-03-newsletter.eml")
    for name in names:
        one = row(page, world, name)
        one.hover()
        one.locator(".cbx").click()
    expect(page.locator(".bulkbar .bcount")).to_have_text("2 selected")
    asked = []
    page.pw.on("request", lambda r: asked.append(r.url) if "/api/" in r.url and r.method == "POST" else None)
    page.locator(".bulkbar .bb").filter(has_text="Archive").click()
    expect(page.locator("#toast")).to_have_text("Archived 2")
    assert [u for u in asked if "archive" in u or "bulk" in u] == [page.origin + "/api/bulk/file"]
    until(page, lambda: all(mailbox(world, n) == "Archive" for n in names), "they weren't filed")
    expect(rows).to_have_count(before - 2)
    expect(page.locator(".bulkbar")).to_have_count(0)
    page.keyboard.press("z")
    until(page, lambda: all(mailbox(world, n) == "INBOX" for n in names), "one Undo didn't reverse both")
    expect(rows).to_have_count(before)
    clean(page)


def test_x_marks_the_open_row_and_escape_clears_the_marks_first(page, world):
    opened(page)
    r = row(page, world, RECEIPT)
    r.click()
    expect(page.locator(".rd-subj")).to_be_visible()
    page.keyboard.press("x")
    expect(r).to_have_class(re.compile(r"\bmarked\b"))
    page.keyboard.press("Escape")
    expect(r).not_to_have_class(re.compile(r"\bmarked\b"))
    expect(page.locator(".rd-subj")).to_be_visible()
    clean(page)


def test_the_palette_finds_and_runs_a_command_and_escape_closes_it(page, world):
    opened(page)
    page.keyboard.press("ControlOrMeta+k")
    box = page.get_by_role("combobox", name="Command")
    expect(box).to_be_focused()
    box.fill("all inb")
    expect(page.locator(".pi.sel")).to_contain_text("All inboxes")
    box.press("Enter")
    expect(page.locator("#listTitle")).to_have_text("Inbox")
    expect(box).to_have_count(0)
    page.keyboard.press("ControlOrMeta+k")
    expect(box).to_be_focused()
    box.fill("zzzz nothing")
    expect(page.locator(".pnone")).to_have_text("No matches.")
    box.press("Escape")
    expect(box).to_have_count(0)
    clean(page)


def test_g_then_a_letter_goes_to_that_folder_by_its_role(page, world):
    opened(page)
    page.keyboard.press("g")
    expect(page.locator("#toast")).to_contain_text("Go to folder")
    page.keyboard.press("t")
    expect(page.locator("#listTitle")).to_have_text("Trash")
    page.keyboard.press("g")
    page.keyboard.press("z")
    expect(page.locator("#listTitle")).to_have_text("Snoozed")
    clean(page)


def test_a_search_saves_as_a_view_in_the_rail_and_the_view_runs_it(page, world):
    opened(page)
    page.pw.on("dialog", lambda d: d.accept("From Lin"))
    box = page.get_by_role("textbox", name="Search")
    box.fill("from:lin")
    box.press("Enter")
    page.locator(".ctx-save").click()
    expect(page.locator("#toast")).to_have_text("Saved view · From Lin")
    view = page.locator("#folderrail .frow[data-view]").filter(has_text="From Lin")
    expect(view).to_have_attribute("aria-current", "page")
    page.locator('#folderrail .frow[data-folder="INBOX"]').click()
    view.click()
    expect(page.locator("#listTitle")).to_have_text("Search")
    expect(box).to_have_value("from:lin")
    page.keyboard.press("[")      # a narrow rail shows only icons, and no way to remove a view
    view.hover()
    page.locator("#folderrail .vrow", has_text="From Lin").locator(".vdel").click()
    expect(view).to_have_count(0)
    clean(page)


def test_a_saved_summary_shows_at_once_with_no_call_and_a_full_one_comes_when_asked(page, world, ai):
    from mailmind.bootstrap.mailbox import store
    c = store.conn()
    try:
        answers.save_summary(c, world.pk(RECEIPT), "Saved words", 3, "personal", "Keep the receipt", "claude-opus-5-5", "2026-11-02")
        c.commit()
    finally:
        c.close()
    opened(page)
    expect(page.locator(".bf-narr")).to_be_visible()   # the day's brief is written as the Inbox opens: that one call
    row(page, world, RECEIPT).click()
    stored = page.locator("#aiCard.stored")
    expect(stored.locator(".ai-tldr")).to_have_text("Saved words")
    expect(stored.locator(".ai-action")).to_have_text("→ Keep the receipt")
    assert len(ai.requests) == 1
    ai.queue(fa.answer({"tldr": "Paid for thread.", "points": ["One order."], "action": "Keep it"}))
    page.keyboard.press("i")
    card = page.locator("#aiCard")
    expect(card.locator(".ai-tldr")).to_have_text("Paid for thread.")
    expect(card.locator(".ai-points li")).to_have_text(["One order."])
    expect(card.locator(".ai-action")).to_have_text("→ Keep it")
    clean(page)


def test_reading_settings_change_a_plain_mail_and_stay_after_a_reload(page, world):
    opened(page)
    row(page, world, RECEIPT).click()
    frame = page.locator("iframe.rd-iframe")
    expect(frame).to_be_visible()
    page.get_by_role("button", name="Reading settings").click()
    menu = page.get_by_role("dialog", name="Reading settings")
    menu.get_by_role("button", name="Mono").click()
    expect(menu.get_by_role("button", name="Mono")).to_have_attribute("aria-pressed", "true")
    face = "f => getComputedStyle(f.contentDocument.querySelector('pre')).fontFamily"
    until(page, lambda: "SF Mono" in frame.evaluate(face), "the frame kept its old face")
    menu.get_by_role("button", name="Wide").click()
    assert page.evaluate("getComputedStyle(document.body).getPropertyValue('--measure')").strip() == "920px"
    page.pw.reload()
    expect(frame).to_be_visible()
    until(page, lambda: "SF Mono" in frame.evaluate(f"f => f.contentDocument?.querySelector('pre') ? ({face})(f) : ''"), "the face didn't stay")
    clean(page)


def test_a_narrow_pane_keeps_its_hidden_verbs_under_more(page, world):
    page.set_viewport_size({"width": 700, "height": 800})
    page.goto("/")
    page.get_by_role("tab", name=re.compile("^Everything")).click()
    row(page, world, RECEIPT).click()
    more = page.get_by_role("button", name="More actions")
    expect(more).to_be_visible()
    more.click()
    item = page.get_by_role("menuitem", name="Unread")
    expect(item).to_be_visible()
    item.click()
    expect(page.locator("#toast")).to_have_text("Marked unread")
    clean(page)


def test_focus_reading_hides_the_list_and_enter_or_escape_ends_it(page, world):
    opened(page)
    r = row(page, world, RECEIPT)
    r.click()
    expect(page.locator(".rd-subj")).to_be_visible()
    page.locator("body").press("Enter")
    expect(page.locator("body")).to_have_class(re.compile(r"\bread-focus\b"))
    expect(r).to_be_hidden()
    page.keyboard.press("Escape")
    expect(page.locator("body")).not_to_have_class(re.compile(r"\bread-focus\b"))
    expect(r).to_be_visible()
    clean(page)


def test_axe_finds_nothing_with_the_palette_and_the_menus_open(page, world):
    from axe_playwright_python.sync_playwright import Axe
    opened(page)
    row(page, world, RECEIPT).click()
    expect(page.locator("iframe.rd-iframe")).to_be_attached()
    found = {}
    page.get_by_role("button", name="Reading settings").click()
    # React Aria keeps a visually hidden Dismiss button beside the dialog, which the region rule counts as content
    beside_the_dialog = {"region": {"enabled": False}}
    found["reading settings"] = Axe().run(page.pw, options={"iframes": False, "rules": beside_the_dialog}).response["violations"]
    page.keyboard.press("Escape")
    page.keyboard.press("h")
    found["snooze"] = Axe().run(page.pw, options={"iframes": False}).response["violations"]
    page.keyboard.press("Escape")
    page.keyboard.press("ControlOrMeta+k")
    found["palette"] = Axe().run(page.pw, options={"iframes": False}).response["violations"]
    assert {k: {v["id"]: [n["target"] for n in v["nodes"]] for v in vs} for k, vs in found.items() if vs} == {}


def test_at_the_daily_limit_a_summary_goes_only_when_you_say_so(page, world, ai):
    import time
    from mailmind.assistant.application.gateway import local_day
    from mailmind.assistant.application.ports import Spend, Usage
    from mailmind.bootstrap import assistant
    now = time.time()
    assistant.LEDGER.record(Spend(local_day(now), "ask", "claude-opus-5-5", "claude-opus-5-5", "ok", Usage(), 15_000_000, int(now)))
    ai.responder = lambda req: fa.answer({"tldr": "Over the limit, as asked.", "points": [], "action": ""})
    opened(page)
    row(page, world, RECEIPT).click()
    expect(page.locator(".rd-subj")).to_be_visible()
    ask = page.get_by_role("alertdialog", name="Today’s AI limit is reached")
    page.keyboard.press("i")
    expect(ask).to_contain_text("MailMind has spent $15.00 on AI today, and its daily limit is $15.00.")
    ask.get_by_role("button", name="Not now").click()
    expect(ask).to_be_hidden()
    expect(page.locator("#aiCard .ai-err")).to_contain_text("Couldn’t summarize")
    assert ai.requests == []
    page.keyboard.press("i")
    ask.get_by_role("button", name="Go over the limit").click()
    expect(page.locator("#aiCard .ai-tldr")).to_have_text("Over the limit, as asked.")
    assert all("status of 402" in p for p in page.problems)    # the two refusals the page asked about
    page.problems.clear()
    clean(page)
