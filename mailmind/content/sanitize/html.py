"""Allowlist HTML sanitizer for rendering email bodies — stdlib only.

This is DEFENSE-IN-DEPTH LAYER 3. The real isolation when rendering a message is
the client-side `sandbox=""` iframe (opaque origin, scripts disabled) + a strict
per-frame Content-Security-Policy. This scrubber is the backstop: it re-emits an
allowlisted subset of the HTML, dropping scripts, event handlers, dangerous URLs,
embedded objects, and (by default) remote content used for tracking.

  clean, info = sanitize(html, load_images=False)
  info = {"blocked_images": int, "blocked_links": int}

`load_images=False` strips remote <img src> (returns count) so a 1x1 tracker can't
beacon the open; the UI offers a per-message "Load images" that re-renders with True.
"""
from collections.abc import Iterator
from html.parser import HTMLParser
from html import escape
import re
from typing import Literal, Protocol

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
    if "\\" in emitted or "<" in emitted or ">" in emitted:
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


def sanitize_inline_style(value: str | None, ctx: CssContext) -> str:
    """Sanitize one style="..." attribute body. Returns safe 'p:v;p:v' (possibly '').
    Never raises. HARD 4096-char cap BEFORE any normalization (DoS defense)."""
    try:
        if not value or len(value) > _CSS_INLINE_MAX:
            return ""
        return _css_sanitize_declarations(value, ctx, _CSS_INLINE_DECLS)
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
    return sel


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
                    inner = _css_sanitize_rules(blk, ctx, depth + 1, counter)
                    if inner:
                        out.append("@%s %s{%s}" % (kw, pre, inner))
                # any other at-rule: drop
        except Exception:
            continue
    return "".join(out)


def sanitize_css_stylesheet(css: str | None, ctx: CssContext) -> str:
    """Sanitize <style> text content. Returns safe CSS (possibly ''). Guarantees no
    '<'/'>' in output (so it can't break out of <style>). Never raises."""
    try:
        if not css:
            return ""
        if len(css) > _CSS_SHEET_MAX:
            css = css[:_CSS_SHEET_MAX]
        result = _css_sanitize_rules(css, ctx, 0, [0])
        return result.replace("<", "").replace(">", "")
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
                css = sanitize_css_stylesheet("".join(self._style_buf), self)
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
                    css = sanitize_inline_style(val, self)
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
            css = sanitize_css_stylesheet("".join(self._style_buf), self)
            if css:
                self.out.append("<style>" + css + "</style>")
        out = "".join(self.out)
        if self._folded:
            out += _FOLD_CLOSE           # the fold wrapper opened mid-stream; close it at the end
        return out


def without_fold(html: str) -> str:
    """`html`, sanitized before replies stopped carrying the fold, without it: a draft's stored
    quote. sanitize closes the fold at the very end of what it returns."""
    head, opened, rest = html.partition(_FOLD_OPEN)
    if not opened:
        return html
    body, closed, after = rest.rpartition(_FOLD_CLOSE)
    return head + (body + after if closed else rest)


def sanitize(html: str | None, load_images: bool = False, fold: bool = True) -> tuple[str, dict[str, int]]:
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
