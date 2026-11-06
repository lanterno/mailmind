"""Regression gate for the HTML-email sanitizer (mailmind.content.sanitize).

The authoritative invariant: parse the sanitizer's OUTPUT the way a browser would (entities
decoded) and assert NO real dangerous tag, event-handler attribute, dangerous URL scheme, SVG-data
image, or remote image (when blocked) survives. Strings inside escaped attribute values are inert
and don't count."""
import html as htmlmod
import re
from html.parser import HTMLParser

import pytest

from mailmind.content import sanitize

DANGEROUS_TAGS = {"script", "iframe", "object", "embed", "form", "svg", "math",
                  "base", "meta", "link", "frame", "frameset", "input", "style",
                  "applet", "noscript", "template", "title", "head", "button",
                  "select", "textarea"}
BAD_SCHEME = re.compile(r"^\s*(javascript|vbscript|livescript|mocha|data\s*:\s*text/html)", re.I)


class _Inspect(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.bad = []

    def _scan(self, t, attrs):
        t = t.lower()
        if t in DANGEROUS_TAGS:
            self.bad.append(("tag", t))
        for n, v in attrs:
            n = (n or "").lower()
            if n.startswith("on"):
                self.bad.append(("handler", t, n))
            if n in ("href", "src", "srcset", "xlink:href", "formaction") and v and BAD_SCHEME.match(htmlmod.unescape(v)):
                self.bad.append(("url", t, n))
            if n == "src" and t == "img" and v and re.match(r"\s*data:image/svg", htmlmod.unescape(v), re.I):
                self.bad.append(("svg-data", t))

    def handle_starttag(self, t, a):
        self._scan(t, a)

    def handle_startendtag(self, t, a):
        self._scan(t, a)


def real_leaks(out, load_images):
    insp = _Inspect()
    insp.feed(out)
    insp.close()
    bad = list(insp.bad)
    if not load_images and re.search(r"<img[^>]+src\s*=\s*[\"']?https?:", out, re.I):
        bad.append(("remote-img-when-blocked",))
    return bad


# Representative attack vectors across the classes the red-team exercised: 24 of them.
ATTACKS = {
    "script_tag": "<script>alert(1)</script>x",
    "onerror": "<img src=x onerror=alert(1)>",
    "onerror_upper": "<img src=x ONERROR=alert(1)>",
    "onerror_whitespace": "<img\tsrc=x\nonerror=alert(1)>",
    "svg_onload": "<svg/onload=alert(1)>",
    "svg_script": "<svg><script>alert(1)</script></svg>",
    "href_javascript": "<a href='javascript:alert(1)'>c</a>",
    "href_javascript_tab": "<a href='java\tscript:alert(1)'>c</a>",
    "href_javascript_charref": "<a href='&#106;avascript:alert(1)'>c</a>",
    "href_vbscript": "<a href='vbscript:x'>c</a>",
    "href_data_html": "<a href='data:text/html,<script>alert(1)</script>'>c</a>",
    "href_protocol_relative": "<a href='//evil/track'>c</a>",
    "iframe": "<iframe src='x'></iframe>",
    "object": "<object data='x'></object>",
    "form": "<form action='/x'><input></form>",
    "base": "<base href='http://evil/'>b",
    "meta_refresh": "<meta http-equiv=refresh content='0;url=evil'>b",
    "style_block": "<style>body{x:url(http://track)}</style>v",
    "style_attr_javascript": "<p style='background:url(javascript:alert(1))'>t</p>",
    "td_background": "<td background='http://evil/o.gif'>x</td>",
    "font_color_expression": "<font color='expression(alert(1))'>x</font>",
    "img_svg_data": "<img src='data:image/svg+xml;base64,PHN2Zz4='>",
    "title_breakout": "<div title='\"></div><img src=x onerror=alert(1)>'>hi</div>",
    "attr_breakout": "<a href='http://ok' title='x\" onmouseover=\"alert(1)'>y</a>",
}


def _attacks():
    """Each attack, and its entity-decoded form where that differs."""
    for name, attack in ATTACKS.items():
        yield pytest.param(attack, id=name)
        decoded = htmlmod.unescape(attack)
        if decoded != attack:
            yield pytest.param(decoded, id=f"{name}_decoded")


def test_the_attack_classes_are_all_here():
    assert len(ATTACKS) == 24


@pytest.mark.parametrize("load_images", [False, True], ids=["images_blocked", "images_loaded"])
@pytest.mark.parametrize("attack", list(_attacks()))
def test_nothing_dangerous_survives(attack, load_images):
    out, _ = sanitize.sanitize(attack, load_images=load_images)
    assert real_leaks(out, load_images) == [], out


# Safe content that MUST be preserved: (what goes in, what must come out).
KEEP = {
    "link": ("<a href='https://example.com'>link</a>", ['href="https://example.com"', "link", 'target="_blank"']),
    "inline": ("<p>Hi <b>bold</b> <i>it</i></p>", ["<p>", "<b>bold</b>", "<i>it</i>"]),
    "list": ("<ul><li>a</li></ul>", ["<ul>", "<li>a</li>"]),
    "table": ("<table><tr><td colspan='2'>c</td></tr></table>", ['colspan="2"', "c"]),
}


@pytest.mark.parametrize("src, fragment", [pytest.param(src, f, id=f"{name}-{f}")
                                           for name, (src, must) in KEEP.items() for f in must])
def test_safe_content_is_kept(src, fragment):
    out, _ = sanitize.sanitize(src)
    assert fragment in out


def test_a_raster_data_image_is_kept_when_images_load():
    out, _ = sanitize.sanitize("<img src='data:image/png;base64,iVBOR' alt='ok'>", load_images=True)
    assert "data:image/png" in out


@pytest.mark.parametrize("junk", ["<p><b>unclosed", "<<<>>>", None, "", "<a href=", "</x></y>"])
def test_junk_does_not_raise(junk):
    out, _ = sanitize.sanitize(junk)
    assert isinstance(out, str)
