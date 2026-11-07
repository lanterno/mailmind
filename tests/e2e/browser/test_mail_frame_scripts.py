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
AWAY = "https://away.example.test"


def fire(name):
    return f"top.__ran=(top.__ran||[]).concat('{name}')"


HOSTILE = f"""<!doctype html><html><head>
<script>{fire("head script")}</script>
<script src="/assets/legacy/theme.js"></script>
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
    world.deliver(w.ADA, "INBOX", raw)
    c = store.conn()
    pk = c.execute("SELECT pk FROM messages WHERE message_id=?", (mid,)).fetchone()["pk"]
    c.close()
    return pk


OWN_DOCUMENT = re.compile(r"/api/message/\d+/body\.html\?key=\d+&images=0$")


def on_the_page(page, pk):
    page.goto("/")
    page.get_by_role("tab", name=re.compile("^Everything")).click()
    page.locator(f'#rows .row[data-pk="{pk}"]').click()


def on_the_react_app(page, pk):
    page.goto(f"/m/{w.ADA.replace('@', '%40')}/INBOX?m={pk}")


@pytest.fixture(params=["page", "react"])
def opening(request):
    if request.param == "react":
        request.getfixturevalue("serve_the_build")
        return on_the_react_app
    return on_the_page


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
        own_document = req.is_navigation_request() and OWN_DOCUMENT.search(req.url)    # the React app's frame loads its body
        if req.frame.parent_frame is not None and req.url.startswith("http") and not own_document:
            asked.append(req.url)
    page.pw.on("requestfinished", note)
    return asked


def click_if_there(where):
    """The sanitizer takes some of these out."""
    if where.count():
        where.click(timeout=3000)


def test_nothing_in_a_message_runs_or_asks_for_anything(page, world, opening, wall, asked_by_frames):
    opening(page, delivered(world, message(HOSTILE)))
    frame = page.frame_locator(FRAME).first
    expect(frame.get_by_text("A message with everything in it")).to_be_visible()
    expect(page.locator(FRAME).first).to_have_attribute("sandbox", "allow-same-origin")
    if wall == "no sanitizer":      # or this says nothing: it all has to have reached the frame
        assert frame.locator("script").count() >= 2 and frame.locator("[onerror]").count() >= 2
    click_if_there(frame.get_by_text("a link"))
    click_if_there(frame.get_by_role("button", name="go"))
    page.pw.wait_for_timeout(1500)           # past the second meta refresh
    assert page.pw.evaluate("window.__ran || []") == []
    # The frame's policy lets an https image through, for someone who asks to see them, so holding those back is the sanitizer's.
    only = {f"{AWAY}/pixel.png", f"{AWAY}/back.png"} if wall == "no sanitizer" else set()
    assert set(asked_by_frames) <= only
    assert set(page.blocked) <= only
    where = page.pw.evaluate(f"document.querySelector('{FRAME}').contentWindow.location.href")
    assert OWN_DOCUMENT.search(where) if opening is on_the_react_app else where == "about:srcdoc"
    assert [p for p in page.problems if p.startswith("pageerror")] == []


def test_a_name_or_subject_written_as_html_stays_text_on_the_page(page, world, opening):
    """They go into the frame's title and the reader's head, on the page itself, where a script is the page's."""
    opening(page, delivered(world, message("<p>Hello</p>")))
    expect(page.frame_locator(FRAME).first.get_by_text("Hello")).to_be_visible()
    expect(page.locator(FRAME).first).to_have_attribute("title", re.compile("onerror"))
    assert page.pw.evaluate("document.querySelectorAll('[onerror]').length") == 0
    assert page.pw.evaluate("window.__ran || []") == []


def test_plain_text_written_as_html_stays_text(page, world, opening, wall):
    written = f"<script>{fire('text script')}</script><img src=data:, onerror={fire('text img')}>"
    opening(page, delivered(world, message(written, kind="plain")))
    frame = page.frame_locator(FRAME).first
    expect(frame.locator("body")).to_contain_text("<script>")
    assert frame.locator("body script, body img").count() == 0
    assert page.pw.evaluate("window.__ran || []") == []
