"""What the HTML sanitizer (mailmind.content.sanitize.html) makes of a self-closing tag and of a comment. A self-closed tag
is held to the rules its open tag is, and closed at once, so it can neither carry a handler through nor swallow what
follows it; a comment, conditional ones included, goes whole, with whatever it hides."""
import pytest

from mailmind.content.sanitize import sanitize

NOTHING_BLOCKED = {"blocked_images": 0, "blocked_links": 0, "blocked_css_urls": 0}


@pytest.mark.parametrize("html, out", [
    ("<br/>x", "<br/>x"),
    ("<div/>after", "<div></div>after"),
    ("<b/>x", "<b></b>x"),
    ("<SPAN/>x", "<span></span>x"),
    ("<blink/>x", "x"),
], ids=repr)
def test_a_tag_that_closes_itself_is_closed_at_once(html, out):
    assert sanitize(html) == (out, NOTHING_BLOCKED)


@pytest.mark.parametrize("html, out", [
    ("<script/>alert(1)", "alert(1)"),
    ("<SCRIPT/>alert(1)", "alert(1)"),
    ("<iframe src='https://e.example.test/' />after", "after"),
    ("<svg/>after", "after"),
    ("<style/>p{}", "p{}"),
    ("<title/>after", "after"),
    ("<svg><svg/>inner</svg>after", "after"),
], ids=repr)
def test_a_tag_dropped_with_its_content_drops_none_that_follows_when_it_closes_itself(html, out):
    assert sanitize(html) == (out, NOTHING_BLOCKED)


def test_a_self_closed_image_loses_its_handler_and_its_remote_source_while_images_are_blocked():
    html = "<img src='https://e.example.test/a.png' onerror=alert(1) />"
    assert sanitize(html) == ("<img/>", {**NOTHING_BLOCKED, "blocked_images": 1})
    assert sanitize(html, load_images=True) == ('<img src="https://e.example.test/a.png"/>', NOTHING_BLOCKED)


def test_a_self_closed_link_is_held_to_the_rules_an_open_one_is():
    assert sanitize("<a href='javascript:alert(1)'/>x") == ("<a></a>x", {**NOTHING_BLOCKED, "blocked_links": 1})
    assert sanitize("<a href='https://example.test/'/>x") == \
        ('<a href="https://example.test/" rel="noopener noreferrer nofollow" target="_blank"></a>x', NOTHING_BLOCKED)


@pytest.mark.parametrize("html, out", [
    ("<p>a<!-- hidden -->b</p>", "<p>ab</p>"),
    ("<!--<script>alert(1)</script>-->x", "x"),
    ("<!--[if mso]><img src='https://e.example.test/t.gif'><![endif]-->x", "x"),
    ("<p>hi</p><!-- <blockquote>quoted</blockquote> -->", "<p>hi</p>"),
    ("<!-->x", "x"),
], ids=repr)
def test_a_comment_goes_with_all_it_holds(html, out):
    assert sanitize(html) == (out, NOTHING_BLOCKED)


def test_a_comment_opened_in_an_attribute_stays_text():
    assert sanitize('<p title="<!--">x</p>-->') == ('<p title="&lt;!--">x</p>--&gt;', NOTHING_BLOCKED)
