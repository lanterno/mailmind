"""The mail frame's walls on mail the sanitizer let everything through: the sandbox and the policy hold alone. Only the app
in this process can be made to serve a message unsanitized, so these are here and not in ../test_mail_frame.py, which
holds the rest and the helpers these use.
"""
import pytest

from tests.e2e.browser.test_mail_frame import (HOSTILE, TRACKER, arrives, clean, drawn_height, loaded, nothing_got_out,
                                               only_refused, reading, until)

pytestmark = pytest.mark.browser


@pytest.fixture(autouse=True)
def unsanitized(monkeypatch):
    """As if the sanitizer had missed everything, so what would get past the walls reaches them."""
    monkeypatch.setattr("mailmind.content.sanitize.sanitize",
                        lambda html, load_images=False, fold=True: (html, {"blocked_images": 1, "blocked_css_urls": 0}))


def test_a_hostile_body_runs_nothing_and_reaches_nothing(page, world):
    pk, key = arrives(world, HOSTILE)
    frame = reading(page, key)
    assert frame.evaluate("f => f.contentDocument.querySelectorAll('script').length") > 0     # it did get here
    loaded(page, frame)
    nothing_got_out(page)
    assert "/body.html" in frame.evaluate("f => f.contentDocument.URL")
    only_refused(page)


def test_what_a_hostile_body_can_be_clicked_into_stops_too(page, world):
    pk, key = arrives(world, HOSTILE)
    frame = reading(page, key)
    url = frame.evaluate("f => f.contentDocument.URL")
    frame.evaluate("f => f.contentDocument.getElementById('go').click()")
    frame.evaluate("f => f.contentDocument.getElementById('js').click()")
    page.wait_for_timeout(300)      # a javascript: link would run in a task after the click; nothing says it was refused
    nothing_got_out(page)
    assert frame.evaluate("f => f.contentDocument.URL") == url
    only_refused(page)


def test_with_images_asked_for_a_hostile_body_still_runs_nothing(page, world):
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


def test_the_documents_own_sandbox_holds_with_no_attribute_on_the_frame(page, world):
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


def test_the_documents_own_policy_stops_a_remote_image_the_sanitizer_let_through(page, world):
    pk, key = arrives(world, f'<img src="{TRACKER}" alt="tracker">')
    frame = reading(page, key)
    assert frame.evaluate("f => f.contentDocument.images.length") == 1
    loaded(page, frame)
    assert page.blocked == []
    only_refused(page)


def test_a_mail_as_tall_as_its_frame_doesnt_keep_growing_it(page, world):
    pk, key = arrives(world, "<style>html,body{height:100%}</style><p>Hello</p>")
    frame = reading(page, key)
    loaded(page, frame)
    page.wait_for_timeout(200)      # the page fits it again on load, after it has drawn what came before
    settled = frame.evaluate("f => parseInt(f.style.height)")
    page.wait_for_timeout(500)      # what this asks is that time passing grows it no more
    assert frame.evaluate("f => parseInt(f.style.height)") == settled < 2000
    clean(page)


def test_a_mail_as_tall_as_its_frame_keeps_its_height_however_often_it_is_fitted_again(page, world):
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
