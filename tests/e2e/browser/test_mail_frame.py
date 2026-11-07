"""A mail body in the app's frame, loaded from its own URL under its own policy: what a browser lets it do, and what
the page around it still does to it. tests/security/test_mail_frame.py holds what the route answers.

Some of these serve the mail unsanitized, since the sandbox and the policy are the walls and the sanitizer only stands behind
them, so what would get past them has to reach them.
"""
import re

import pytest
from playwright.sync_api import expect

from fixtures import world as w
from mailmind.bootstrap.mailbox import store
from mailmind.platform import perimeter

pytestmark = [pytest.mark.browser, pytest.mark.usefixtures("serve_the_build")]

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
    """A message that just landed in Ada's Inbox: its pk and key."""
    raw = (f"From: Mallory <mallory@evil.example.test>\r\nTo: Ada Park <ada@example.test>\r\nSubject: {subject}\r\n"
           f"Date: Sun, 01 Nov 2026 09:00:00 +0000\r\nMessage-ID: <{abs(hash(body))}@evil.example.test>\r\nMIME-Version: 1.0\r\n"
           f"Content-Type: {ctype}; charset=utf-8\r\n\r\n{body}").encode()
    uid = world.deliver(w.ADA, "INBOX", raw)
    c = store.conn()
    r = c.execute("SELECT pk, message_key FROM messages WHERE account=? AND folder='INBOX' AND uid=?", (w.ADA, uid)).fetchone()
    c.close()
    return r["pk"], r["message_key"]


def reading(page, pk):
    """The reader on message `pk`, its frame's document dressed."""
    page.goto(f"/m/{w.ADA.replace('@', '%40')}/INBOX?m={pk}")
    frame = page.locator("iframe.rd-iframe")
    expect(frame).to_be_visible()
    until(page, lambda: frame.evaluate("f => !!f.contentDocument?.head?.querySelector('style')"), "the frame was never dressed")
    return frame


def unsanitized(monkeypatch):
    monkeypatch.setattr("mailmind.content.sanitize.sanitize",
                        lambda html, load_images=False, fold=True: (html, {"blocked_images": 1, "blocked_css_urls": 0}))


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


def test_it_loads_from_its_own_url_with_its_own_policy(page, world):
    pk, key = arrives(world, "<p>Hello</p>")
    documents = []
    page.pw.on("response", lambda r: documents.append(r) if "/body.html" in r.url else None)
    frame = reading(page, pk)
    assert frame.get_attribute("src") == f"/api/message/{pk}/body.html?key={key}&images=0"
    assert frame.get_attribute("srcdoc") is None
    assert frame.get_attribute("sandbox") == "allow-same-origin"
    assert frame.evaluate("f => f.contentDocument.body.textContent") == "Hello"
    assert [d.status for d in documents] == [200]       # the perimeter took a frame's navigation for the page's own
    assert documents[0].header_value("content-security-policy").startswith("sandbox allow-same-origin; default-src 'none'; img-src data:;")
    clean(page)


def test_a_hostile_body_runs_nothing_and_reaches_nothing(page, world, monkeypatch):
    unsanitized(monkeypatch)
    pk, _ = arrives(world, HOSTILE)
    frame = reading(page, pk)
    assert frame.evaluate("f => f.contentDocument.querySelectorAll('script').length") > 0     # it did get here
    page.wait_for_timeout(300)
    nothing_got_out(page)
    assert "/body.html" in frame.evaluate("f => f.contentDocument.URL")
    only_refused(page)


def test_what_a_hostile_body_can_be_clicked_into_stops_too(page, world, monkeypatch):
    unsanitized(monkeypatch)
    pk, _ = arrives(world, HOSTILE)
    frame = reading(page, pk)
    url = frame.evaluate("f => f.contentDocument.URL")
    frame.evaluate("f => f.contentDocument.getElementById('go').click()")
    frame.evaluate("f => f.contentDocument.getElementById('js').click()")
    page.wait_for_timeout(300)
    nothing_got_out(page)
    assert frame.evaluate("f => f.contentDocument.URL") == url
    only_refused(page)


def test_with_images_asked_for_a_hostile_body_still_runs_nothing(page, world, monkeypatch):
    unsanitized(monkeypatch)
    pk, _ = arrives(world, HOSTILE)
    reading(page, pk)
    page.get_by_role("button", name="Load images").click()
    until(page, lambda: TRACKER in page.blocked, "the image was never asked for")
    frame = page.locator("iframe.rd-iframe")
    until(page, lambda: "images=1" in frame.evaluate("f => f.contentDocument?.URL ?? ''"), "the frame kept its URL")
    page.wait_for_timeout(300)
    assert page.evaluate("document.body.dataset.pwned") is None
    assert all(u.startswith("https://tracker.example.test/") for u in page.blocked), page.blocked     # images, and nothing else
    only_refused(page)


def test_the_documents_own_sandbox_holds_with_no_attribute_on_the_frame(page, world, monkeypatch):
    unsanitized(monkeypatch)
    pk, key = arrives(world, HOSTILE)
    reading(page, arrives(world, "<p>Something else</p>")[0])
    page.evaluate("""url => {
        const f = document.createElement("iframe");
        f.id = "bare";
        f.src = url;
        document.body.append(f);
    }""", f"/api/message/{pk}/body.html?key={key}&images=0")
    bare = page.locator("#bare")
    assert bare.get_attribute("sandbox") is None
    until(page, lambda: bare.evaluate("f => f.contentDocument?.getElementById('said')?.textContent") == "Hello", "it never loaded")
    page.wait_for_timeout(300)
    nothing_got_out(page)
    # The policy's default-src would stop a script alone. Only a sandbox stops a <meta> refresh, and only the header's is here.
    assert any("The document is sandboxed" in p for p in page.problems), page.problems
    only_refused(page)


def test_no_remote_image_loads_until_asked_and_then_it_does(page, world):
    pk, _ = arrives(world, f'<p>Hello</p><img src="{TRACKER}" alt="tracker">')
    documents, answers = asked(page, "/body.html"), asked(page, f"/api/message/{pk}/body?")
    referrers = []
    page.pw.on("request", lambda r: referrers.append(r.headers.get("referer")) if r.url == TRACKER else None)
    frame = reading(page, pk)
    page.wait_for_timeout(200)
    clean(page)                                     # nothing has gone to the tracker
    assert frame.evaluate("f => f.contentDocument.images[0].getAttribute('src')") is None
    expect(page.locator(".rd-imgbar")).to_contain_text("1 remote image hidden")
    expect(page.locator(".rd-extbar")).to_contain_text("1 tracker blocked")
    page.get_by_role("button", name="Load images").click()
    until(page, lambda: page.blocked == [TRACKER], "the image was never asked for")
    expect(page.locator(".rd-imgbar")).to_have_count(0)
    expect(page.locator(".rd-extbar")).to_contain_text("no active content")
    assert [u.rsplit("images=")[1] for u in documents] == ["0", "1"]
    assert answers == []                            # only the frame went back for it
    assert referrers == [None]                      # and what it asked of the sender's server names no page of this app
    page.blocked.clear()
    clean(page)


def test_the_documents_own_policy_stops_a_remote_image_the_sanitizer_let_through(page, world, monkeypatch):
    unsanitized(monkeypatch)
    pk, _ = arrives(world, f'<img src="{TRACKER}" alt="tracker">')
    frame = reading(page, pk)
    assert frame.evaluate("f => f.contentDocument.images.length") == 1
    page.wait_for_timeout(200)
    assert page.blocked == []
    only_refused(page)


def test_load_images_adds_nothing_to_the_tabs_history(page, world):
    pk, _ = arrives(world, f'<img src="{TRACKER}" alt="tracker">')
    frame = reading(page, pk)
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
    pk, _ = arrives(world, '<p><a href="https://ok.example.test/page">Read it</a></p>')
    frame = reading(page, pk)
    url = frame.evaluate("f => f.contentDocument.URL")
    catching_opens(page)
    at = link_at(frame, "a")
    page.mouse.click(at["x"], at["y"], button=button)
    until(page, lambda: page.evaluate("window.opens.length") == 1, "the click was never taken")
    assert page.evaluate("window.opens") == [["https://ok.example.test/page", "_blank", "noopener,noreferrer"]]
    assert frame.evaluate("f => f.contentDocument.URL") == url
    clean(page)


def test_a_link_that_says_one_place_and_goes_to_another_asks_first(page, world):
    pk, _ = arrives(world, '<p><a href="https://evil.example.test/">https://bank.example.test/</a></p>')
    frame = reading(page, pk)
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
    pk, _ = arrives(world, f'<p><a href="https://ok.example.test/page">Read it</a></p><img src="{TRACKER}" alt="tracker">')
    frame = reading(page, pk)
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
    pk, _ = arrives(world, "Plain words.", "text/plain")
    documents, answers = asked(page, "/body.html"), asked(page, f"/api/message/{pk}/body")
    frame = reading(page, pk)
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


def test_a_reading_setting_darkens_rich_mail_without_fetching_it(page, world):
    pk, _ = arrives(world, "<p>Rich words.</p>")
    documents = asked(page, "/body.html")
    frame = reading(page, pk)
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
    pk, _ = arrives(world, "\r\n".join(f"Line {n}" for n in range(60)), "text/plain")
    frame = reading(page, pk)
    until(page, lambda: frame.evaluate("f => parseInt(f.style.height) > 0"), "the frame was never sized")
    small = frame.evaluate("f => parseInt(f.style.height)")
    page.get_by_role("button", name="Reading settings").click()
    page.get_by_role("dialog", name="Reading settings").get_by_role("button", name="Extra large").click()
    until(page, lambda: frame.evaluate("f => parseInt(f.style.height)") > small, "the frame kept its height")
    clean(page)


def test_a_mail_as_tall_as_its_frame_doesnt_keep_growing_it(page, world, monkeypatch):
    unsanitized(monkeypatch)
    pk, _ = arrives(world, "<style>html,body{height:100%}</style><p>Hello</p>")
    frame = reading(page, pk)
    until(page, lambda: frame.evaluate("f => f.contentDocument.readyState") == "complete", "it never loaded")
    page.wait_for_timeout(200)
    settled = frame.evaluate("f => parseInt(f.style.height)")
    page.wait_for_timeout(500)
    assert frame.evaluate("f => parseInt(f.style.height)") == settled < 2000
    clean(page)


def test_a_narrower_column_fits_the_frame_again(page, world):
    pk, _ = arrives(world, "Words and more words. " * 200, "text/plain")
    frame = reading(page, pk)
    until(page, lambda: frame.evaluate("f => parseInt(f.style.height) > 0"), "the frame was never sized")
    wide = frame.evaluate("f => parseInt(f.style.height)")
    page.get_by_role("button", name="Reading settings").click()
    page.get_by_role("dialog", name="Reading settings").get_by_role("button", name="Narrow").click()
    until(page, lambda: frame.evaluate("f => parseInt(f.style.height)") > wide, "the frame kept its height")
    clean(page)


def test_the_new_frame_images_load_into_starts_at_the_olds_height(page, world):
    tall = "".join(f"<p>Line {n}</p>" for n in range(40))
    pk, _ = arrives(world, f'{tall}<img src="{TRACKER}" alt="tracker">')
    frame = reading(page, pk)
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
    pk, _ = arrives(world, "Yes, Thursday works.\r\n\r\nOn Monday Ada wrote:\r\n> Are you free <then>?\r\n", "text/plain")
    frame = reading(page, pk)
    until(page, lambda: frame.evaluate("f => !!f.contentDocument.querySelector('details.mm-quote')"), "it never folded")
    assert frame.evaluate("f => f.contentDocument.querySelector('pre').textContent") == "Yes, Thursday works.\n"
    assert frame.evaluate("f => f.contentDocument.querySelector('details pre').textContent") == "On Monday Ada wrote:\n> Are you free <then>?\n"
    assert frame.evaluate("f => f.contentDocument.querySelector('details').open") is False
    assert frame.evaluate("f => f.contentDocument.querySelector('details pre').children.length") == 0
    clean(page)


def test_a_message_gone_by_the_time_its_frame_asks_says_so_in_the_frame(page, world):
    pk, _ = arrives(world, f'<p>Hello</p><img src="{TRACKER}" alt="tracker">')
    frame = reading(page, pk)
    c = store.conn()
    store.delete_message(c, pk)
    c.commit()
    c.close()
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
    pk, _ = arrives(world, f'<img src="{TRACKER}" alt="tracker">')
    reading(page, pk)
    page.get_by_role("button", name="Load images").click()
    until(page, lambda: page.blocked == [TRACKER], "the frame's own policy didn't let it through")
    page.blocked.clear()
    clean(page)


def test_a_conversation_gives_each_message_its_own_frame(page, world):
    page.goto(f"/m/{w.ADA.replace('@', '%40')}/INBOX")
    page.get_by_role("tab", name=re.compile("^Everything")).click()
    page.locator("#rows .row[data-pk]").filter(has=page.locator(".thr-count")).filter(has_text="unch").first.click()
    expect(page.locator("#thread .tmsg")).to_have_count(3)
    page.locator("#thread .tmsg-hd").first.click()
    frames = page.locator("iframe.rd-iframe")
    expect(frames).to_have_count(2)
    sources = [frames.nth(i).get_attribute("src") for i in range(2)]
    assert sources[0] != sources[1] and all("/body.html?key=" in s for s in sources)
    until(page, lambda: all(frames.nth(i).evaluate("f => !!f.contentDocument?.head?.querySelector('style')") for i in range(2)),
          "a frame was never dressed")
    clean(page)
