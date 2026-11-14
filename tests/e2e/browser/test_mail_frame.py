"""A mail body in the app's frame, loaded from its own URL under its own policy: what a browser lets it do, and what
the page around it still does to it. tests/security/test_mail_frame.py holds what the route answers.

The sandbox and the policy are the walls and the sanitizer only stands behind them, so what would get past them has to
reach them: the tests that take `unsanitized` have the server hand the mail back as it came, as if the sanitizer had
missed everything (the protocol's `unsanitized on`), and the walls hold alone.
"""
import re

import pytest
from playwright.sync_api import expect

from fixtures import world as w
from mailmind.bootstrap.mailbox import store
from mailmind.platform import perimeter

pytestmark = pytest.mark.browser

TRACKER = "https://tracker.example.test/open.png"
SAID = re.compile(r"violates the following Content Security Policy directive|frame is sandboxed|The document is sandboxed")
HOSTILE = f"""<p id="said">Hello</p>
<script>parent.document.body.dataset.pwned = "script"</script>
<img src="{TRACKER}" alt="tracker">
<img src="data:," onerror="parent.document.body.dataset.pwned = 'onerror'">
<svg onload="parent.document.body.dataset.pwned = 'svg'"></svg>
<iframe srcdoc="&lt;script&gt;top.document.body.dataset.pwned = 'nested'&lt;/script&gt;"></iframe>
<iframe src="https://evil.example.test/frame"></iframe>
<object data="https://evil.example.test/object"></object>
<link rel="stylesheet" href="https://evil.example.test/style.css">
<style>@import url(https://evil.example.test/import.css); body{{background:url(https://tracker.example.test/bg.png)}}</style>
<meta http-equiv="refresh" content="0;url=https://evil.example.test/refresh">
<base href="https://evil.example.test/">
<form action="https://evil.example.test/form"><button id="go">Go</button></form>
<a id="js" href="javascript:parent.document.body.dataset.pwned = 'href'">click</a>"""


def clean(page):
    assert page.problems == [], page.problems
    assert page.blocked == [], page.blocked


def until(page, check, what):
    for _ in range(100):
        if check():
            return
        page.wait_for_timeout(50)
    assert check(), what


def arrives(world, body, ctype="text/html", subject="A message"):
    """A message that just landed in Ada's Inbox: its row's pk, and its key, which the page names it by."""
    raw = (f"From: Mallory <mallory@evil.example.test>\r\nTo: Ada Park <ada@example.test>\r\nSubject: {subject}\r\n"
           f"Date: Sun, 01 Nov 2026 09:00:00 +0000\r\nMessage-ID: <{abs(hash(body))}@evil.example.test>\r\nMIME-Version: 1.0\r\n"
           f"Content-Type: {ctype}; charset=utf-8\r\n\r\n{body}").encode()
    uid = world.deliver(w.ADA, "INBOX", raw)
    c = store.conn()
    r = c.execute("SELECT pk, message_key FROM messages WHERE account=? AND folder='INBOX' AND uid=?", (w.ADA, uid)).fetchone()
    c.close()
    return r["pk"], r["message_key"]


def inbox_uid(key):
    """The uid of message `key`'s one copy in Ada's Inbox."""
    c = store.conn()
    r = c.execute("SELECT uid FROM messages WHERE account=? AND folder='INBOX' AND message_key=?", (w.ADA, key)).fetchone()
    c.close()
    return r["uid"]


def reading(page, key):
    """The reader on the Inbox's copy of message `key`, its frame's document dressed."""
    page.goto(f"/m/{w.ADA.replace('@', '%40')}/INBOX?key={key}&folder=INBOX&uid={inbox_uid(key)}")
    frame = page.locator("iframe.rd-iframe")
    expect(frame).to_be_visible()
    until(page, lambda: frame.evaluate("f => !!f.contentDocument?.head?.querySelector('style')"), "the frame was never dressed")
    return frame


def loaded(page, frame):
    """Waits for the frame's document to have loaded: all it holds has tried to load, to run and to refresh by then. The page
    dresses it sooner, once it is parsed."""
    until(page, lambda: frame.evaluate("f => f.contentDocument?.readyState") == "complete", "the frame never loaded")


def asked(page, part):
    """A list that fills with every request whose URL holds `part`."""
    seen = []
    page.pw.on("request", lambda r: seen.append(r.url) if part in r.url else None)
    return seen


def only_refused(page):
    """Nothing went wrong but what the walls refused, which Chromium says on the console."""
    assert [p for p in page.problems if not SAID.search(p)] == []
    page.problems.clear()


def nothing_got_out(page):
    assert page.evaluate("document.body.dataset.pwned") is None
    assert page.blocked == []


@pytest.fixture
def unsanitized(app):
    """As if the sanitizer had missed everything, so what would get past the walls reaches them."""
    app.unsanitized()


def test_it_loads_from_its_own_url_with_its_own_policy(page, world):
    pk, key = arrives(world, "<p>Hello</p>")
    documents = []
    page.pw.on("response", lambda r: documents.append(r) if "/body.html" in r.url else None)
    frame = reading(page, key)
    assert frame.get_attribute("src") == f"/api/message/body.html?key={key}&folder=INBOX&uid={inbox_uid(key)}&images=0"
    assert frame.get_attribute("srcdoc") is None
    assert frame.get_attribute("sandbox") == "allow-same-origin"
    assert frame.evaluate("f => f.contentDocument.body.textContent") == "Hello"
    assert [d.status for d in documents] == [200]       # the perimeter took a frame's navigation for the page's own
    assert documents[0].header_value("content-security-policy").startswith("sandbox allow-same-origin; default-src 'none'; img-src data:;")
    clean(page)


def test_no_remote_image_loads_until_asked_and_then_it_does(page, world):
    pk, key = arrives(world, f'<p>Hello</p><img src="{TRACKER}" alt="tracker">')
    documents, answers = asked(page, "/body.html"), asked(page, f"/api/message/body?key={key}&")
    referrers = []
    page.pw.on("request", lambda r: referrers.append(r.headers.get("referer")) if r.url == TRACKER else None)
    frame = reading(page, key)
    expect(page.locator(".rd-imgbar")).to_contain_text("1 remote image hidden")
    expect(page.locator(".rd-extbar")).to_contain_text("1 tracker blocked")
    loaded(page, frame)
    read = len(answers)
    clean(page)                                     # nothing has gone to the tracker
    assert frame.evaluate("f => f.contentDocument.images[0].getAttribute('src')") is None
    page.get_by_role("button", name="Load images").click()
    until(page, lambda: page.blocked == [TRACKER], "the image was never asked for")
    expect(page.locator(".rd-imgbar")).to_have_count(0)
    expect(page.locator(".rd-extbar")).to_contain_text("no active content")
    assert [u.rsplit("images=")[1] for u in documents] == ["0", "1"]
    assert len(answers) == read                     # only the frame went back for it
    assert referrers == [None]                      # and what it asked of the sender's server names no page of this app
    page.blocked.clear()
    clean(page)


def test_load_images_adds_nothing_to_the_tabs_history(page, world):
    pk, key = arrives(world, f'<img src="{TRACKER}" alt="tracker">')
    frame = reading(page, key)
    before = page.evaluate("history.length")
    page.get_by_role("button", name="Load images").click()
    until(page, lambda: "images=1" in frame.evaluate("f => f.contentDocument?.URL ?? ''"), "the frame kept its URL")
    assert page.evaluate("history.length") == before
    page.blocked.clear()
    clean(page)


def link_at(frame, selector):
    return frame.evaluate("""(f, selector) => {
        const r = f.contentDocument.querySelector(selector).getBoundingClientRect(), o = f.getBoundingClientRect();
        return { x: o.x + r.x + r.width / 2, y: o.y + r.y + r.height / 2 };
    }""", selector)


def catching_opens(page):
    page.evaluate("() => { window.opens = []; window.open = (...a) => { window.opens.push(a); return null; }; }")


@pytest.mark.parametrize("button", ["left", "middle"])
def test_a_links_click_is_the_pages_to_take(page, world, button):
    pk, key = arrives(world, '<p><a href="https://ok.example.test/page">Read it</a></p>')
    frame = reading(page, key)
    url = frame.evaluate("f => f.contentDocument.URL")
    catching_opens(page)
    at = link_at(frame, "a")
    page.mouse.click(at["x"], at["y"], button=button)
    until(page, lambda: page.evaluate("window.opens.length") == 1, "the click was never taken")
    assert page.evaluate("window.opens") == [["https://ok.example.test/page", "_blank", "noopener,noreferrer"]]
    assert frame.evaluate("f => f.contentDocument.URL") == url
    clean(page)


def test_a_link_that_says_one_place_and_goes_to_another_asks_first(page, world):
    pk, key = arrives(world, '<p><a href="https://evil.example.test/">https://bank.example.test/</a></p>')
    frame = reading(page, key)
    catching_opens(page)
    asks = []
    page.pw.on("dialog", lambda d: (asks.append(d.message), d.dismiss()))
    at = link_at(frame, "a")
    page.mouse.click(at["x"], at["y"])
    until(page, lambda: len(asks) == 1, "it never asked")
    assert "Careful" in asks[0]
    assert page.evaluate("window.opens") == []
    clean(page)


def test_a_links_click_is_taken_again_after_images_are_loaded(page, world):
    pk, key = arrives(world, f'<p><a href="https://ok.example.test/page">Read it</a></p><img src="{TRACKER}" alt="tracker">')
    frame = reading(page, key)
    page.get_by_role("button", name="Load images").click()
    until(page, lambda: "images=1" in frame.evaluate("f => f.contentDocument?.URL ?? ''") and frame.evaluate(
        "f => !!f.contentDocument?.head?.querySelector('style')"), "the frame kept its URL")
    catching_opens(page)
    at = link_at(frame, "a")
    page.mouse.click(at["x"], at["y"])
    until(page, lambda: page.evaluate("window.opens.length") == 1, "the click was never taken")
    page.blocked.clear()
    clean(page)


def test_a_reading_setting_changes_a_plain_mail_without_fetching_it(page, world):
    pk, key = arrives(world, "Plain words.", "text/plain")
    documents, answers = asked(page, "/body.html"), asked(page, f"/api/message/body?key={key}&")
    frame = reading(page, key)
    fetched, read = len(documents), len(answers)
    assert fetched == 1
    style = "getComputedStyle(f.contentDocument.querySelector('pre'))"
    assert "SF Mono" not in frame.evaluate(f"f => {style}.fontFamily")
    page.get_by_role("button", name="Reading settings").click()
    menu = page.get_by_role("dialog", name="Reading settings")
    menu.get_by_role("button", name="Mono").click()
    until(page, lambda: "SF Mono" in frame.evaluate(f"f => {style}.fontFamily"), "the face stayed")
    menu.get_by_role("button", name="Extra large").click()
    until(page, lambda: frame.evaluate(f"f => {style}.fontSize") == "20px", "the size stayed")
    menu.get_by_role("button", name="Dark").click()
    until(page, lambda: frame.evaluate("f => getComputedStyle(f.contentDocument.body).backgroundColor") != "rgb(255, 253, 248)", "the colors stayed")
    assert (len(documents), len(answers)) == (fetched, read)
    clean(page)


NIGHT_PAPER, NIGHT_INK = "rgb(22, 33, 58)", "rgb(237, 231, 215)"      # :root's --bg and --ink in tokens.css
THEMES = {"day": 0, "dusk": 1, "night": 2}      # clicks of the theme button, from the Day the page opens on


def _dark_mail_colors(page):
    page.get_by_role("button", name="Reading settings").click()
    page.get_by_role("dialog", name="Reading settings").get_by_role("button", name="Dark").click()


@pytest.mark.parametrize("clicks", THEMES.values(), ids=THEMES.keys())
def test_dark_plain_mail_is_on_night_paper_whichever_theme_the_app_is_in(page, world, clicks):
    pk, key = arrives(world, "Plain words.", "text/plain")
    frame = reading(page, key)
    for _ in range(clicks):
        page.locator("#themeBtn").click()
    _dark_mail_colors(page)
    body = "getComputedStyle(f.contentDocument.body)"
    until(page, lambda: frame.evaluate(f"f => {body}.backgroundColor") == NIGHT_PAPER, "the paper stayed the theme's")
    assert frame.evaluate(f"f => {body}.color") == NIGHT_INK
    clean(page)


@pytest.mark.parametrize("clicks", THEMES.values(), ids=THEMES.keys())
def test_dark_rich_mail_is_inverted_onto_night_paper_whichever_theme_the_app_is_in(page, world, clicks):
    pk, key = arrives(world, "<p>Rich words.</p>")
    frame = reading(page, key)
    for _ in range(clicks):
        page.locator("#themeBtn").click()
    _dark_mail_colors(page)
    until(page, lambda: "invert" in frame.evaluate("f => getComputedStyle(f.contentDocument.body).filter"), "it wasn't inverted")
    until(page, lambda: frame.evaluate("f => getComputedStyle(f.contentDocument.documentElement).backgroundColor") == NIGHT_PAPER, "the page stayed the theme's")
    clean(page)


def test_a_reading_setting_darkens_rich_mail_without_fetching_it(page, world):
    pk, key = arrives(world, "<p>Rich words.</p>")
    documents = asked(page, "/body.html")
    frame = reading(page, key)
    fetched = len(documents)
    page.get_by_role("button", name="Reading settings").click()
    menu = page.get_by_role("dialog", name="Reading settings")
    menu.get_by_role("button", name="Dark").click()
    until(page, lambda: "invert" in frame.evaluate("f => getComputedStyle(f.contentDocument.body).filter"), "it wasn't inverted")
    menu.get_by_role("button", name="Original").click()
    until(page, lambda: frame.evaluate("f => getComputedStyle(f.contentDocument.body).filter") == "none", "it stayed inverted")
    assert len(documents) == fetched
    clean(page)


def test_the_frame_fits_what_a_reading_setting_makes_of_it(page, world):
    pk, key = arrives(world, "\r\n".join(f"Line {n}" for n in range(60)), "text/plain")
    frame = reading(page, key)
    until(page, lambda: frame.evaluate("f => parseInt(f.style.height) > 0"), "the frame was never sized")
    small = frame.evaluate("f => parseInt(f.style.height)")
    page.get_by_role("button", name="Reading settings").click()
    page.get_by_role("dialog", name="Reading settings").get_by_role("button", name="Extra large").click()
    until(page, lambda: frame.evaluate("f => parseInt(f.style.height)") > small, "the frame kept its height")
    clean(page)


def drawn_height(page):
    """The frame's height once React has drawn what the page last asked of it: its render waits in a message ahead of this one."""
    return page.evaluate("""() => new Promise(done => { const c = new MessageChannel();
        c.port1.onmessage = () => done(parseInt(document.querySelector('iframe.rd-iframe').style.height)); c.port2.postMessage(null) })""")


def test_a_narrower_column_fits_the_frame_again(page, world):
    pk, key = arrives(world, "Words and more words. " * 200, "text/plain")
    frame = reading(page, key)
    until(page, lambda: frame.evaluate("f => parseInt(f.style.height) > 0"), "the frame was never sized")
    wide = frame.evaluate("f => parseInt(f.style.height)")
    page.get_by_role("button", name="Reading settings").click()
    page.get_by_role("dialog", name="Reading settings").get_by_role("button", name="Narrow").click()
    until(page, lambda: frame.evaluate("f => parseInt(f.style.height)") > wide, "the frame kept its height")
    clean(page)


def test_the_new_frame_images_load_into_starts_at_the_olds_height(page, world):
    tall = "".join(f"<p>Line {n}</p>" for n in range(40))
    pk, key = arrives(world, f'{tall}<img src="{TRACKER}" alt="tracker">')
    frame = reading(page, key)
    until(page, lambda: frame.evaluate("f => parseInt(f.style.height) > 500"), "the frame was never sized")
    height = frame.evaluate("f => f.style.height")
    page.evaluate("""() => {
        window.started = [];
        new MutationObserver((changes) => {
            for (const c of changes) for (const n of c.addedNodes) if (n.tagName === "IFRAME") window.started.push(n.style.height);
        }).observe(document.body, { subtree: true, childList: true });
    }""")
    page.get_by_role("button", name="Load images").click()
    until(page, lambda: page.evaluate("window.started.length") > 0, "no new frame")
    assert page.evaluate("window.started") == [height]
    page.blocked.clear()
    clean(page)


def test_a_plain_reply_folds_its_quote_and_keeps_every_word(page, world):
    pk, key = arrives(world, "Yes, Thursday works.\r\n\r\nOn Monday Ada wrote:\r\n> Are you free <then>?\r\n", "text/plain")
    frame = reading(page, key)
    until(page, lambda: frame.evaluate("f => !!f.contentDocument.querySelector('details.mm-quote')"), "it never folded")
    assert frame.evaluate("f => f.contentDocument.querySelector('pre').textContent") == "Yes, Thursday works.\n"
    assert frame.evaluate("f => f.contentDocument.querySelector('details pre').textContent") == "On Monday Ada wrote:\n> Are you free <then>?\n"
    assert frame.evaluate("f => f.contentDocument.querySelector('details').open") is False
    assert frame.evaluate("f => f.contentDocument.querySelector('details pre').children.length") == 0
    clean(page)


def test_opening_a_folded_quote_grows_the_frame_to_it_and_closing_it_gives_that_back(page, world):
    quote = "\r\n".join(f"> line {n}" for n in range(30))
    pk, key = arrives(world, f"Yes, Thursday works.\r\n\r\nOn Monday Ada wrote:\r\n{quote}\r\n", "text/plain")
    frame = reading(page, key)
    until(page, lambda: frame.evaluate("f => !!f.contentDocument.querySelector('details.mm-quote')"), "it never folded")
    loaded(page, frame)
    folded = drawn_height(page)
    summary = page.frame_locator("iframe.rd-iframe").locator("summary")
    summary.click()
    until(page, lambda: frame.evaluate("f => parseInt(f.style.height)") > folded + 300, "the opened quote got no room")
    summary.click()
    until(page, lambda: frame.evaluate("f => parseInt(f.style.height)") == folded, "the closed quote kept its room")
    clean(page)


def test_a_message_gone_by_the_time_its_frame_asks_says_so_in_the_frame(page, app, world):
    pk, key = arrives(world, f'<p>Hello</p><img src="{TRACKER}" alt="tracker">')
    frame = reading(page, key)
    with world.server.lock:                               # deleted in another client
        world.server.mailbox(w.ADA, "INBOX").messages.pop(inbox_uid(key))
    # A reconcile another tab asked for takes its row away, and tells this page nothing.
    assert app.post("/api/reconcile", {"account": w.ADA}).json["total_pruned"] == 1
    page.get_by_role("button", name="Load images").click()
    until(page, lambda: "isn’t here any more" in frame.evaluate("f => f.contentDocument?.body?.textContent ?? ''"), "it says nothing")
    assert [p for p in page.problems if "404" not in p] == []      # the browser notes the status, and nothing else went wrong
    page.problems.clear()
    clean(page)


def test_the_page_lets_no_remote_image_in_though_a_frame_may_ask_for_one(page, world):
    answer = page.goto("/")
    assert answer.header_value("content-security-policy") == perimeter.page_policy()
    page.evaluate("""url => {
        const i = document.createElement("img");
        i.src = url;
        document.body.append(i);
    }""", TRACKER)
    until(page, lambda: any("img-src" in p for p in page.problems), "the policy said nothing")
    assert page.blocked == []
    page.problems.clear()
    pk, key = arrives(world, f'<img src="{TRACKER}" alt="tracker">')
    reading(page, key)
    page.get_by_role("button", name="Load images").click()
    until(page, lambda: page.blocked == [TRACKER], "the frame's own policy didn't let it through")
    page.blocked.clear()
    clean(page)


def test_two_copies_of_one_key_in_one_folder_each_open_as_themselves(page, world):
    """A sender can reuse a Message-ID, so two copies in the Inbox can share a key. A link names one by its uid, and the
    reader's subject and the frame's body are both that copy's."""
    def copy_with(subject, said):
        raw = (f"From: Mallory <mallory@evil.example.test>\r\nTo: Ada Park <ada@example.test>\r\nSubject: {subject}\r\n"
               f"Date: Sun, 01 Nov 2026 09:00:00 +0000\r\nMessage-ID: <reused@evil.example.test>\r\nMIME-Version: 1.0\r\n"
               f"Content-Type: text/html; charset=utf-8\r\n\r\n<p>{said}</p>").encode()
        return world.deliver(w.ADA, "INBOX", raw)
    first, second = copy_with("The first copy", "What the first says"), copy_with("The second copy", "What the second says")
    c = store.conn()
    keys = {r["message_key"] for r in c.execute("SELECT message_key FROM messages WHERE account=? AND folder='INBOX' AND uid IN (?, ?)",
                                                 (w.ADA, first, second))}
    c.close()
    assert len(keys) == 1
    key = keys.pop()
    for uid, subject, said in ((second, "The second copy", "What the second says"), (first, "The first copy", "What the first says")):
        page.goto(f"/m/{w.ADA.replace('@', '%40')}/INBOX?key={key}&folder=INBOX&uid={uid}")
        expect(page.locator(".rd-subj")).to_have_text(subject)
        frame = page.locator("iframe.rd-iframe")
        expect(frame).to_have_attribute("src", f"/api/message/body.html?key={key}&folder=INBOX&uid={uid}&images=0")
        until(page, lambda: frame.evaluate("f => f.contentDocument?.body?.textContent ?? ''").strip() == said,
              f"the frame never showed {subject}")
    clean(page)


def test_a_conversation_gives_each_message_its_own_frame(page, world):
    page.goto(f"/m/{w.ADA.replace('@', '%40')}/INBOX")
    page.get_by_role("tab", name=re.compile("^Everything")).click()
    page.locator("#rows .row[data-key]").filter(has=page.locator(".thr-count")).filter(has_text="unch").first.click()
    expect(page.locator("#thread .tmsg")).to_have_count(3)
    page.locator("#thread .tmsg-hd").first.click()
    frames = page.locator("iframe.rd-iframe")
    expect(frames).to_have_count(2)
    sources = [frames.nth(i).get_attribute("src") for i in range(2)]
    assert sources[0] != sources[1] and all(re.search(r"/body\.html\?key=\d+&folder=[^&]+&uid=\d+&images=0$", s) for s in sources)
    until(page, lambda: all(frames.nth(i).evaluate("f => !!f.contentDocument?.head?.querySelector('style')") for i in range(2)),
          "a frame was never dressed")
    clean(page)


# CSS a sanitizer once wrote so that a browser read it otherwise than it was checked: a comment opener, a line break in
# a selector's string or a square bracket a later selector closed let a later value's string be read as CSS.
READ_OTHERWISE = r"""<p title="attribute" style='font-family:\/\*;font-family:"\*\/;position:fixed;top:0;left:0;width:9px;"'>a</p>
<p style='font-family:a//**/*;font-family:"*/;position:fixed;top:0;x:"'>b</p>
<style>p{font-family:\/\*}q{font-family:"\*\/}*{position:fixed;top:0}"}</style>
<style>p{font-family:a//**/*}q{font-family:"*/}*{position:fixed;top:0}"}</style>
<style>@media /*{p{font-family:"*/{*{position:fixed}}"}}</style>
<style>a[title="x
"]{color:red}q{font-family:"]{}*{position:fixed}"}</style>
<style>p{font-family:[}a]{color:red}x;position:fixed;top:0;y{color:blue}</style>
<style>p[title=sheet]{color:rgb(4, 5, 6)}</style>
<p title="sheet">c</p>"""


def test_no_css_in_a_message_makes_anything_fixed(page, world):
    """Each of these made elements position:fixed, which can lay a message over the app's own page, before the sanitizer
    wrote only CSS a browser reads as written. What it keeps of the rest still applies."""
    pk, key = arrives(world, READ_OTHERWISE)
    frame = reading(page, key)
    loaded(page, frame)
    fixed = frame.evaluate("""f => [...f.contentDocument.querySelectorAll('*')]
        .filter(e => f.contentWindow.getComputedStyle(e).position === 'fixed').map(e => e.tagName)""")
    assert fixed == []
    color = frame.evaluate("f => f.contentWindow.getComputedStyle(f.contentDocument.querySelector('[title=sheet]')).color")
    assert color == "rgb(4, 5, 6)"
    clean(page)


HEADED = """<!doctype html><html><head><meta charset="utf-8"><title>The news</title>
<style>p[title=headed]{color:rgb(4, 5, 6);position:fixed} body{background:url(https://tracker.example.test/bg.png)}</style>
<noscript><style>p[title=noscript]{color:rgb(7, 8, 9)}</style></noscript>
<template><style>p[title=headed]{color:rgb(1, 1, 1)}</style></template>
</head><body><p title="headed">Styled from the head</p><p title="noscript">And from its noscript</p></body></html>"""


def test_a_heads_style_sheet_applies_in_the_frame(page, world):
    """A newsletter styled from its head shows as it was sent: the head's sheets come through the sanitizer, held to the
    same CSS checks as any other, and apply in the frame. One in a <template> applies to nothing."""
    pk, key = arrives(world, HEADED)
    frame = reading(page, key)
    loaded(page, frame)
    style = "f.contentWindow.getComputedStyle(f.contentDocument.querySelector('[title={}]'))"
    assert frame.evaluate(f"f => {style.format('headed')}.color") == "rgb(4, 5, 6)"
    assert frame.evaluate(f"f => {style.format('noscript')}.color") == "rgb(7, 8, 9)"
    assert frame.evaluate(f"f => {style.format('headed')}.position") == "static"
    assert frame.evaluate("f => f.contentDocument.title") == ""
    clean(page)                                     # the sheet's remote background was never asked for


NESTED = """<p>Before</p>
<form action="https://evil.example.test/form">A field<form>b</form><p title="after">After a form</p></form>
<button>c<button>d</button><p title="after">After a button</p></button>
<select><option>o</option><select><option>p</option></select><p title="after">After a select</p></select>"""


def test_the_text_after_a_nested_form_button_or_select_shows(page, world):
    """A browser ends a form at the first </form> in it, and a button or select at one of its own nested in it, so the
    text after each shows, and nothing of a form, a button or a select does."""
    pk, key = arrives(world, NESTED)
    frame = reading(page, key)
    loaded(page, frame)
    after = "f => [...f.contentDocument.querySelectorAll('[title=after]')].map(p => p.innerText)"
    assert frame.evaluate(after) == ["After a form", "After a button", "After a select"]
    assert frame.evaluate("f => f.contentDocument.querySelectorAll('form, button, select, input, option').length") == 0
    assert "field" not in frame.evaluate("f => f.contentDocument.body.innerText")
    clean(page)


# The walls on mail the sanitizer let everything through: the sandbox and the policy hold alone.

def test_a_hostile_body_runs_nothing_and_reaches_nothing(page, world, unsanitized):
    pk, key = arrives(world, HOSTILE)
    frame = reading(page, key)
    assert frame.evaluate("f => f.contentDocument.querySelectorAll('script').length") > 0     # it did get here
    loaded(page, frame)
    nothing_got_out(page)
    assert "/body.html" in frame.evaluate("f => f.contentDocument.URL")
    only_refused(page)


def test_what_a_hostile_body_can_be_clicked_into_stops_too(page, world, unsanitized):
    pk, key = arrives(world, HOSTILE)
    frame = reading(page, key)
    url = frame.evaluate("f => f.contentDocument.URL")
    frame.evaluate("f => f.contentDocument.getElementById('go').click()")
    frame.evaluate("f => f.contentDocument.getElementById('js').click()")
    page.wait_for_timeout(300)      # a javascript: link would run in a task after the click; nothing says it was refused
    nothing_got_out(page)
    assert frame.evaluate("f => f.contentDocument.URL") == url
    only_refused(page)


def test_with_images_asked_for_a_hostile_body_still_runs_nothing(page, world, unsanitized):
    pk, key = arrives(world, HOSTILE)
    reading(page, key)
    page.get_by_role("button", name="Load images").click()
    until(page, lambda: TRACKER in page.blocked, "the image was never asked for")
    frame = page.locator("iframe.rd-iframe")
    until(page, lambda: "images=1" in frame.evaluate("f => f.contentDocument?.URL ?? ''"), "the frame kept its URL")
    loaded(page, frame)
    assert page.evaluate("document.body.dataset.pwned") is None
    assert all(u.startswith("https://tracker.example.test/") for u in page.blocked), page.blocked     # images, and nothing else
    only_refused(page)


def test_the_documents_own_sandbox_holds_with_no_attribute_on_the_frame(page, world, unsanitized):
    pk, key = arrives(world, HOSTILE)
    reading(page, arrives(world, "<p>Something else</p>")[1])
    page.evaluate("""url => {
        const f = document.createElement("iframe");
        f.id = "bare";
        f.src = url;
        document.body.append(f);
    }""", f"/api/message/body.html?key={key}&folder=INBOX&images=0")
    bare = page.locator("#bare")
    assert bare.get_attribute("sandbox") is None
    until(page, lambda: bare.evaluate("f => f.contentDocument?.getElementById('said')?.textContent") == "Hello", "it never loaded")
    loaded(page, bare)
    # The policy's default-src would stop a script alone. Only a sandbox stops a <meta> refresh, and only the header's is here.
    until(page, lambda: any("The document is sandboxed" in p for p in page.problems), "the refresh wasn't refused")
    nothing_got_out(page)
    only_refused(page)


def test_the_documents_own_policy_stops_a_remote_image_the_sanitizer_let_through(page, world, unsanitized):
    pk, key = arrives(world, f'<img src="{TRACKER}" alt="tracker">')
    frame = reading(page, key)
    assert frame.evaluate("f => f.contentDocument.images.length") == 1
    loaded(page, frame)
    assert page.blocked == []
    only_refused(page)


def test_a_mail_as_tall_as_its_frame_doesnt_keep_growing_it(page, world, unsanitized):
    pk, key = arrives(world, "<style>html,body{height:100%}</style><p>Hello</p>")
    frame = reading(page, key)
    loaded(page, frame)
    page.wait_for_timeout(200)      # the page fits it again on load, after it has drawn what came before
    settled = frame.evaluate("f => parseInt(f.style.height)")
    page.wait_for_timeout(500)      # what this asks is that time passing grows it no more
    assert frame.evaluate("f => parseInt(f.style.height)") == settled < 2000
    clean(page)


def test_a_mail_as_tall_as_its_frame_keeps_its_height_however_often_it_is_fitted_again(page, world, unsanitized):
    pk, key = arrives(world, "<style>html,body{height:100%}</style><p>Hello</p>")
    frame = reading(page, key)
    loaded(page, frame)
    page.get_by_role("button", name="Reading settings").click()
    menu = page.get_by_role("dialog", name="Reading settings")
    heights = []
    for colors, dark in [("Dark", True), ("Original", False)] * 3:     # each dresses the mail again, and fits it as it does
        menu.get_by_role("button", name=colors).click()
        until(page, lambda: frame.evaluate("f => f.contentDocument.body.classList.contains('mm-dark')") is dark, "it wasn't dressed again")
        heights.append(drawn_height(page))
    assert len(set(heights[0::2])) == len(set(heights[1::2])) == 1 and max(heights) < 300, heights   # each look its own height
    clean(page)
