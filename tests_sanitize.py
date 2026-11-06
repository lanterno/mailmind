"""Regression gate for the HTML-email sanitizer (mailmind.content.sanitize).

The authoritative invariant: parse the sanitizer's OUTPUT the way a browser
would (entities decoded) and assert NO real dangerous tag, event-handler
attribute, dangerous URL scheme, SVG-data image, or remote image (when blocked)
survives. Strings inside escaped attribute values are inert and don't count.

Run:  python3 tests_sanitize.py      (exits non-zero on any leak)
"""
import re
import sys
import html as htmlmod
from html.parser import HTMLParser

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


# Representative attack vectors across the classes the red-team exercised.
ATTACKS = [
    "<script>alert(1)</script>x",
    "<img src=x onerror=alert(1)>",
    "<img src=x ONERROR=alert(1)>",
    "<img\tsrc=x\nonerror=alert(1)>",
    "<svg/onload=alert(1)>",
    "<svg><script>alert(1)</script></svg>",
    "<a href='javascript:alert(1)'>c</a>",
    "<a href='java\tscript:alert(1)'>c</a>",
    "<a href='&#106;avascript:alert(1)'>c</a>",
    "<a href='vbscript:x'>c</a>",
    "<a href='data:text/html,<script>alert(1)</script>'>c</a>",
    "<a href='//evil/track'>c</a>",
    "<iframe src='x'></iframe>",
    "<object data='x'></object>",
    "<form action='/x'><input></form>",
    "<base href='http://evil/'>b",
    "<meta http-equiv=refresh content='0;url=evil'>b",
    "<style>body{x:url(http://track)}</style>v",
    "<p style='background:url(javascript:alert(1))'>t</p>",
    "<td background='http://evil/o.gif'>x</td>",
    "<font color='expression(alert(1))'>x</font>",
    "<img src='data:image/svg+xml;base64,PHN2Zz4='>",
    "<div title='\"></div><img src=x onerror=alert(1)>'>hi</div>",
    "<a href='http://ok' title='x\" onmouseover=\"alert(1)'>y</a>",
]
# Safe content that MUST be preserved.
KEEP = [
    ("<a href='https://example.com'>link</a>", ['href="https://example.com"', "link", 'target="_blank"']),
    ("<p>Hi <b>bold</b> <i>it</i></p>", ["<p>", "<b>bold</b>", "<i>it</i>"]),
    ("<ul><li>a</li></ul>", ["<ul>", "<li>a</li>"]),
    ("<table><tr><td colspan='2'>c</td></tr></table>", ['colspan="2"', "c"]),
    ("<img src='data:image/png;base64,iVBOR' alt='ok'>", None),  # checked with load_images=True below
]


def run():
    leaks = 0
    for atk in ATTACKS:
        for li in (False, True):
            for variant in {atk, htmlmod.unescape(atk)}:
                out, _ = sanitize.sanitize(variant, load_images=li)
                bad = real_leaks(out, li)
                if bad:
                    leaks += 1
                    print(f"  LEAK {bad}\n    in : {variant[:70]}\n    out: {out[:90]}")
    # preservation
    keep_fail = 0
    for src, must in KEEP[:-1]:
        out, _ = sanitize.sanitize(src)
        for m in must:
            if m not in out:
                keep_fail += 1
                print(f"  DROPPED safe content: {m!r} from {src}")
    out, _ = sanitize.sanitize(KEEP[-1][0], load_images=True)
    if "data:image/png" not in out:
        keep_fail += 1
        print("  raster data-URI image not preserved with load_images=True")
    # robustness
    for junk in ["<p><b>unclosed", "<<<>>>", None, "", "<a href=", "</x></y>"]:
        sanitize.sanitize(junk)  # must not raise
    print(f"\nsanitizer: {len(ATTACKS)} attack classes, 0 leaks expected -> {leaks} leaks; "
          f"{keep_fail} preservation failures")
    return leaks == 0 and keep_fail == 0


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
