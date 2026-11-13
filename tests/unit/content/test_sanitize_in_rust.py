"""The HTML sanitizer, now the Rust core's, writes what the Python app's sanitizer wrote before it wherever the two read
a message alike, and wherever they don't, writes nothing a browser would run or fetch unasked.

The Python sanitizer is kept below as the oracle: as it was, but for what the core has changed since, each change marked
`the core now`. Python's CSS sanitizer wrote values, selectors and conditions a browser could read otherwise than it
checked them, so now nothing written marks a comment, breaks a line or leaves a bracket open. It reads text alone, so
the core's answers as it does for any text at all: the tests generate it, take each cap to its edge, and go through every character there
is where Python's `str` and `re` read characters their own way (spaces, word characters, lower-casing, ignored case).
Its HTML went through Python's html.parser, which reads some broken markup otherwise than a browser does. The core reads
it with html5ever's tokenizer, the one browsers' parsing follows, and its readings win: where the two parsers agree the
answers are equal, and on any markup at all, what the core writes is safe when read back, and reads back to itself.
core/crates/mm-content/tests/ pins answers on the Rust side, the places the readings differ among them.
"""
import functools
import itertools
import re
import string
from collections.abc import Iterator
from html import escape
from html.parser import HTMLParser
from types import SimpleNamespace
from typing import Literal, Protocol

import mailmind_leaves
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from mailmind.content.sanitize import html as sanitizer

# ── the oracle: mailmind/content/sanitize/html.py as it was, and what the core changed since ──────────────────────────

# Tags whose CONTENT is also dropped (containers — wait for the close tag).
DROP_WITH_CONTENT = {
    "script", "head", "title", "iframe", "object", "embed", "applet",   # "style" captured + sanitized
    "form", "button", "select", "textarea", "noscript", "template",
    "svg", "math", "frameset", "xml",
}
# Dangerous VOID/empty tags — drop the tag itself but NEVER enter skip mode
# (they have no end tag; skipping-until-close would swallow the whole document).
DROP_VOID = {"meta", "link", "base", "input", "frame", "source", "param", "keygen"}
# Allowed tags (everything else is unwrapped: tag dropped, text kept).
ALLOWED = {
    "p", "br", "hr", "div", "span", "a", "b", "i", "u", "s", "strike", "em",
    "strong", "small", "sub", "sup", "mark", "abbr", "cite", "q", "blockquote",
    "pre", "code", "kbd", "samp", "var", "ul", "ol", "li", "dl", "dt", "dd",
    "table", "thead", "tbody", "tfoot", "tr", "td", "th", "caption", "colgroup",
    "col", "h1", "h2", "h3", "h4", "h5", "h6", "img", "figure", "figcaption",
    "center", "font", "address", "article", "section", "header", "footer", "main",
    "nav", "aside", "wbr", "time", "details", "summary",
}
VOID = {"br", "hr", "img", "col", "wbr"}
# Per-tag allowed attributes. '*' applies to every allowed tag.
ATTRS = {
    "*": {"dir", "title", "lang", "align", "colspan", "rowspan", "width", "height",
          "valign", "span", "start", "datetime"},
    "a": {"href"},
    "img": {"src", "alt"},
    "td": {"colspan", "rowspan"}, "th": {"colspan", "rowspan"},
    "font": {"color"},
}
URL_ATTR = {"a": "href", "img": "src"}
SAFE_URL = re.compile(r"^\s*(https?:|mailto:|cid:)", re.I)
# strip whitespace/control chars an attacker uses to hide the scheme (e.g. "java\tscript:")
_CTRL = re.compile(r"[\x00-\x20   ]")
_DATA_IMG = re.compile(r"^\s*data:image/(png|jpe?g|gif|webp|bmp);", re.I)  # raster only, no svg+xml (active content)
_BAD_VAL = re.compile(r"(expression\s*\(|javascript\s*:|vbscript\s*:|behaviou?r\s*:|-moz-binding|@import|&\s*\{|/\*)", re.I)


# ─────────────────────────────────────────────────────────────────────────────
# CSS SANITIZER (stdlib only). Two public entries share one declaration core.
# Never raises; fails closed by dropping the offending declaration/rule.
#
# Security invariant (defeats decode-desync): every value is reduced ONCE to a
# single canonical decoded string. Matching, url()-scanning, AND emission all
# derive from THAT string (matching only additionally lowercases). Nothing reaches
# output that the gate did not see post-decode. A residual backslash / control char
# / stray `url(` / `var(` after canonicalization => drop the declaration.
# Designed + adversarially red-teamed (4 attack classes; all hardenings applied).
# ─────────────────────────────────────────────────────────────────────────────

# Hard caps (DoS): bound work BEFORE any O(n) normalization runs.
_CSS_INLINE_MAX      = 4096      # raw style="..." value cap (pre-normalize). Over => "".
_CSS_DECL_VAL_MAX    = 1024      # per-declaration value cap (post-normalize). Over => drop decl.
_CSS_PROP_MAX        = 64        # property-name cap. Over => drop decl.
_CSS_INLINE_DECLS    = 64        # max declarations per inline style. Over => stop.
_CSS_SHEET_MAX       = 50_000    # <style> input cap. Truncated beyond.
_CSS_SHEET_RULES     = 1000      # max rules processed in a stylesheet.
_CSS_SHEET_DECLS     = 256       # max declarations per rule block.
_CSS_NEST_MAX        = 3         # max @media/@supports/@layer recursion depth.
_CSS_SELECTOR_MAX    = 512       # selector text cap. Over => drop rule.
_CSS_URL_ARG_MAX     = 4096      # url() argument cap. Over => drop decl.

# Allowed properties (matched on normalized name). Anything else dropped silently.
CSS_ALLOWED_PROPS = {
    # color / text
    "color", "background-color", "opacity",
    "font", "font-family", "font-size", "font-style", "font-weight",
    "font-variant", "font-stretch", "line-height", "letter-spacing",
    "word-spacing", "text-align", "text-decoration", "text-decoration-line",
    "text-decoration-color", "text-decoration-style", "text-transform",
    "text-indent", "text-overflow", "text-shadow", "white-space",
    "word-break", "word-wrap", "overflow-wrap", "direction", "unicode-bidi",
    "vertical-align", "list-style-type", "list-style-position",
    # box model
    "margin", "margin-top", "margin-right", "margin-bottom", "margin-left",
    "padding", "padding-top", "padding-right", "padding-bottom", "padding-left",
    "border", "border-width", "border-style", "border-color",
    "border-top", "border-right", "border-bottom", "border-left",
    "border-top-width", "border-top-style", "border-top-color",
    "border-right-width", "border-right-style", "border-right-color",
    "border-bottom-width", "border-bottom-style", "border-bottom-color",
    "border-left-width", "border-left-style", "border-left-color",
    "border-radius", "border-top-left-radius", "border-top-right-radius",
    "border-bottom-left-radius", "border-bottom-right-radius",
    "border-collapse", "border-spacing", "box-shadow", "box-sizing", "outline",
    # sizing
    "width", "height", "min-width", "min-height", "max-width", "max-height",
    # layout (presentational, no overlay risk)
    "display", "float", "clear", "overflow", "overflow-x", "overflow-y",
    "visibility", "table-layout", "caption-side", "empty-cells",
    # url-bearing (gated, see _css_gate_urls)
    "background", "background-image", "background-repeat", "background-position",
    "background-size", "background-clip", "background-origin", "background-attachment",
    "list-style", "list-style-image",
    # value-restricted specials
    "position",
}

# Properties that may legitimately carry a url() token. ALL others reject any url().
CSS_URL_PROPS = {
    "background", "background-image", "list-style", "list-style-image",
}

# Value-restricted properties: every space-token must be in the allow-set (per-token).
CSS_PROP_VALUE_ALLOW = {
    "position": {"static", "relative", "inherit", "initial", "unset", "revert"},
}

# Keyword-only properties: reject if normalized value contains a function/url/scheme.
CSS_KEYWORD_ONLY = {
    "display", "float", "clear", "overflow", "overflow-x", "overflow-y",
    "visibility", "position", "table-layout", "border-collapse",
    "caption-side", "empty-cells", "white-space", "text-transform",
    "text-align", "font-style", "font-weight", "list-style-type",
    "list-style-position", "vertical-align", "box-sizing", "direction",
    "unicode-bidi", "word-break",
}

# Sizing props where a viewport/percentage curtain enables full-pane spoofing.
CSS_SIZING_PROPS = {
    "width", "height", "min-width", "min-height", "max-width", "max-height",
}
# Viewport units (and % heights) that let an email paint a full-pane fake panel.
_CSS_VIEWPORT_UNIT = re.compile(r"(?<![a-z])(vh|vw|vmin|vmax|dvh|svh|lvh|dvw|svw|lvw)(?![a-z])", re.I)

# Value denylist, matched on the DECODED + lowercased value. Each arm is a flat
# alternation (no nested quantifiers) => linear time, no catastrophic backtracking.
_CSS_BAD_VAL = re.compile(
    r"(expression\s*\("
    r"|javascript\s*:"
    r"|vbscript\s*:"
    r"|behaviou?r\s*:?"
    r"|-moz-binding"
    r"|binding\s*:"
    r"|@import"
    r"|@charset"
    r"|@namespace"
    r"|image-set\s*\("       # image-set() can carry remote url()s; not needed
    r"|<\s*/?\s*style"
    r"|[<>]"                 # any raw angle bracket => drop (protects <style> emit path)
    r"|&\s*\{"               # HTC / XBL
    r"|\\"                   # any residual backslash AFTER decode => malformed/recursive => drop
    r")", re.I)

# Functional notations that re-introduce indirection / re-expansion risk.
_CSS_BAD_FUNC = re.compile(r"(var|env|attr|image-set|cross-fade|element|paint)\s*\(", re.I)

# CSS hex-escape: \ + 1..6 hex + optional single trailing whitespace.
_CSS_HEX_ESC = re.compile(r"\\([0-9a-fA-F]{1,6})[ \t\r\n\f]?")
# CSS escape of a literal (non-hex) char: \X  (X is a single non-hex, non-newline char).
_CSS_CHAR_ESC = re.compile(r"\\([^0-9a-fA-F\r\n\f])")
# url(...) token, honoring optional single/double quotes. Scanned on the DECODED string.
_CSS_URL_TOKEN = re.compile(r"url\(\s*(\"[^\"]*\"|'[^']*'|[^)]*)\s*\)", re.I)
# canonical, already-safe url("...") form we re-emit (used by the belt-and-suspenders check)
_CSS_CANON_URL = re.compile(r"url\(\"(?:[^\"\\]|\\.)*\"\)")


class CssContext(Protocol):
    """What the CSS sanitizer reads of its caller and tells it: _San is the one that renders."""
    load_images: bool
    blocked_css_urls: int


# One top-level unit of a stylesheet: ('rule', selector, block), or ('at', at_keyword, prelude, block).
type _CssUnit = tuple[Literal["rule"], str, str] | tuple[Literal["at"], str, str, str | None]


def _css_decode_escapes(s: str) -> str:
    """SINGLE explicit pass decoding CSS \\hex and \\char escapes. Output is capped to
    the input length (escapes only shrink), so this cannot expand. Non-recursive: a
    backslash that the decode leaves behind (recursive/malformed escape) is caught by
    the `\\` arm of _CSS_BAD_VAL => fail closed."""
    if "\\" not in s:
        return s
    def _hex(m: re.Match[str]) -> str:
        try:
            cp = int(m.group(1), 16)
            if cp == 0 or cp > 0x10FFFF:
                return "�"
            return chr(cp)
        except Exception:
            return ""
    s = _CSS_HEX_ESC.sub(_hex, s)
    s = _CSS_CHAR_ESC.sub(lambda m: m.group(1), s)
    return s[:_CSS_DECL_VAL_MAX * 2]


def _css_breaks_out(value: str) -> bool:
    """Whether a decoded value, written out as it reads, would end its declaration or its rule early: a `;` outside any
    string or parentheses, a brace outside a string, or a string or parenthesis left open. An escaped quote decodes to a
    bare one, so `"a\\";position:fixed` would read as a string, then a declaration of its own. Fail closed."""
    # the core now: a square bracket is held as a parenthesis is, each closed by its own match, and a line break in a
    # string breaks out, as a browser ends the string there. Selectors and conditions are held to it too.
    quote, opened = "", []
    for ch in value:
        if quote:
            if ch == quote:
                quote = ""
            elif ch in "\n\r\f":
                return True
        elif ch in "\"'":
            quote = ch
        elif ch in "([":
            opened.append(ch)
        elif ch in ")]":
            if not opened or opened.pop() != "(["[")]".index(ch)]:
                return True
        elif ch in "{}" or (ch == ";" and not opened):
            return True
    return bool(quote) or bool(opened)


def _css_written(css: str) -> str:
    """the core now: what a style attribute or a sheet is about to be is written only if a browser reads it as written:
    no comment mark, line break or backslash, and every string, brace, parenthesis and square bracket closed by its own
    match."""
    if "/*" in css or "*/" in css or any(ch in css for ch in "\n\r\f\\"):
        return ""
    quote, opened = "", []
    for ch in css:
        if quote:
            if ch == quote:
                quote = ""
        elif ch in "\"'":
            quote = ch
        elif ch in "{([":
            opened.append(ch)
        elif ch in "})]":
            if not opened or opened.pop() != "{(["["})]".index(ch)]:
                return ""
    return "" if quote or opened else css


def _css_strip_comments(s: str) -> str:
    """Remove /* ... */ including an unterminated /* to EOF. Only removes => no growth."""
    out: list[str] = []
    i, n = 0, len(s)
    while i < n:
        j = s.find("/*", i)
        if j < 0:
            out.append(s[i:])
            break
        out.append(s[i:j])
        k = s.find("*/", j + 2)
        if k < 0:
            break               # unterminated comment: drop to EOF
        i = k + 2
    return "".join(out)


def _css_canonical(raw: str) -> tuple[str, str]:
    """Reduce a raw value to the ONE canonical string used for BOTH matching and
    emission. Order: strip-comments -> decode-escapes -> control-strip -> ws-collapse.
    Returns (canon, lowered): emission uses `canon`; decisions use `lowered`
    (== canon.lower()). They are byte-identical except for case."""
    s = _css_strip_comments(raw)
    s = _css_decode_escapes(s)
    s = _CTRL.sub(" ", s)                 # NBSP/tab/control -> space (defeats java\xa0script)
    s = re.sub(r"\s+", " ", s).strip()
    return s, s.lower()


def _css_serialize_url(url: str) -> str:
    """Wrap a vetted URL in canonical url("...") with " and \\ escaped, so it can't
    terminate the url(), the declaration, or inject ;}<."""
    esc = url.replace("\\", "\\\\").replace('"', '\\"')
    return 'url("%s")' % esc


def _css_gate_urls(canon: str, lowered: str, prop_is_url: bool, ctx: CssContext) -> str | None:
    """Rewrite/gate EVERY url() token in `canon`. Returns the emitted value (canonical)
    or None to DROP THE ENTIRE DECLARATION. Single rule: ANY disallowed url() token =>
    drop the whole declaration, never a partial value."""
    if "url(" not in lowered:
        return canon                      # nothing to gate
    if not prop_is_url:
        return None                       # url() on a non-url property => drop decl

    drop = [False]
    blocked_remote = [False]

    def _repl(m: re.Match[str]) -> str:
        arg = m.group(1).strip()
        if len(arg) >= 2 and arg[0] in "\"'" and arg[-1] == arg[0]:
            arg = arg[1:-1]
        arg = arg.strip()
        # control-strip BEFORE scheme checks (defeats url(java\tscript:...))
        arg_clean = _CTRL.sub("", arg)
        # Post-decode, a url() arg must be clean: no backslash, no control, no whitespace,
        # no nested url/quote. Anything left => the browser could re-expand => drop.
        if (not arg_clean or len(arg_clean) > _CSS_URL_ARG_MAX
                or "\\" in arg or arg_clean != arg
                or re.search(r"\s", arg) or '"' in arg or "'" in arg
                or "url(" in arg.lower() or "(" in arg or ")" in arg):
            drop[0] = True
            return ""
        low = arg_clean.lower()
        # data:image raster (NOT svg+xml): inline, no network -> keep regardless of consent.
        if _DATA_IMG.match(arg_clean):
            return _css_serialize_url(arg_clean)
        # remote http(s): tracker/beacon vector. Gate on consent, count in a DISTINCT counter.
        if low.startswith(("http:", "https:")) and SAFE_URL.match(arg_clean):
            if not ctx.load_images:
                blocked_remote[0] = True
                drop[0] = True
                return ""
            return _css_serialize_url(arg_clean)
        # everything else (svg, cid:, data:non-image, relative, javascript:, empty) => drop
        drop[0] = True
        return ""

    out = _CSS_URL_TOKEN.sub(_repl, canon)
    if blocked_remote[0]:
        ctx.blocked_css_urls = getattr(ctx, "blocked_css_urls", 0) + 1
    if drop[0]:
        return None
    return out


def _css_sanitize_one(prop: str, raw_val: str, ctx: CssContext) -> str | None:
    """One declaration -> 'prop:val' (canonical) or None. Never raises to caller."""
    prop = prop.strip().lower()
    prop = _css_decode_escapes(prop).strip().lower()
    if not prop or len(prop) > _CSS_PROP_MAX or prop not in CSS_ALLOWED_PROPS:
        return None

    canon, lowered = _css_canonical(raw_val)
    if not canon or len(canon) > _CSS_DECL_VAL_MAX:
        return None

    # 1) global value denylist (runs on decoded+lowercased copy)
    if _CSS_BAD_VAL.search(lowered):
        return None
    if _css_breaks_out(canon):
        return None
    # 2) functional-indirection denylist (var/env/attr/image-set/...). url( handled below.
    if _CSS_BAD_FUNC.search(lowered):
        return None
    # 3) sizing curtain guard: reject viewport units and % heights on sizing props
    if prop in CSS_SIZING_PROPS:
        if _CSS_VIEWPORT_UNIT.search(lowered):
            return None
        if "%" in lowered and prop in ("height", "min-height", "max-height"):
            return None
    # 4) per-property value allow-set (every token must match)
    allow = CSS_PROP_VALUE_ALLOW.get(prop)
    if allow is not None:
        toks = [t for t in lowered.replace(",", " ").split(" ") if t]
        if not toks or any(t not in allow for t in toks):
            return None
    # 5) keyword-only props: no function, no url, no scheme, no nested decl
    if prop in CSS_KEYWORD_ONLY:
        if "(" in lowered or "url" in lowered or ":" in lowered or ";" in lowered:
            return None
    # 6) url() gate (single string, decoded) — drops decl on any bad token
    emitted = _css_gate_urls(canon, lowered, prop in CSS_URL_PROPS, ctx)
    if emitted is None:
        return None

    # 7) belt-and-suspenders: nothing reaches output the gate didn't canonicalize.
    if "url(" in emitted.lower():
        residue = _CSS_CANON_URL.sub("", emitted)
        if "url(" in residue.lower():
            return None
    if "\\" in emitted or "<" in emitted or ">" in emitted or "/*" in emitted or "*/" in emitted:   # the core now: /* */
        return None
    emitted = emitted.strip().rstrip(";").strip()
    if not emitted:
        return None
    return "%s:%s" % (prop, emitted)


def _css_split_decls(block: str) -> list[str]:
    """Split a declaration block on top-level ';', respecting url() and strings so a ';'
    inside url("a;b") or a quoted value doesn't desync. Bounded single pass."""
    decls: list[str] = []
    buf: list[str] = []
    i, n = 0, len(block)
    depth = 0
    quote: str | None = None
    while i < n:
        ch = block[i]
        if quote:
            buf.append(ch)
            if ch == "\\" and i + 1 < n:
                buf.append(block[i + 1]); i += 2; continue
            if ch == quote:
                quote = None
            i += 1; continue
        if ch in "\"'":
            quote = ch; buf.append(ch); i += 1; continue
        if ch == "(":
            depth += 1; buf.append(ch); i += 1; continue
        if ch == ")":
            if depth > 0: depth -= 1
            buf.append(ch); i += 1; continue
        if ch == ";" and depth == 0:
            decls.append("".join(buf)); buf = []; i += 1; continue
        buf.append(ch); i += 1
    if buf:
        decls.append("".join(buf))
    return decls


def _css_sanitize_declarations(block: str, ctx: CssContext, max_decls: int) -> str:
    """Shared core: 'p:v;p:v' -> sanitized 'p:v;p:v' (possibly ''). Per-decl fail-closed."""
    out: list[str] = []
    for idx, decl in enumerate(_css_split_decls(block)):
        if idx >= max_decls:
            break
        try:
            if ":" not in decl:
                continue
            prop, _, val = decl.partition(":")
            r = _css_sanitize_one(prop, val, ctx)
            if r:
                out.append(r)
        except Exception:
            continue
    return ";".join(out)


def python_sanitize_inline_style(value: str | None, ctx: CssContext) -> str:
    """Sanitize one style="..." attribute body. Returns safe 'p:v;p:v' (possibly '').
    Never raises. HARD 4096-char cap BEFORE any normalization (DoS defense)."""
    try:
        if not value or len(value) > _CSS_INLINE_MAX:
            return ""
        return _css_written(_css_sanitize_declarations(value, ctx, _CSS_INLINE_DECLS))      # the core now: written
    except Exception:
        return ""


def _css_tokenize_rules(css: str) -> Iterator[_CssUnit]:
    """Yield top-level units, honoring strings/comments for brace matching:
       ('rule', selector, block)            qualified rule
       ('at',  at_keyword, prelude, block)  at-rule (block may be None)
    Unterminated string/comment/brace => stop cleanly (fail closed)."""
    i, n = 0, len(css)
    seg_start = 0
    while i < n:
        ch = css[i]
        if ch == "/" and i + 1 < n and css[i + 1] == "*":
            k = css.find("*/", i + 2)
            if k < 0:
                return
            if not css[seg_start:i].strip():
                seg_start = k + 2       # the core now: a comment in front of a rule is no part of its selector
            i = k + 2; continue
        if ch in "\"'":
            j = i + 1
            while j < n:
                if css[j] == "\\":
                    j += 2; continue
                if css[j] == ch:
                    break
                j += 1
            if j >= n:
                return
            i = j + 1; continue
        if ch == "@":
            j = i
            while j < n and css[j] not in "{;":
                if css[j] in "\"'":
                    q = css[j]; j += 1
                    while j < n and css[j] != q:
                        j += 2 if css[j] == "\\" else 1
                j += 1
            prelude = css[i:j]
            m = re.match(r"@([A-Za-z-]+)", prelude)
            at_kw = (m.group(1).lower() if m else "")
            if j < n and css[j] == ";":
                yield ("at", at_kw, prelude[len(at_kw) + 1:].strip(), None)
                i = j + 1; seg_start = i; continue
            if j < n and css[j] == "{":
                blk, end = _css_read_block(css, j)
                if blk is None:
                    return
                yield ("at", at_kw, prelude[len(at_kw) + 1:].strip(), blk)
                i = end; seg_start = i; continue
            return
        if ch == "{":
            sel = css[seg_start:i]
            blk, end = _css_read_block(css, i)
            if blk is None:
                return
            yield ("rule", sel.strip(), blk)
            i = end; seg_start = i; continue
        if ch == "}":
            i += 1; seg_start = i; continue
        i += 1


def _css_read_block(css: str, open_idx: int) -> tuple[str, int] | tuple[None, int]:
    """Given index of '{', return (inner_text, index_after_matching_'}') or (None, -1)
    if unterminated. Respects strings/comments and nested braces."""
    i, n = open_idx + 1, len(css)
    depth = 1
    start = i
    while i < n:
        ch = css[i]
        if ch == "/" and i + 1 < n and css[i + 1] == "*":
            k = css.find("*/", i + 2)
            if k < 0:
                return (None, -1)
            i = k + 2; continue
        if ch in "\"'":
            j = i + 1
            while j < n:
                if css[j] == "\\":
                    j += 2; continue
                if css[j] == ch:
                    break
                j += 1
            if j >= n:
                return (None, -1)
            i = j + 1; continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return (css[start:i], i + 1)
        i += 1
    return (None, -1)


_CSS_SEL_BAD = re.compile(r"(url\(|expression|javascript:|[<>{}\\])", re.I)
_CSS_PRELUDE_OK = re.compile(r"^[\w \t()\[\]:,.%/*+-]*$")


def _css_sanitize_selector(sel: str) -> str | None:
    """Return safe selector text or None (drop rule)."""
    sel = sel.strip()
    if not sel or len(sel) > _CSS_SELECTOR_MAX:
        return None
    if _CSS_SEL_BAD.search(sel):
        return None
    # the core now: no comment mark, a line break written as a space, and read whole on its own
    if "/*" in sel or "*/" in sel:
        return None
    sel = sel.replace("\n", " ").replace("\r", " ").replace("\f", " ")
    return None if _css_breaks_out(sel) else sel


def _css_sanitize_rules(css: str, ctx: CssContext, depth: int, counter: list[int]) -> str:
    out: list[str] = []
    for unit in _css_tokenize_rules(css):
        if counter[0] >= _CSS_SHEET_RULES:
            break
        counter[0] += 1
        try:
            if unit[0] == "rule":
                _, sel, blk = unit
                ssel = _css_sanitize_selector(sel)
                if ssel is None:
                    continue
                decls = _css_sanitize_declarations(blk, ctx, _CSS_SHEET_DECLS)
                if decls:
                    out.append("%s{%s}" % (ssel, decls))
            else:
                _, kw, prelude, blk = unit
                if kw in ("import", "charset", "namespace", "font-face",
                          "keyframes", "page", "font-feature-values",
                          "-webkit-keyframes", "-moz-keyframes"):
                    continue            # drop entirely
                if kw in ("media", "supports", "layer"):
                    if blk is None or depth >= _CSS_NEST_MAX:
                        continue
                    pre = prelude.strip()
                    if pre and (not _CSS_PRELUDE_OK.match(pre) or "url" in pre.lower()):
                        continue
                    if "/*" in pre or "*/" in pre or _css_breaks_out(pre):     # the core now
                        continue
                    inner = _css_sanitize_rules(blk, ctx, depth + 1, counter)
                    if inner:
                        out.append("@%s %s{%s}" % (kw, pre, inner))
                # any other at-rule: drop
        except Exception:
            continue
    return "".join(out)


def python_sanitize_css_stylesheet(css: str | None, ctx: CssContext) -> str:
    """Sanitize <style> text content. Returns safe CSS (possibly ''). Guarantees no
    '<'/'>' in output (so it can't break out of <style>). Never raises."""
    try:
        if not css:
            return ""
        if len(css) > _CSS_SHEET_MAX:
            css = css[:_CSS_SHEET_MAX]
        result = _css_sanitize_rules(css, ctx, 0, [0])
        return _css_written(result.replace("<", "").replace(">", ""))      # the core now: written
    except Exception:
        return ""


def _safe_url(val: str | None) -> str | None:
    if not val:
        return None
    v = _CTRL.sub("", val)
    return val if SAFE_URL.match(v) else None


_FOLD_OPEN = '<details class="mm-quote"><summary>··· quoted text</summary>'
_FOLD_CLOSE = "</details>"


class _San(HTMLParser):
    def __init__(self, load_images: bool, fold: bool = True):
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.load_images = load_images
        self.fold = fold
        self.blocked_images = 0
        self.blocked_links = 0
        self._skip = 0          # nesting depth inside a dropped-with-content subtree
        self._skip_tag: str | None = None
        self._bq_depth = 0      # blockquote nesting depth
        self._folded = False    # have we wrapped the trailing quote chain in a <details> yet
        self._has_content = False  # any visible text seen before the first top-level quote
        self.blocked_css_urls = 0  # remote url() blocked in CSS while load_images=False
        self._in_style = 0      # inside a <style> being captured
        self._style_buf: list[str] = []    # buffered raw CSS text

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if self._skip:
            if tag == self._skip_tag:
                self._skip += 1
            return
        if tag in DROP_VOID:
            return  # drop the tag; it has no content/close so don't enter skip mode
        if tag == "style":
            self._in_style = 1
            self._style_buf = []
            return                       # emit on close, after sanitizing the CSS
        if tag in DROP_WITH_CONTENT:
            self._skip = 1
            self._skip_tag = tag
            return
        if tag not in ALLOWED:
            return  # unwrap: drop the tag, keep its text content
        # Reading comfort: fold the quoted reply chain. Replies put the quote at the end, so the
        # FIRST top-level <blockquote> (+ everything after) goes behind a pure-CSS <details> toggle.
        # Only fold when real content precedes it (don't fold a message that's ALL quote).
        if tag == "blockquote" and self.fold and self._bq_depth == 0 and not self._folded and self._has_content:
            self.out.append(_FOLD_OPEN)
            self._folded = True
        if tag == "blockquote":
            self._bq_depth += 1
        clean = self._clean_attrs(tag, attrs)
        self.out.append("<" + tag + clean + ("/>" if tag in VOID else ">"))

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag.lower() not in VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "style":
            if self._in_style:
                css = python_sanitize_css_stylesheet("".join(self._style_buf), self)
                if css:
                    self.out.append("<style>" + css + "</style>")
                self._in_style = 0
                self._style_buf = []
            return
        if self._skip:
            if tag == self._skip_tag:
                self._skip -= 1
                if self._skip == 0:
                    self._skip_tag = None
            return
        if tag in ALLOWED and tag not in VOID:
            self.out.append("</" + tag + ">")
            if tag == "blockquote" and self._bq_depth > 0:
                self._bq_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._in_style:
            self._style_buf.append(data)
            return                       # never escape/measure buffered CSS
        if self._skip:
            return
        if data.strip() and self._bq_depth == 0 and not self._folded:
            self._has_content = True   # real text before the first quote -> there's something to fold under
        self.out.append(escape(data, quote=False))

    def handle_comment(self, data: str) -> None:
        return  # drop comments (conditional comments, hidden payloads)

    def _clean_attrs(self, tag: str, attrs: list[tuple[str, str | None]]) -> str:
        allowed = ATTRS.get("*", set()) | ATTRS.get(tag, set())
        urlattr = URL_ATTR.get(tag)
        parts: list[str] = []
        for name, val in attrs:
            name = (name or "").lower()
            if name == "style":
                # keep a SANITIZED style= (rich newsletters); hard 8192 raw cap (DoS — this
                # path skips the generic attr-length cap below). sanitize_inline_style never raises.
                if val is not None and len(val) <= 8192:
                    css = python_sanitize_inline_style(val, self)
                    if css:
                        parts.append('style="%s"' % escape(css, quote=True))
                continue
            if name.startswith("on") or name in ("srcset", "background",
                                                  "formaction", "xlink:href", "src", "href"):
                # handle href/src explicitly below; drop on*, srcset, etc.
                if name not in ("href", "src"):
                    continue
            if name not in allowed:
                continue
            if name == urlattr:
                if tag == "img":
                    if val and _DATA_IMG.match(val):
                        if not self.load_images:
                            self.blocked_images += 1
                            continue
                        parts.append('src="%s"' % escape(val, quote=True))
                        continue
                    safe = _safe_url(val) if val and val.lower().lstrip().startswith(("http", "cid")) else None
                    # remote http(s) image: block unless load_images
                    if safe and not self.load_images:
                        self.blocked_images += 1
                        continue
                    if not safe:
                        continue
                    parts.append('src="%s"' % escape(safe, quote=True))
                    continue
                else:  # a href
                    safe = _safe_url(val)
                    if not safe:
                        self.blocked_links += 1
                        continue
                    parts.append('href="%s"' % escape(safe, quote=True))
                    continue
            # plain allowlisted attribute
            v = "" if val is None else val
            if len(v) > 2048 or _BAD_VAL.search(v):
                continue  # drop CSS/script smuggled into color=/width= etc.
            parts.append('%s="%s"' % (name, escape(v, quote=True)))
        if tag == "a" and any(p.startswith("href=") for p in parts):
            parts.append('rel="noopener noreferrer nofollow" target="_blank"')
        return (" " + " ".join(parts)) if parts else ""

    def finish(self) -> str:
        """All of it, once fed and closed."""
        if self._in_style and self._style_buf:     # unclosed <style>: flush so the rest of the doc isn't swallowed
            css = python_sanitize_css_stylesheet("".join(self._style_buf), self)
            if css:
                self.out.append("<style>" + css + "</style>")
        out = "".join(self.out)
        if self._folded:
            out += _FOLD_CLOSE           # the fold wrapper opened mid-stream; close it at the end
        return out


def python_without_fold(html: str) -> str:
    """`html`, sanitized before replies stopped carrying the fold, without it: a draft's stored
    quote. sanitize closes the fold at the very end of what it returns."""
    head, opened, rest = html.partition(_FOLD_OPEN)
    if not opened:
        return html
    body, closed, after = rest.rpartition(_FOLD_CLOSE)
    return head + (body + after if closed else rest)


def python_sanitize(html: str | None, load_images: bool = False, fold: bool = True) -> tuple[str, dict[str, int]]:
    """Return (clean_html, info). Never raises on malformed input. `fold` is for reading: it puts a
    quoted chain behind a toggle the reader's stylesheet draws, which mail going out must not carry."""
    p = _San(load_images, fold)
    try:
        p.feed(html or "")
        p.close()
    except Exception:
        # parser blew up on something pathological — fail closed to escaped text
        return escape(html or "", quote=False), {"blocked_images": 0, "blocked_links": 0, "blocked_css_urls": 0}
    return p.finish(), {"blocked_images": p.blocked_images, "blocked_links": p.blocked_links,
                 "blocked_css_urls": getattr(p, "blocked_css_urls", 0)}


# ── reading each ─────────────────────────────────────────────────────────────────────────────────────────────────

NOTHING_BLOCKED = {"blocked_images": 0, "blocked_links": 0, "blocked_css_urls": 0}


class Ctx:
    """What a CSS sanitizer is handed: whether images load, and the count it adds each blocked url() to."""

    def __init__(self, load_images):
        self.load_images = load_images
        self.blocked_css_urls = 0


def unpaired(text):
    """`text` with each lone surrogate as U+FFFD. No Rust string holds a lone surrogate, so the core reads one as
    U+FFFD, and writes U+FFFD where a CSS escape names one; no browser could be sent either."""
    return "".join("\ufffd" if "\ud800" <= c <= "\udfff" else c for c in text)


def css_of(entry, css, load_images):
    """What a CSS sanitizer writes of `css`, and how many url() it blocked."""
    ctx = Ctx(load_images)
    return unpaired(entry(css, ctx)), ctx.blocked_css_urls


def assert_css_as_python_wrote_it(css, load_images=True):
    """`css` read as a style attribute and as a style sheet."""
    assert css_of(sanitizer.sanitize_inline_style, css, load_images) == \
        css_of(python_sanitize_inline_style, css, load_images), css
    assert css_of(sanitizer.sanitize_css_stylesheet, css, load_images) == \
        css_of(python_sanitize_css_stylesheet, css, load_images), css


def assert_html_as_python_wrote_it(html, load_images, fold=True):
    out, info = python_sanitize(html, load_images, fold)
    assert sanitizer.sanitize(html, load_images, fold) == (unpaired(out), info), html


def every_character():
    return (chr(c) for c in range(0x110000) if not 0xD800 <= c <= 0xDFFF)


@functools.cache
def near_letters():
    """Every character Python's lower-casing, upper-casing or ignored case leads to or from an ASCII letter: a dotted
    capital I and a dotless small i match `i`, a long s `s` and the Kelvin sign `k`, and lower-casing a dotted capital
    I writes two characters."""
    letters = set(string.ascii_letters)
    folds = re.compile("[a-z]", re.I)
    near = [c for c in every_character() if letters & set(c.lower() + c.upper() + c.casefold()) or folds.fullmatch(c)]
    assert {"\u0130", "\u0131", "\u017f", "\u212a", "\ufb00"} <= set(near)
    return near


def written_otherwise(text, near):
    """`text` with each of its ASCII letters in turn written as each character in `near`."""
    for at, letter in enumerate(text):
        if letter in string.ascii_letters:
            for c in near:
                yield text[:at] + c + text[at + 1:]


# ── the CSS sanitizer, for any text at all ───────────────────────────────────────────────────────────────────────

def test_the_caps_are_the_ones_python_had():
    assert (mailmind_leaves.CSS_INLINE_MAX, mailmind_leaves.CSS_DECL_VALUE_MAX, mailmind_leaves.CSS_PROP_MAX,
            mailmind_leaves.CSS_INLINE_DECLS, mailmind_leaves.CSS_SHEET_MAX, mailmind_leaves.CSS_SHEET_RULES,
            mailmind_leaves.CSS_SHEET_DECLS, mailmind_leaves.CSS_NEST_MAX, mailmind_leaves.CSS_SELECTOR_MAX,
            mailmind_leaves.CSS_URL_ARG_MAX) == \
        (_CSS_INLINE_MAX, _CSS_DECL_VAL_MAX, _CSS_PROP_MAX, _CSS_INLINE_DECLS, _CSS_SHEET_MAX, _CSS_SHEET_RULES,
         _CSS_SHEET_DECLS, _CSS_NEST_MAX, _CSS_SELECTOR_MAX, _CSS_URL_ARG_MAX)


# Every character Python takes for a space (`str.isspace`, `\s`) or the sanitizer for a control: none lies past U+3000,
# as test_every_character_reads_in_a_declaration_as_python_read_it goes on to show.
SPACES = sorted({c for c in map(chr, range(0x3001)) if c.isspace() or re.fullmatch(r"\s", c) or _CTRL.fullmatch(c)})
PROPERTIES = ["color", "background", "background-image", "list-style", "list-style-image", "position", "width",
              "height", "min-height", "max-height", "display", "font-family", "COLOR", " Position ", "\\63olor",
              "colo\\72 ", "-moz-binding", "behavior", "x-unknown"]
CSS_WORDS = [
    *PROPERTIES,
    ":", ";", "{", "}", "(", ")", "[", "]", '"', "'", "\\", "/*", "*/", "/", "*", ",", "!important", "@", "&{", "<", ">",
    "</style", "<style", "@media", "@MEDIA", "@import", "@font-face", "@supports", "@layer", "@page", "@charset",
    "@namespace", "@keyframes", "@-webkit-keyframes", "screen", "(min-width:1px)", "and", "p", ".a", "#b", "a:hover",
    "[x]", "td>p", "p q", "url(", "URL(", "https://e.example.test/a.png", "HTTPS://e.example.test", "http:",
    "data:image/png;base64,AA", "data:image/svg+xml,x", "data:text/html,x", "javascript:", "vbscript:", "cid:x",
    "mailto:a", "//e.example.test/", "red", "#fff", "1px", "100vh", "50%", "vmin", "dvh", "VH", "\u017fvh",
    "vm\u0130n", "fixed", "relative", "static", "inherit", "expression", "expression(", "behavior:", "binding:",
    "var(", "attr(", "image-set(", "env(", "cross-fade(", "paint(", "rgba(0,0,0,.2)", "translate(1px)", "x", "0",
    "\\75", "\\72 ", "\\6c", "\\28", "\\29", "\\22", "\\27", "\\3c", "\\5c", "\\0", "\\D800", "\\110000", "\\/",
    "\\*", "\\a", "\\A ", "\\ffffff", "\\2f", "\\2a", "\u0130", "\u0131", "\u017f", "\u212a", "\u212b", "\u03a3", "\xdf",
    "\xe9", "\U0001f600", "\x00", "\r",
]
hex_escapes = st.builds("\\{}{}".format, st.text("0123456789abcdefABCDEF", min_size=1, max_size=7),
                        st.sampled_from(["", " ", "\t", "\n", "\r", "\r\n", "\x0c", "x", "g"]))
css_text = st.lists(st.one_of(st.sampled_from(CSS_WORDS), st.sampled_from(SPACES), hex_escapes,
                              st.characters().map("\\".__add__), st.text(max_size=3)), max_size=40).map("".join)
declarations = st.lists(st.builds("{}:{}".format, st.sampled_from(PROPERTIES) | css_text, css_text),
                        max_size=8).map(";".join)
selectors = st.sampled_from(["p", ".a", "td p", "a:hover", "*", "p,q", "[x='y']", "a /* c */ b", ""]) | css_text
preludes = st.sampled_from(["", "screen", "(min-width:600px)", "print and (color)", "url(x)", "a/b", "x;y"]) | css_text
rules = st.recursive(
    st.builds("{}{{{}}}".format, selectors, declarations),
    lambda inner: st.builds("@{} {}{{{}}}".format,
                            st.sampled_from(["media", "supports", "layer", "MEDIA", "font-face", "page", "x"]),
                            preludes, st.lists(inner, max_size=3).map("".join)),
    max_leaves=10)
sheets = st.lists(rules | css_text, max_size=6).map("".join)


@settings(max_examples=2000, deadline=None)
@given(css_text | declarations, st.booleans())
def test_any_style_text_is_written_as_python_wrote_it(css, load_images):
    assert_css_as_python_wrote_it(css, load_images)


@settings(max_examples=2000, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(sheets, st.booleans())
def test_any_style_sheet_is_written_as_python_wrote_it(css, load_images):
    assert_css_as_python_wrote_it(css, load_images)


@settings(max_examples=2000, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(css_text | declarations | sheets, st.booleans())
def test_whatever_css_is_written_a_browser_reads_as_written(css, load_images):
    """Neither a style attribute nor a style sheet the sanitizer writes opens or closes a comment, or reads otherwise."""
    assert_reads_as_written(sanitizer.sanitize_inline_style(css, Ctx(load_images)))
    assert_reads_as_written(sanitizer.sanitize_css_stylesheet(css, Ctx(load_images)))


# A pad and how many characters of a declaration's value each one leaves once comments, escapes and spaces are read.
PADS = [("a", 1), ("\xe9", 1), ("\U0001f600", 1), ("\\61", 1), ("\\61 ", 1), ("x/**/", 1), ("b c", 3),
        ("\u3000d", 2), ("\x1ce", 2)]


def padded(start, pad, length, end=""):
    """`start`, `pad` over and over, then `end`: `length` characters in all."""
    room = max(length - len(start) - len(end), 0)
    return start + (pad * room)[:room] + end


@settings(max_examples=300, deadline=None)
@given(st.integers(-3, 3), st.sampled_from([pad for pad, _ in PADS] + [" ", "\u3000", ";"]), st.booleans())
def test_a_style_attribute_at_the_edge_of_its_length(off, pad, load_images):
    assert_css_as_python_wrote_it(padded("color:red;font-family:x", pad, _CSS_INLINE_MAX + off), load_images)


@settings(max_examples=300, deadline=None)
@given(st.integers(-4, 4), st.sampled_from(PADS), st.sampled_from(["font-family:", "width:", "background:url(http:"]),
       st.booleans())
def test_a_value_at_the_edge_of_its_length(off, pad, start, load_images):
    pad, width = pad
    value = pad * ((_CSS_DECL_VAL_MAX + off) // width) + "a" * ((_CSS_DECL_VAL_MAX + off) % width)
    declaration = start + value + (")" if "(" in start else "")
    assert_css_as_python_wrote_it(declaration, load_images)
    assert_css_as_python_wrote_it("p{" + declaration + "}", load_images)


@settings(max_examples=300, deadline=None)
@given(st.integers(-3, 3), st.sampled_from(SPACES))
def test_a_value_escapes_decode_past_the_length_they_are_read_to(off, space):
    """Decoding escapes keeps twice a value's length: spaces past it go, with whatever they hid."""
    declaration = "font-family:\\61" + space * (2 * _CSS_DECL_VAL_MAX + off) + "b"
    assert_css_as_python_wrote_it(declaration)
    assert_css_as_python_wrote_it("p{" + declaration + "}")


@settings(max_examples=300, deadline=None)
@given(st.sampled_from([_CSS_INLINE_DECLS, _CSS_SHEET_DECLS]).flatmap(
    lambda cap: st.lists(st.sampled_from(["color:red", "", " ", "x", "width:1vh", "font-family:a"]),
                         min_size=cap - 3, max_size=cap + 3)))
def test_declarations_at_the_edge_of_their_count(declarations):
    assert_css_as_python_wrote_it(";".join(declarations))
    assert_css_as_python_wrote_it("p{" + ";".join(declarations) + "}")


@settings(max_examples=200, deadline=None)
@given(st.integers(-3, 3), st.sampled_from([("p{color:red}", 1), ("p{}", 1), ("@import x;", 1), ("}", 0),
                                            ("@media x{p{color:red}}", 2), ("@font-face{color:red}", 1)]))
def test_rules_at_the_edge_of_their_count(off, rule):
    rule, units = rule
    count = (_CSS_SHEET_RULES + off) // max(units, 1)
    assert_css_as_python_wrote_it(rule * count + "q{color:blue}")


@settings(max_examples=100, deadline=None)
@given(st.integers(0, _CSS_NEST_MAX + 3), st.sampled_from(["media", "supports", "layer", "MEDIA", "page"]))
def test_rules_at_the_edge_of_their_nesting(depth, keyword):
    assert_css_as_python_wrote_it(f"@{keyword} x{{" * depth + "p{color:red}" + "}" * depth)


@settings(max_examples=300, deadline=None)
@given(st.integers(-15, 15), st.sampled_from([pad for pad, _ in PADS] + [" ", "\u3000", "}", "{", "/"]))
def test_a_style_sheet_at_the_edge_of_its_length(off, pad):
    assert_css_as_python_wrote_it(padded("p{color:red}", pad, _CSS_SHEET_MAX + off, "q{color:blue}"))


@settings(max_examples=200, deadline=None)
@given(st.integers(-3, 3), st.sampled_from(["a", "\xe9", "\U0001f600", " ", "a b", "\u3000"]))
def test_a_selector_at_the_edge_of_its_length(off, pad):
    assert_css_as_python_wrote_it(padded(" x", pad, _CSS_SELECTOR_MAX + off, " ") + "{color:red}")


# ── the CSS sanitizer, character by character ────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("declarations", [
    "font-family:a{c}b;width:1{c}vh",
    "background:url({c}http:x);color:expression{c}(1)",
])
def test_every_character_reads_in_a_declaration_as_python_read_it(declarations):
    for c in every_character():
        css = declarations.format(c=c)
        assert css_of(sanitizer.sanitize_inline_style, css, True) == \
            css_of(python_sanitize_inline_style, css, True), hex(ord(c))


def test_every_character_reads_in_a_rule_as_python_read_it():
    """A selector is stripped of what Python took for spaces, and an at-rule's condition holds word characters."""
    for c in every_character():
        css = f"@media a{c}b{{q{{color:red}}}}{c}r{{color:red}}"
        assert css_of(sanitizer.sanitize_css_stylesheet, css, True) == \
            css_of(python_sanitize_css_stylesheet, css, True), hex(ord(c))


# Each holds a word the sanitizer looks for, ignoring case, or a name it compares once lower-cased.
DECLARATIONS_NAMING_WORDS = [
    "font-family:expression(1)", "font-family:javascript:x", "font-family:vbscript:x", "font-family:behaviour:x",
    "font-family:-moz-binding", "font-family:binding:x", "font-family:@import", "font-family:@charset",
    "font-family:@namespace", "font-family:image-set(x)", "width:var(x)", "width:env(x)", "width:attr(x)",
    "width:cross-fade(x)", "width:element(x)", "width:paint(x)", "width:1vh", "width:1vmin", "width:1vmax",
    "width:1dvh", "width:1svw", "width:1lvw", "background:url(https://e.example.test/a.png)",
    "background:url(http:x)", "list-style:url(data:image/png;base64,AA)", "background-image:url(data:image/jpeg;x)",
    "background:url(cid:x)", "position:static", "position:relative", "display:block", "text-align:center",
]
SHEETS_NAMING_WORDS = [
    "@media screen{p{color:red}}", "@supports x{p{color:red}}", "@layer x{p{color:red}}", "@import x;p{color:red}",
    "@font-face{p{color:red}}", "p url(x){color:red}", "p expression{color:red}", "javascript:x{color:red}",
    "@media url{p{color:red}}",
]


def test_a_letter_in_a_word_the_css_sanitizer_looks_for_reads_as_python_read_it():
    near = near_letters()
    for css in itertools.chain(*(written_otherwise(d, near) for d in DECLARATIONS_NAMING_WORDS + SHEETS_NAMING_WORDS)):
        assert_css_as_python_wrote_it(css)


# ── the HTML sanitizer, on markup both parsers read alike ────────────────────────────────────────────────────────

RAW_TEXT = ["script", "title", "textarea", "xmp", "iframe", "noembed", "noframes"]       # and style
VOIDS = sorted(VOID | DROP_VOID | {"area", "track"})
CONTAINERS = sorted((ALLOWED - VOID) | (DROP_WITH_CONTENT - set(RAW_TEXT))
                    | {"blink", "o:p", "x-card", "body", "html", "option"})
URLS = ["https://e.example.test/a.png", "HTTP://e.example.test/", " https://e.example.test/", "java\tscript:alert(1)",
        "javascript:alert(1)", "mailto:ada@example.test", "cid:part1", "data:image/png;base64,AA",
        " data:image/gif;base64,AA", "data:image/svg+xml,x", "data:text/html,x", "", "x", "//e.example.test/",
        "\u3000https://e.example.test/", "http\u017f://e.example.test/"]
URL_ATTRIBUTES = {"href", "src", "srcset", "background", "formaction", "xlink:href"}
ATTRIBUTE_NAMES = sorted(ATTRS["*"] | URL_ATTRIBUTES | {"alt", "color", "style", "onclick", "onerror", "class", "id",
                                                         "rel", "target"})
# Python's html.parser keeps a carriage return and a NUL that a browser's reading turns into a line feed and drops
# or replaces: core/crates/mm-content/tests/sanitize.rs pins those.
plain_text = st.text(st.characters(exclude_characters="\r\0"), max_size=10)


def _value(name):
    if name in URL_ATTRIBUTES:
        return st.sampled_from(URLS) | plain_text
    if name == "style":
        return css_text.map(lambda css: css.replace("\r", "").replace("\0", ""))
    return st.sampled_from(["red", "100", "center", "expression(1)", "javascript:x", "/*", "&{", "x" * 3000]) | plain_text


@st.composite
def attributes(draw):
    names = draw(st.lists(st.sampled_from(ATTRIBUTE_NAMES), unique=True, max_size=4))
    return "".join(f' {name}="{escape(draw(_value(name)))}"' for name in names)


def _element(tag, attributes, content):
    return f"<{tag}{attributes}>" if tag.lower() in VOIDS else f"<{tag}{attributes}>{content}</{tag}>"


texts = plain_text.map(lambda text: escape(text, quote=False)) | st.sampled_from([
    "&amp;", "&lt;", "&gt;", "&quot;", "&#39;", "&copy;", "&#169;", "&#xA9;", "&nbsp;", "&eacute;", "wrote:", " ",
    "\n", "<!-- a comment -->", "<!DOCTYPE html>"])
raw_text = st.text(st.characters(exclude_characters="\r\0<"), max_size=12)
raw_elements = (
    st.builds(lambda tag, attributes, text: f"<{tag}{attributes}>{text}</{tag}>",
              st.sampled_from(RAW_TEXT), attributes(), raw_text)
    | st.builds(lambda attributes, css: f"<style{attributes}>{css}</style>",
                attributes(), sheets.map(lambda css: css.replace("<", "").replace("\r", "").replace("\0", ""))))
tags = st.sampled_from(CONTAINERS + VOIDS).flatmap(lambda tag: st.sampled_from([tag, tag.upper()]))
documents = st.recursive(
    texts | raw_elements,
    lambda inner: st.builds(_element, tags, attributes(), st.lists(inner, max_size=4).map("".join))
    | st.lists(inner, min_size=2, max_size=4).map("".join),
    max_leaves=24)


@settings(max_examples=2000, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(documents, st.booleans(), st.booleans())
def test_markup_both_read_alike_is_written_as_python_wrote_it(html, load_images, fold):
    assert_html_as_python_wrote_it(html, load_images, fold)


def test_every_character_reads_in_a_link_or_an_image_as_python_read_it():
    """Before its scheme, Python stripped controls and what `\\s` takes for a space."""
    probe = ('<a href="{c}https://e.example.test/">x</a><img src="{c}http://e.example.test/a.png">'
             '<img src="{c}data:image/png;base64,AA">')
    for batch in itertools.batched((c for c in every_character() if c not in "\r\0"), 1000):
        html = "".join(probe.format(c=escape(c)) for c in batch)
        if sanitizer.sanitize(html, True) != python_sanitize(html, True):
            for c in batch:
                assert_html_as_python_wrote_it(probe.format(c=escape(c)), True)


SOURCES_NAMING_SCHEMES = [
    ('<a href="{}">x</a>', "https://e.example.test/"), ('<a href="{}">x</a>', "http:x"),
    ('<a href="{}">x</a>', "mailto:ada@example.test"), ('<a href="{}">x</a>', "cid:x"),
    ('<img src="{}">', "https:x"), ('<img src="{}">', "cid:x"), ('<img src="{}">', "data:image/png;base64,AA"),
    ('<img src="{}">', "data:image/jpeg;x"), ('<img src="{}">', "data:image/webp;x"),
    ('<p title="{}">y</p>', "javascript:x"), ('<p title="{}">y</p>', "expression(x)"),
]


@pytest.mark.parametrize("load_images", [False, True])
def test_a_letter_in_a_scheme_the_sanitizer_allows_reads_as_python_read_it(load_images):
    near = near_letters()
    for tag, value in SOURCES_NAMING_SCHEMES:
        for written in written_otherwise(value, near):
            assert_html_as_python_wrote_it(tag.format(escape(written)), load_images)


@settings(max_examples=500, deadline=None)
@given(st.lists(st.sampled_from([_FOLD_OPEN, _FOLD_CLOSE, "<p>x</p>", "<details>", "y"]), max_size=6).map("".join))
def test_the_fold_comes_out_as_python_took_it_out(html):
    assert sanitizer.without_fold(html) == python_without_fold(html)


# ── the HTML sanitizer, on any markup at all ─────────────────────────────────────────────────────────────────────

# Pieces of a message meant to end, open or hide something, CSS's comment marks and square brackets among them.
HOSTILE = [
    "<script>", "</script>", "<SCRIPT>", "<script/>", "<style>", "</style>", "<style/>", "<title>", "</title>",
    "<textarea>", "</textarea>", "<xmp>", "</xmp>", "<iframe>", "</iframe>", "<noembed>", "</noembed>", "<noframes>",
    "<noscript>", "</noscript>", "<plaintext>", "<template>", "</template>", "<svg>", "</svg>", "<svg/>", "<math>",
    "</math>", "<foreignObject>", "<desc>", "<annotation-xml encoding=text/html>", "<mglyph>", "<object>",
    "</object>", "<embed>", "<head>", "</head>", "<select>", "<option>", "<form action=https://e.example.test/>",
    "<button>", "<input>", "<base href=https://e.example.test/>", "<link rel=stylesheet href=//e.example.test/s>",
    "<meta http-equiv=refresh content='0;url=https://e.example.test/'>", "<body onload=alert(1)>",
    "<p>", "</p>", "<b>", "</b>", "<div>", "</div>", "<blockquote>", "</blockquote>", "<details>", "<summary>",
    "<table>", "<td>", "<br>", "<br/>", "</br>", "<img>", "<hr/>", "<font>", "<a>", "</a>", "<blink>", "<o:p>",
    "<", "</", ">", "/>", "<a", "<img", "<p", " ", "\t", "\n", "\r", "\x0c", "\0", "/", "=", '"', "'", "`",
    "<!--", "-->", "--!>", "<!-->", "<!", "<?", "<![CDATA[", "]]>", "<!DOCTYPE html>",
    "&", "&amp;", "&lt;", "&gt;", "&quot;", "&#0;", "&#1;", "&#x3c;", "&#60", "&lt", "&#128;", "&#xD800;",
    "&#x110000;", "\xa0", "\u2028", "\ufeff", "\ufffd",
    " onclick=alert(1)", " ONERROR=alert(1)", " onmouseover='x'", " href=", " src=", " style=", " srcset=",
    " xlink:href=", " formaction=", " background=", " title=", " color=", " width=", " class=", " id=", " rel=",
    " target=",
    "javascript:alert(1)", "JaVaScRiPt:x", "java\tscript:x", "java&#9;script:x", "&#106;avascript:x", "vbscript:x",
    "data:text/html,x", "data:image/svg+xml,x", "data:image/png;base64,AA", " data:image/gif;x",
    "https://e.example.test/a.png", "http://e.example.test/", "HTTPS://e.example.test/", " https://e.example.test/",
    "cid:part1", "mailto:ada@example.test", "//e.example.test/", "http\u017f://e.example.test/",
    "\u3000https://e.example.test/", "expression(1)",
    "color:red", ";", ":", "{", "}", "(", ")", "position:fixed", "position:static", "width:100vh", "height:50%",
    "background:url(https://t.example.test/b.png)", "background:url(data:image/png;base64,AA)",
    "background:url(javascript:x)", "list-style-image:url(cid:x)", "url(", "\\", "\\75", "\\72 ", "\\6c", "\\28",
    "\\22", "\\27", "\\3c", "\\5c", "\\0", "\\D800", "\\/", "\\a", "@media screen", "@import", "@font-face",
    "@supports", "@layer", "p", ".a", "font-family:", "expression", "behavior:", "-moz-binding", "var(", "image-set(",
    "&{", "\u0130", "\u0131", "\u017f", "\u212a", "\xe9", "\U0001f600",
    "*", "/*", "*/", "\\*", "\\2a", "\\2f", "//**/*", "[", "]", "a[title=", "]{",
]
hostile = st.lists(st.sampled_from(HOSTILE) | st.text("abcxyz ABC\xe9\u0130\u017f\u3000*/[]", max_size=4),
                   max_size=40).map("".join)


def read_by_a_browser(html):
    """The tags in `html`, and the text of each <style>, as html5ever's tokenizer reads them."""
    read = []
    for kind, name, attrs in mailmind_leaves.html_tokens(html):
        if kind == "start":
            read.append(("start", name, tuple(attrs)))
        elif kind == "end":
            read.append(("end", name))
        elif kind == "comment":
            read.append(("comment", name))
        elif read and read[-1][:2] == ("start", "style"):
            read.append(("style text", name))
    return read


class _PythonReading(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.read = []

    def handle_starttag(self, tag, attrs):
        self.read.append(("start", tag, tuple((name, value or "") for name, value in attrs)))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        self.read.append(("end", tag))

    def handle_comment(self, data):
        self.read.append(("comment", data))

    def handle_data(self, data):
        if self.read and self.read[-1][:2] == ("start", "style"):
            self.read.append(("style text", data))
        elif self.read and self.read[-1][0] == "style text":
            self.read[-1] = ("style text", self.read[-1][1] + data)


def read_by_python(html):
    """The tags in `html`, and the text of each <style>, as Python's html.parser reads them."""
    reading = _PythonReading()
    reading.feed(html)
    reading.close()
    return reading.read


def scheme(url):
    """The scheme a browser reads at the start of `url`, lower-cased, or None for a URL relative to the page's."""
    url = url.strip("".join(map(chr, range(0x21)))).translate(dict.fromkeys(map(ord, "\t\n\r")))
    found = re.match(r"([A-Za-z][A-Za-z0-9+.-]*):", url)
    return found.group(1).lower() if found else None


def raster_image(url):
    url = url.strip("".join(map(chr, range(0x21))))
    return re.match(r"data:image/(png|jpe?g|gif|webp|bmp);", url, re.I | re.A) is not None


def assert_fetches_nothing_unasked(css, load_images):
    """Each url() a browser reads in `css` is written whole, and names a raster image inline, or, once images are
    asked for, one on the web."""
    for at in re.finditer(r"url\(", css, re.I | re.A):
        written = re.match(r'url\("([^"\\]*)"\)', css[at.start():])
        assert written, css
        url = written.group(1)
        assert raster_image(url) or (load_images and scheme(url) in ("http", "https")), css


def assert_reads_as_written(css):
    """A browser reads `css` with the structure it was written with: no comment mark, line break or backslash, and
    each string, brace, parenthesis and square bracket closed by its own match. Otherwise it could read a later value's
    string as CSS that no check saw."""
    assert "/*" not in css and "*/" not in css, css
    assert not set(css) & set("\n\r\f\\"), css
    unquoted = re.sub(r"\"[^\"]*\"|'[^']*'", "", css)
    assert not set(unquoted) & set("\"'"), css
    while (shorter := re.sub(r"\{[^{}()\[\]]*\}|\([^{}()\[\]]*\)|\[[^{}()\[\]]*\]", "", unquoted)) != unquoted:
        unquoted = shorter
    assert not set(unquoted) & set("{}()[]"), css


def assert_safe(read, load_images, fold):
    for token in read:
        assert token[0] != "comment"
        if token[0] == "style text":
            assert "<" not in token[1]
            assert_fetches_nothing_unasked(token[1], load_images)
            assert_reads_as_written(token[1])
        if token[0] != "start":
            continue
        _, tag, attrs = token
        attrs = dict(attrs)
        assert tag in ALLOWED or tag == "style", tag
        allowed = set() if tag == "style" else ATTRS["*"] | ATTRS.get(tag, set()) | {"style"}
        if tag == "a" and "href" in attrs:
            allowed |= {"rel", "target"}
            assert (attrs.get("rel"), attrs.get("target")) == ("noopener noreferrer nofollow", "_blank")
        if tag == "details" and fold and "class" in attrs:
            allowed |= {"class"}
            assert attrs["class"] == "mm-quote"
        assert set(attrs) <= allowed, (tag, attrs)
        assert not any(name.startswith("on") for name in attrs)
        if "href" in attrs:
            assert scheme(attrs["href"]) in (None, "http", "https", "mailto", "cid"), attrs
        if "src" in attrs:
            assert load_images, attrs
            assert scheme(attrs["src"]) in (None, "http", "https", "cid") or raster_image(attrs["src"]), attrs
        if "style" in attrs:
            assert_fetches_nothing_unasked(attrs["style"], load_images)
            assert_reads_as_written(attrs["style"])


@settings(max_examples=2000, deadline=None)
@given(hostile, st.booleans(), st.booleans())
def test_whatever_a_message_holds_what_is_written_is_safe_and_reads_back_to_itself(html, load_images, fold):
    """Read back by a browser's tokenizer and by Python's parser alike: tags and attributes from the allowlist, no
    handler, no comment, no URL with a scheme the sanitizer doesn't allow, no remote image or url() unless images were
    asked for, and no style sheet a `<` could end. Sanitized again, it comes back as it went, the fold as it reads
    in mail going out. Near the caps, writing a url() in its quoted form can take a value past its cap, so the pieces
    keep well under them."""
    out, _ = sanitizer.sanitize(html, load_images, fold)
    read = read_by_a_browser(out)
    assert read == read_by_python(out)
    assert_safe(read, load_images, fold)
    plain_fold = _FOLD_OPEN.replace(' class="mm-quote"', "")
    assert sanitizer.sanitize(out, load_images, fold=False) == (out.replace(_FOLD_OPEN, plain_fold), NOTHING_BLOCKED)


# ── the edges ────────────────────────────────────────────────────────────────────────────────────────────────────

def test_nothing_is_sanitized_to_nothing():
    assert sanitizer.sanitize(None) == sanitizer.sanitize("") == ("", NOTHING_BLOCKED)
    assert sanitizer.sanitize_inline_style(None, Ctx(False)) == sanitizer.sanitize_css_stylesheet(None, Ctx(False)) == ""


def test_a_blocked_url_adds_to_what_the_caller_counted_as_python_did():
    """A remote url() counts once for each declaration it drops; a caller that never counted starts at nought."""
    css = "background:url(https://t.example.test/a.png),url(https://t.example.test/b.png)"
    for sheet in (sanitizer.sanitize_css_stylesheet, python_sanitize_css_stylesheet):
        ctx = SimpleNamespace(load_images=False)
        sheet(f"p{{{css}}}q{{{css};list-style:url(http://t.example.test/c.png)}}", ctx)
        assert ctx.blocked_css_urls == 3
    assert sanitizer.sanitize(f'<p style="{css}">x</p><style>p{{{css}}}</style>')[1] == \
        python_sanitize(f'<p style="{css}">x</p><style>p{{{css}}}</style>')[1] == \
        {**NOTHING_BLOCKED, "blocked_css_urls": 2}


def test_the_tokens_read_back_are_the_ones_a_browser_reads():
    assert mailmind_leaves.html_tokens('<P Title="a &amp; b">x<!--c--></p><br/><style>a<b</style>') == [
        ("start", "p", [("title", "a & b")]), ("text", "x", []), ("comment", "c", []), ("end", "p", []),
        ("start", "br", []), ("start", "style", []), ("text", "a<b", []), ("end", "style", []),
    ]


@pytest.mark.parametrize("html", [
    "<p title='caf\udce9'>caf\udce9</p>",
    "<a href='https://e.example.test/\udce9'>\ud83d\ude00</a>",
    "<p style='font-family:caf\udce9'>x</p><style>p{font-family:\udce9}</style>",
    "<img src='data:image/png;\udc80'>",
], ids=ascii)
@pytest.mark.parametrize("load_images", [False, True])
def test_a_lone_surrogate_reads_as_python_read_it(html, load_images):
    """An undecodable byte the email package carries as a lone surrogate reads as U+FFFD, one for one, which nothing
    the sanitizer asks of a character tells apart from it."""
    assert_html_as_python_wrote_it(html, load_images)


def test_an_escape_naming_a_surrogate_is_written_as_the_replacement_character():
    """Python wrote the lone surrogate itself, which the page could never have been sent."""
    assert python_sanitize_inline_style("font-family:\\D800", Ctx(False)) == "font-family:\ud800"
    assert sanitizer.sanitize_inline_style("font-family:\\D800", Ctx(False)) == "font-family:\ufffd"


# CSS that Python wrote so that a browser read it otherwise than Python checked it: up to where a later value's string
# ended, and the rest of that string as CSS. In Chromium each made an element position:fixed.
READ_OTHERWISE = [
    # An escape decodes to a comment opener; a later value's string closes it.
    'font-family:\\/\\*;font-family:"\\*\\/;position:fixed;top:0;left:0;width:100px;"',
    'p{font-family:\\/\\*}q{font-family:"\\*\\/}*{position:fixed;top:0}"}',
    # Taking a comment out joins a slash and a star.
    'font-family:a//**/*;font-family:"*/;position:fixed;top:0;x:"',
    'background:ur//**/*;font-family:"*/l(https://t.example.test/x);x:"',
    'p{font-family:a//**/*}q{font-family:"*/}*{position:fixed;top:0}"}',
    # An at-rule's condition may hold one.
    '@media /*{p{font-family:"*/{*{position:fixed}}"}}',
    # A line break ends a selector's string there.
    'a[title="x\n"]{color:red}q{font-family:"]{}*{position:fixed}"}',
    # A value opens a square bracket and a selector closes it, so the next selector is read as declarations.
    'p{font-family:[}a]{color:red}x;position:fixed;top:0;y{color:blue}',
    'p{color:red}a[{color:red}b]{color:red}x;position:fixed;top:0;y{color:blue}',
]


@pytest.mark.parametrize("css", READ_OTHERWISE)
def test_css_a_browser_would_read_otherwise_is_written_as_the_oracle_writes_it(css):
    assert_css_as_python_wrote_it(css, load_images=False)


@pytest.mark.parametrize("css", READ_OTHERWISE)
def test_no_written_css_opens_a_comment(css):
    """Nor reads otherwise: what is written in its place is read as it was written, `position:fixed` inside a string."""
    for entry in (sanitizer.sanitize_inline_style, sanitizer.sanitize_css_stylesheet):
        written = entry(css, Ctx(False))
        assert_reads_as_written(written)
        assert "position" not in re.sub(r"\"[^\"]*\"|'[^']*'", "", written), written
