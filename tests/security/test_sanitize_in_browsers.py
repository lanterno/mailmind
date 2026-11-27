"""The sanitizer against browsers' own parsers (ROADMAP #37; test-strategy.md section 6, "The sanitizer removes
everything active"). test_sanitize.py reads the sanitizer's output with Python's HTMLParser, which isn't how a browser
reads it: mutation XSS lives in the difference (markup a browser's tree builder moves, closes or reopens, so that
what was text or an attribute becomes an element). Here Chromium, and Firefox where it is installed, parse every
output with their own `DOMParser`, then serialize what they built and parse that again, as `innerHTML` does when a page
copies markup, and nothing active may be in either tree: no script, frame, form, plugin, style sheet or base, no SVG or
MathML, no handler, no URL that runs, and with images blocked, nothing that loads from a server.

The inputs are test_sanitize.py's attacks, the mutation-XSS classics, and a few thousand drawn from a fixed seed out of
the pieces those are made of, so a failure is the same on every run and prints its input. The pure part, that the
sanitizer's output is its own sanitizing (a fixed point, so sanitizing again moves nothing), runs in `just test`; the
browsers run with `--browser`.
"""
import os
import random

import pytest

from mailmind.content import sanitize
from security.test_sanitize import ATTACKS

MUTATIONS = [
    "<svg><style><img src=x onerror=alert(1)></style></svg>",
    "<math><mtext><table><mglyph><style><img src=x onerror=alert(1)>",
    "<math><mi><table><mi><svg><style><img src=x onerror=alert(1)>",
    '<noscript><p title="</noscript><img src=x onerror=alert(1)>">',
    "<form><math><mtext></form><form><mglyph><style></math><img src onerror=alert(1)>",
    '<svg></p><style><a id="</style><img src=1 onerror=alert(1)>">',
    "<!--><img src=x onerror=alert(1)>-->",
    "<![CDATA[><img src=x onerror=alert(1)>]]>",
    "<xmp><img src=x onerror=alert(1)></xmp>",
    "<textarea><img src=x onerror=alert(1)></textarea>",
    "<template><img src=x onerror=alert(1)></template>",
    "<plaintext><img src=x onerror=alert(1)>",
    "<select><style></select><img src=x onerror=alert(1)>",
    '<a href="  javascript:alert(1)">a</a>',
    '<a href="jav&#x09;ascript:alert(1)">a</a>',
    '<a href="&#x6A;&#x61;vascript:alert(1)">a</a>',
    '<a href="javascript&colon;alert(1)">a</a>',
    '<img srcset="x.png 1x, javascript:alert(1) 2x">',
    '<object data="javascript:alert(1)"></object>',
    '<embed src="javascript:alert(1)">',
    "<isindex action=javascript:alert(1) type=image>",
    "<details open ontoggle=alert(1)>",
    "<video><source onerror=alert(1)></video>",
    "<body onload=alert(1)>",
    "<html onmouseover=alert(1)>",
    '<iframe srcdoc="<script>alert(1)</script>"></iframe>',
    '<svg><foreignObject><img src=x onerror=alert(1)></foreignObject></svg>',
    '<math><annotation-xml encoding="text/html"><img src=x onerror=alert(1)></annotation-xml></math>',
    "<img src=https://tracker.example.test/p.gif>",
    "<p style=\"background:url('https://tracker.example.test/p.gif')\">t</p>",
    "<table background=https://tracker.example.test/p.gif><tr><td>t</td></tr></table>",
]

PIECES = [
    "<svg>", "</svg>", "<math>", "</math>", "<mtext>", "<mglyph>", "<mi>", "<style>", "</style>", "<table>", "</table>",
    "<select>", "</select>", "<noscript>", "</noscript>", "<template>", "</template>", "<form>", "</form>", '<p title="',
    '">', "<!--", "-->", "<![CDATA[", "]]>", "<img src=x onerror=alert(1)>", "<a href=javascript:alert(1)>a</a>", "</p>",
    "<xmp>", "</xmp>", "<textarea>", "</textarea>", "<foreignObject>", "<desc>", '<annotation-xml encoding="text/html">',
    "<div>", "</div>", "<b>", "text", "&lt;img src=x onerror=alert(1)&gt;", "\x00", "<iframe>", "<script>",
    "</script>", "<a href='https://example.test/'>link</a>", "<img src=https://tracker.example.test/p.gif>", "<br/>",
    "<option>", "<frameset>", "<object>", "<base href=//evil.example.test/>", "<p style='color:red'>", "<td>",
]


def inputs():
    yield from ATTACKS.values()
    yield from MUTATIONS
    draw = random.Random(37)
    for _ in range(4000):
        yield "".join(draw.choice(PIECES) for _ in range(draw.randint(1, 7)))


def outputs():
    """[(input, output, images blocked)] for every input, both ways."""
    return [(given, sanitize.sanitize(given, load_images=not blocked)[0], blocked)
            for given in inputs() for blocked in (True, False)]


def test_the_sanitizers_output_is_its_own_sanitizing():
    moved = [(given, out) for given, out, blocked in outputs()
             if sanitize.sanitize(out, load_images=not blocked)[0] != out]
    assert moved == [], moved[:3]


# A browser's reading of one output: what in the tree it builds would act, there and after a round of
# serializing and parsing again.
READ = r"""
(cases) => {
  const ACTIVE = new Set(["script", "iframe", "frame", "frameset", "object", "embed", "applet", "form", "input", "button",
    "select", "textarea", "base", "meta", "link", "style", "svg", "math", "template", "noscript", "portal", "xmp",
    "plaintext", "noembed", "noframes", "isindex", "keygen", "dialog", "title", "audio", "video", "source", "track"]);
  const URLS = ["href", "src", "srcset", "action", "formaction", "xlink:href", "poster", "background", "data", "cite",
    "longdesc", "ping", "manifest", "codebase", "lowsrc", "dynsrc", "srcdoc"];
  const XHTML = "http://www.w3.org/1999/xhtml";
  // As a browser reads a URL's scheme: control characters and spaces anywhere in it don't count.
  const plain = (v) => v.replace(/[\u0000- \u007f-\u009f]/g, "").toLowerCase();
  const runs = (v) => /^(javascript|vbscript|livescript|mocha):/.test(v) || /^data:(?!image\/(png|jpeg|jpg|gif|webp)[;,])/.test(v);
  const remote = (v) => /^(https?:)?\/\//.test(v);
  const acting = (html, blocked) => {
    const doc = new DOMParser().parseFromString(html, "text/html");
    const found = [];
    for (const el of doc.head.children) found.push(`in the head: ${el.localName}`);
    for (const el of [doc.documentElement, doc.body, ...doc.body.querySelectorAll("*")]) {
      const name = el.localName.toLowerCase();
      if (el.namespaceURI !== XHTML) found.push(`${name} in ${el.namespaceURI}`);
      if (ACTIVE.has(name)) found.push(`a ${name}`);
      for (const a of el.attributes) {
        const n = a.name.toLowerCase();
        if (n.startsWith("on")) found.push(`${n} on ${name}`);
        if (URLS.includes(n)) {
          const parts = n === "srcset" ? a.value.split(",").map((p) => plain(p.trim().split(/\s+/)[0] || "")) : [plain(a.value)];
          for (const v of parts) {
            if (runs(v)) found.push(`${n}=${v.slice(0, 20)} on ${name}`);
            if (blocked && n !== "href" && n !== "cite" && remote(v)) found.push(`${n} loads ${v.slice(0, 30)} on ${name}`);
          }
        }
        if (n === "style") {
          const v = plain(a.value);
          if (/expression\(|javascript:|vbscript:|-moz-binding|behavior:/.test(v)) found.push(`style that runs on ${name}`);
          if (blocked && /url\((['"]?)(https?:)?\/\//.test(v)) found.push(`style that loads on ${name}`);
        }
      }
    }
    return { found, again: doc.body.innerHTML };
  };
  return cases.map(([html, blocked]) => {
    const first = acting(html, blocked);
    const second = acting(first.again, blocked);
    return [...first.found, ...second.found.map((f) => `after a round: ${f}`)];
  });
}
"""


@pytest.mark.browser
@pytest.mark.parametrize("kind", ["chromium", "firefox"])
def test_nothing_the_sanitizer_lets_through_acts_in_a_browsers_own_parser(kind, browser_runtime):
    engine = getattr(browser_runtime, kind)
    if not os.path.exists(engine.executable_path):
        pytest.skip(f"{kind} isn't installed for Playwright here (`uv run playwright install {kind}`)")
    cases = outputs()
    browser = engine.launch(headless=True)
    try:
        page = browser.new_context(java_script_enabled=True).new_page()
        page.set_content("<!doctype html><title>parse</title>")
        found = page.evaluate(READ, [[out, blocked] for _, out, blocked in cases])
    finally:
        browser.close()
    acting = [(given, out, said) for (given, out, _), said in zip(cases, found) if said]
    assert len(found) == len(cases)
    assert acting == [], "\n".join(f"{given!r}\n  -> {out!r}\n  {said}" for given, out, said in acting[:5])
