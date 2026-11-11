"""No script runs inside a mail frame, whatever a message holds. The frame's sandbox is the wall and the sanitizer is defence
in depth, so each case runs twice, the second time with the sanitizer gone, as if it had missed everything. Each thing
tried here would add its name to the top window's list if it ran, and the frame may ask nobody for anything.

Chromium only, as all of tests/e2e/browser is.
"""
import re

import pytest
from playwright.sync_api import expect

from fixtures import world as w
from mailmind.bootstrap.mailbox import store
from mailmind.content import sanitize

pytestmark = pytest.mark.browser

FRAME = "iframe.rd-iframe"
QUOTE = 'iframe[title="The message you’re answering"]'
AWAY = "https://away.example.test"


def fire(name):
    return f"top.__ran=(top.__ran||[]).concat('{name}')"


HOSTILE = f"""<!doctype html><html><head>
<script>{fire("head script")}</script>
<script src="/assets/theme.js"></script>
<meta http-equiv="refresh" content="0;url=javascript:{fire("refresh")}">
<meta http-equiv="refresh" content="1;url={AWAY}/refresh">
<link rel="stylesheet" href="{AWAY}/sheet.css">
<style>@import url({AWAY}/import.css); body {{ background: url({AWAY}/back.png) }}</style>
</head><body onload="{fire("body onload")}">
<p>A message with everything in it</p>
<script>{fire("body script")}</script>
<img src="data:," onerror="{fire("img onerror")}">
<img src="{AWAY}/pixel.png"><img src="/api/folders">
<svg onload="{fire("svg onload")}"><script>{fire("svg script")}</script></svg>
<details open ontoggle="{fire("ontoggle")}">more</details>
<input autofocus onfocus="{fire("onfocus")}">
<video src="data:," onerror="{fire("video onerror")}"></video>
<math><mtext><table><mglyph><style><img src="data:," onerror="{fire("mxss")}"></style></mglyph></table></mtext></math>
<iframe srcdoc="<script>{fire("nested frame")}</script>"></iframe>
<object data="javascript:{fire("object")}"></object>
<embed src="javascript:{fire("embed")}">
<a href="javascript:{fire("link")}">a link</a>
<form action="javascript:{fire("form")}"><button>go</button></form>
</body></html>"""


def message(body, kind="html", mid="<hostile@example.test>"):
    name = f'"x\\"><img src=data:, onerror={fire("from name")}>" <bad@example.test>'
    return (f"From: {name}\r\nTo: {w.ADA}\r\nSubject: \"><img src=data:, onerror={fire('subject')}>\r\nMessage-ID: {mid}\r\n"
            f"Date: Mon, 02 Nov 2026 11:00:00 +0000\r\nMIME-Version: 1.0\r\nContent-Type: text/{kind}; charset=utf-8\r\n\r\n{body}").encode()


def delivered(world, raw, mid="<hostile@example.test>"):
    """The key and uid of the copy that just landed in Ada's Inbox."""
    uid = world.deliver(w.ADA, "INBOX", raw)
    c = store.conn()
    key = c.execute("SELECT message_key FROM messages WHERE message_id=?", (mid,)).fetchone()["message_key"]
    c.close()
    return key, uid


OWN_DOCUMENT = re.compile(r"/api/message/body\.html\?key=\d+&folder=INBOX&uid=\d+&images=0$")


def opening(page, copy):
    key, uid = copy
    page.goto(f"/m/{w.ADA.replace('@', '%40')}/INBOX?key={key}&folder=INBOX&uid={uid}")


@pytest.fixture(params=["sanitized", "no sanitizer"])
def wall(request, monkeypatch):
    if request.param == "no sanitizer":
        monkeypatch.setattr(sanitize, "sanitize", lambda html, load_images=False: (html, {"blocked_images": 0, "blocked_css_urls": 0}))
    return request.param


@pytest.fixture
def asked_by_frames(page):
    """What any frame but the page itself got an answer to. Chromium reports a request its policy refused too, as one that failed."""
    asked = []

    def note(req):
        own_document = req.is_navigation_request() and OWN_DOCUMENT.search(req.url)    # the frame loads its body
        if req.frame.parent_frame is not None and req.url.startswith("http") and not own_document:
            asked.append(req.url)
    page.pw.on("requestfinished", note)
    return asked


def until(page, check, what):
    for _ in range(100):
        if check():
            return
        page.pw.wait_for_timeout(50)
    assert check(), what


# What Chromium says as it reads a <meta> refresh it won't follow: one to a javascript: URL, or any in a sandbox.
REFRESH_REFUSED = re.compile(r"Refused to (refresh .* to a javascript: URL|execute the redirect specified via '<meta)")


def refreshes_refused(page, *frames):
    """Waits until each <meta> refresh the frames hold was refused, as each was read: none is left to go off."""
    n = sum(frame.locator('meta[http-equiv="refresh" i]').count() for frame in frames)
    until(page, lambda: sum(bool(REFRESH_REFUSED.search(p)) for p in page.problems) >= n, "a refresh wasn't refused")


def click_if_there(where):
    """The sanitizer takes some of these out."""
    if where.count():
        where.click(timeout=3000)


def test_nothing_in_a_message_runs_or_asks_for_anything(page, world, wall, asked_by_frames):
    opening(page, delivered(world, message(HOSTILE)))
    frame = page.frame_locator(FRAME).first
    expect(frame.get_by_text("A message with everything in it")).to_be_visible()
    expect(page.locator(FRAME).first).to_have_attribute("sandbox", "allow-same-origin")
    if wall == "no sanitizer":      # or this says nothing: it all has to have reached the frame
        assert frame.locator("script").count() >= 2 and frame.locator("[onerror]").count() >= 2
    click_if_there(frame.get_by_text("a link"))
    click_if_there(frame.get_by_role("button", name="go"))
    refreshes_refused(page, frame)
    page.pw.wait_for_timeout(300)            # a javascript: link would run in a task after the click; nothing says it was refused
    assert page.pw.evaluate("window.__ran || []") == []
    # The frame's policy lets an https image through, for someone who asks to see them, so holding those back is the sanitizer's.
    only = {f"{AWAY}/pixel.png", f"{AWAY}/back.png"} if wall == "no sanitizer" else set()
    assert set(asked_by_frames) <= only
    assert set(page.blocked) <= only
    where = page.pw.evaluate(f"document.querySelector('{FRAME}').contentWindow.location.href")
    assert OWN_DOCUMENT.search(where)
    assert [p for p in page.problems if p.startswith("pageerror")] == []


def test_a_name_or_subject_written_as_html_stays_text_on_the_page(page, world):
    """They go into the frame's title and the reader's head, on the page itself, where a script is the page's."""
    opening(page, delivered(world, message("<p>Hello</p>")))
    expect(page.frame_locator(FRAME).first.get_by_text("Hello")).to_be_visible()
    expect(page.locator(FRAME).first).to_have_attribute("title", re.compile("onerror"))
    assert page.pw.evaluate("document.querySelectorAll('[onerror]').length") == 0
    assert page.pw.evaluate("window.__ran || []") == []


def test_plain_text_written_as_html_stays_text(page, world, wall):
    written = f"<script>{fire('text script')}</script><img src=data:, onerror={fire('text img')}>"
    opening(page, delivered(world, message(written, kind="plain")))
    frame = page.frame_locator(FRAME).first
    expect(frame.locator("body")).to_contain_text("<script>")
    assert frame.locator("body script, body img").count() == 0
    assert page.pw.evaluate("window.__ran || []") == []


def quoted_in_a_reply(page, world, body):
    """A reply's quote: a srcdoc, so it answers to the page's policy as well as its own sandbox."""
    opening(page, delivered(world, message(body)))
    expect(page.locator(".rd-subj")).to_be_visible()
    page.keyboard.press("r")
    page.locator("#cmpQToggle").click()
    expect(page.locator(QUOTE)).to_have_attribute("sandbox", "")
    return page.frame_locator(QUOTE)


def test_nothing_in_a_message_runs_in_the_quote_of_a_reply(page, world, wall):
    frame = quoted_in_a_reply(page, world, HOSTILE)
    expect(frame.get_by_text("A message with everything in it")).to_be_visible()
    refreshes_refused(page, page.frame_locator(FRAME).first, frame)     # the reader's frame says so of its own too
    assert page.pw.evaluate("window.__ran || []") == []
    assert [p for p in page.problems if p.startswith("pageerror")] == []


def test_the_quote_of_a_reply_breaks_no_policy(page, world):
    """Its styles are the page's policy's to allow, and the fake clock's script is its sandbox's to refuse (SANDBOX_REFUSAL)."""
    frame = quoted_in_a_reply(page, world, "<p>Hello</p>")
    expect(frame.get_by_text("Hello")).to_be_visible()
    assert page.problems == [], page.problems
