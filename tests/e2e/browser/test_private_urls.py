"""Nothing of a person's in what the browser keeps of the app: no address, folder name, search, question or topic in any URL a
tab goes to, which the browser writes into its history in plaintext and may sync, and none in its storage. A route that holds
any is a place, `/p/<id>`, whose route the server keeps; Back, a reload and a link opened in a new tab still come back to it."""
import json
import re
from urllib.parse import unquote, urlsplit

import pytest
from playwright.sync_api import expect

from fakes import anthropic_api as fa
from fixtures import world as w
from mailmind.bootstrap.assistant import answers
from mailmind.bootstrap.mailbox import store

pytestmark = pytest.mark.browser

LUNCH = ("ada-inbox-01-lunch.eml", "ada-inbox-02-lunch-confirm.eml")
RECEIPT = "ada-inbox-05-receipt.eml"
SEARCHED = "rhubarb receipt"           # what the walk searches for: no URL may hold it
TOPIC = "Lunch plans"
# What no URL and no storage may hold: each account's address, the folders the walk opens, the search, the topic.
PERSONAL = [*(a for a, *_ in w.ACCOUNTS), "Sent", "Archive", "INBOX", "Folders/Clients", "rhubarb", TOPIC]
WRITTEN_AS_ITSELF = re.compile(r"/|/all|/all/__[a-z]+__|/documents|/brief/(needs|tidy)")


def clean(page):
    assert page.problems == [], page.problems
    assert page.blocked == [], page.blocked


def told(text):
    """Each way a URL or a stored value could hold `text`."""
    return {text, text.replace("@", "%40"), text.replace("/", "%2F"), text.replace(" ", "+"), text.replace(" ", "%20")}


def holds_nothing_personal(url):
    path = url.split("://", 1)[-1].split("/", 1)[-1]
    found = [p for p in PERSONAL for t in told(p) if t in path or t in unquote(path)]
    assert found == [], (url, found)
    where = "/" + path.split("?")[0]
    assert WRITTEN_AS_ITSELF.fullmatch(where) or re.fullmatch(r"/p/[A-Za-z0-9_-]{22}", where), url


def the_brief_has_a_topic(app, world, ai):
    with app.stopped():                 # what triage said of the lunch confirmation and nothing else, as no pass would
        c = store.conn()
        try:
            answers.save_summary(c, world.pk(LUNCH[1]), "A summary.", 5, "action-needed", "Reply", "claude-opus-5-5",
                                 "2026-11-02T11:00:00+00:00")
            c.commit()
        finally:
            c.close()
    groups = [{"label": TOPIC, "count": 1, "pks": [world.pk(LUNCH[1])]}]
    ai.responder = lambda req: (fa.answer({"narrative": "Lin is waiting on lunch.", "groups": groups})
                                if "narrative" in ((req.schema or {}).get("properties") or {}) else None)


def every_url(page):
    """Each URL the tab goes to, pushState's and replaceState's included."""
    urls = []
    page.pw.on("framenavigated", lambda frame: urls.append(frame.url) if frame.parent_frame is None else None)
    return urls


def title(page, text):
    expect(page.locator("#listTitle")).to_have_text(text)


def search(page, text):
    page.keyboard.press("/")
    box = page.get_by_role("combobox", name="Search")
    box.fill(text)
    box.press("Enter")
    title(page, "Search")


def at_a_place(page):
    assert re.fullmatch(r"/p/[A-Za-z0-9_-]{22}", urlsplit(page.url).path), page.url


def stored(page):
    return page.pw.evaluate("JSON.stringify([Object.entries(localStorage), Object.entries(sessionStorage), history.state])")


def test_walking_the_app_puts_nothing_personal_in_a_url_or_in_storage(page, app, world, ai):
    the_brief_has_a_topic(app, world, ai)
    urls = every_url(page)
    page.goto("/")
    title(page, "Inbox")
    page.locator(".bf-topic").click()                                     # a topic from the brief
    title(page, TOPIC)
    page.locator('#folderrail .frow[data-folder="Sent"]').click()         # a folder
    title(page, "Sent")
    page.locator('#folderrail .frow[data-folder="INBOX"]').click()
    title(page, "Inbox")
    search(page, SEARCHED)                                                # a search
    search(page, "order 1042")
    page.get_by_role("option").first.click()                              # a message opened from it
    expect(page.locator("iframe.rd-iframe")).to_have_count(1)
    page.locator('#folderrail .frow[data-folder="INBOX"]').click()
    page.get_by_role("tab", name=re.compile("^Everything")).click()
    page.locator("#rows .row[data-key]").filter(has=page.locator(".thr-count")).filter(has_text="unch").first.click()
    expect(page.locator("#thread .tmsg")).to_have_count(3)                # a conversation
    assert len(set(urls)) >= 6, urls
    for url in [*urls, page.url]:
        holds_nothing_personal(url)
    kept = stored(page)
    assert [p for p in PERSONAL for t in told(p) if t in kept] == [], kept
    assert '"mm_lastlook"' not in kept and json.loads(kept)[2] in (None, {"mmTrap": 1})
    clean(page)


def on_its_first_message(page):
    expect(page.locator(".rd-subj")).to_be_visible()      # a folder opens its first message, at a place of its own
    page.where()                                          # which has reached the server, as it would long before a person goes on


def test_a_place_reloaded_or_opened_in_another_tab_is_the_same_view(page, world):
    page.goto("/")
    title(page, "Inbox")
    page.locator('#folderrail .frow[data-folder="Archive"]').click()
    title(page, "Archive")
    assert page.where() == f"/m/{w.ADA.replace('@', '%40')}/Archive"
    on_its_first_message(page)
    at_a_place(page)
    at = page.url
    page.reload()
    title(page, "Archive")
    assert page.url == at
    expect(page.locator('#folderrail .frow[data-folder="Archive"]')).to_have_attribute("aria-current", "page")
    other = page.pw.context.new_page()
    other.goto(at)
    expect(other.locator("#listTitle")).to_have_text("Archive")
    expect(other.locator("#rows .row[data-key]")).to_have_count(1)
    other.close()
    clean(page)


def test_a_message_and_a_search_open_again_from_their_place(page, world):
    page.goto("/")
    search(page, "order 1042")
    page.get_by_role("option").first.click()
    expect(page.locator("iframe.rd-iframe")).to_have_count(1)
    assert "q=order+1042" in page.where() and f"key={world.key(RECEIPT)}" in page.where()
    at_a_place(page)
    page.reload()
    title(page, "Search")
    expect(page.locator(f'#rows .row[data-key="{world.key(RECEIPT)}"]')).to_have_attribute("aria-selected", "true")
    expect(page.locator("iframe.rd-iframe")).to_have_count(1)
    expect(page.frame_locator("iframe.rd-iframe").locator("body")).to_contain_text("Total")
    clean(page)


def at_sent(page):
    title(page, "Sent")
    on_its_first_message(page)


def test_back_after_a_reload_comes_back_to_the_place_before(page, world):
    page.goto("/")
    title(page, "Inbox")
    page.locator('#folderrail .frow[data-folder="Sent"]').click()
    at_sent(page)
    page.locator('#folderrail .frow[data-folder="Archive"]').click()
    title(page, "Archive")
    on_its_first_message(page)
    at_a_place(page)
    page.reload()
    title(page, "Archive")
    page.go_back()                                        # to an entry this page didn't make, at a place it doesn't know
    at_sent(page)
    at_a_place(page)
    page.go_back()
    title(page, "Inbox")
    page.go_forward()
    at_sent(page)
    assert page.where().startswith(f"/m/{w.ADA.replace('@', '%40')}/Sent?key=")
    clean(page)


def test_a_folders_link_opened_in_a_new_tab_opens_that_folder(page, world):
    page.goto("/")
    title(page, "Inbox")
    link = page.locator('#folderrail .frow[data-folder="Archive"]')
    href = link.get_attribute("href")
    assert re.fullmatch(r"/p/[A-Za-z0-9_-]{22}", href)
    link.hover()                                          # the pointer comes over it on its way to the click
    assert page.where(href) == f"/m/{w.ADA.replace('@', '%40')}/Archive"      # so it has reached the server before the click
    with page.pw.context.expect_page() as opening:
        link.click(modifiers=["ControlOrMeta"])
    tab = opening.value
    expect(tab.locator("#listTitle")).to_have_text("Archive")
    title(page, "Inbox")                                  # this tab stays where it was
    tab.close()
    clean(page)


def kept():
    """The ids of the places the server keeps, from this test's own database."""
    from mailmind.platform.db.uow import UnitOfWork
    with UnitOfWork().read() as c:
        return {row[0] for row in c.execute("SELECT id FROM places")}


def places_answered(page):
    """Each request the page sends with places, and whether it has been answered."""
    sent = {}
    page.pw.on("request", lambda r: sent.setdefault(r, False) if r.url.endswith("/api/places") else None)
    page.pw.on("requestfinished", lambda r: sent.update({r: True}) if r.url.endswith("/api/places") else None)
    return sent


def settled(page, sent):
    page.heard_all_asked()
    for _ in range(100):
        if all(sent.values()):
            return
        page.wait_for_timeout(50)
    raise AssertionError("a place sent was never answered")


def test_drawing_the_rail_keeps_no_place_and_a_link_hovered_opens_its_own_elsewhere(page, world):
    sent = places_answered(page)
    page.goto("/")
    title(page, "Inbox")
    links = page.locator('#folderrail a.frow[href^="/p/"]')
    assert links.count() >= 10, links.count()
    at_a_place(page)
    home = urlsplit(page.url).path[len("/p/"):]
    settled(page, sent)
    assert kept() == {home}                               # where the page went, and no link drawn
    link = page.locator('#folderrail .frow[data-folder="Archive"]')
    href = link.get_attribute("href")
    link.hover()
    settled(page, sent)
    assert kept() == {home, href[len("/p/"):]}            # just the one pointed at
    other = page.pw.context.new_page()
    other.goto(href)
    expect(other.locator("#listTitle")).to_have_text("Archive")
    other.close()
    clean(page)


def test_a_place_the_server_doesnt_know_opens_at_home(page, world):
    page.goto("/p/AAAAAAAAAAAAAAAAAAAAAA")
    title(page, "Inbox")
    at_a_place(page)                                      # home's own place: the one asked for names nothing
    assert page.where() == f"/m/{w.ADA.replace('@', '%40')}/INBOX"
    clean(page)


@pytest.mark.parametrize("typed, shown", [(f"/m/{w.BEA}/INBOX", "Inbox"), ("/search?q=order+1042", "Search"), ("/all", "Inbox"),
                                          ("/documents", "Documents")])
def test_a_url_typed_the_readable_way_still_opens(page, world, typed, shown):
    page.goto(typed)
    title(page, shown)
    clean(page)
