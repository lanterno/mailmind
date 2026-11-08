"""What a person typed in Markdown, as the HTML that goes out beside it, on Python alone. A subset that reads well in mail:
paragraphs, *emphasis* and **strong**, [links](https://example.test), lists, > quotes, `code` and fences, and # headings. A
line break stays one, as it does in a message. What it doesn't know stays as typed, escaped, so no HTML a person typed gets
through, and what comes out goes through the sanitizer all the same.

`render` answers None for text with no Markdown in it, which goes out as plain text. A bare address is no Markdown, and neither
is a `>` line, which plain mail has always quoted with: each becomes a link or a quote only in a message that has HTML anyway.
Each span gives up at the first thing it can't close and bounds what it looks
ahead at, so the time this takes follows the length of the text."""
from html import escape
import re
import string

from mailmind.content import sanitize

MAX_TEXT = 200_000           # past this it goes out as typed: no message is this much writing
MAX_DEPTH = 8                # quotes and lists inside each other, past which a line is a paragraph
_MAX_SPAN_DEPTH = 4          # emphasis inside emphasis, past which it is text
_MAX_LINK_TEXT = 2000

_FENCE = re.compile(r" {0,3}(`{3,}|~{3,})(.*)$")
_HASHES = re.compile(r" {0,3}(#{1,6})[ \t]+")
_QUOTE = re.compile(r" {0,3}> ?")
_ITEM = re.compile(r"( {0,3})([-*+]|(\d{1,9})[.)]) +(?=\S)")

_SPECIAL = re.compile(r"[\\`*_\[<\n]|https?://", re.I)
_BACKTICKS = re.compile(r"`+")
_PUNCTUATION = frozenset(string.punctuation)
_LINK_TARGET = re.compile(r"\(\s*<?((?:https?:|mailto:)[^\s<>()]*(?:\([^\s<>()]*\)[^\s<>()]*)*)>?(?:\s+\"[^\"\n]*\")?\s*\)", re.I)
_ANGLED = re.compile(r"<((?:https?:|mailto:)[^\s<>]{1,2048})>", re.I)
_BARE_URL = re.compile(r"https?://[^\s<>\"']{1,2048}", re.I)


def _text(s: str) -> str:
    return escape(s, quote=False)


def _closing_fence(lines: list[str], at: int) -> int | None:
    """The line that closes a fence opening on line `at`, or None. A fence that never closes is no fence, so one stray run of
    backticks can't swallow the rest of a message."""
    m = _FENCE.match(lines[at])
    if m is None or (m[1][0] == "`" and "`" in m[2]):
        return None
    for j in range(at + 1, len(lines)):
        t = lines[j].strip()
        if len(t) >= len(m[1]) and not t.strip(m[1][0]):
            return j
    return None


def _title(line: str) -> tuple[int, str] | None:
    """(its level, its words) for a heading line."""
    m = _HASHES.match(line)
    if m is None:
        return None
    title = line[m.end():].rstrip()
    bare = title.rstrip("#")
    if bare != title and (not bare or bare[-1] in " \t"):        # a closing run of hashes
        title = bare.rstrip()
    return (len(m[1]), title) if title else None


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _opens(s: str, at: int, width: int) -> bool:
    after, before = s[at + width:at + width + 1], s[at - 1:at] if at else ""
    return bool(after) and not after.isspace() and not before.isalnum()


def _closes(s: str, at: int, width: int) -> bool:
    return not s[at - 1].isspace() and not s[at + width:at + width + 1].isalnum()


def _closer(s: str, start: int, mark: str) -> int | None:
    """Where the emphasis `mark` opened before `start` closes. A run longer than the mark is taken from its far end, so
    ***both*** comes apart, and a single mark goes past a double one."""
    ch, width = mark[0], len(mark)
    at = start
    while (at := s.find(mark, at)) >= 0:
        if width == 1 and s[at + 1:at + 2] == ch:
            while s[at:at + 1] == ch:
                at += 1
            continue
        if width == 2:
            while s[at + 2:at + 3] == ch:
                at += 1
        if at > start and _closes(s, at, width):
            return at
        at += width
    return None


class _Render:
    """One text's rendering. `formatted` says whether anything in it was Markdown."""

    def __init__(self) -> None:
        self.formatted = False

    def blocks(self, lines: list[str], depth: int) -> str:
        out: list[str] = []
        i, n = 0, len(lines)
        while i < n:
            line = lines[i]
            if not line.strip():
                i += 1
            elif (end := _closing_fence(lines, i)) is not None:
                self.formatted = True
                out.append(f"<pre><code>{_text(chr(10).join(lines[i + 1:end]))}</code></pre>")
                i = end + 1
            elif title := _title(line):
                self.formatted = True
                out.append(f"<h{title[0]}>{self.spans(title[1])}</h{title[0]}>")
                i += 1
            elif depth < MAX_DEPTH and _QUOTE.match(line):
                i, quote = self._quote(lines, i, depth)
                out.append(quote)
            elif depth < MAX_DEPTH and _ITEM.match(line):
                i, listed = self._list(lines, i, depth)
                out.append(listed)
            else:
                i, paragraph = self._paragraph(lines, i)
                out.append(paragraph)
        return "\n".join(out)

    def _quote(self, lines: list[str], i: int, depth: int) -> tuple[int, str]:
        inner: list[str] = []
        while i < len(lines) and (q := _QUOTE.match(lines[i])):
            inner.append(lines[i][q.end():])
            i += 1
        return i, f"<blockquote>{self.blocks(inner, depth + 1)}</blockquote>"

    def _item_lines(self, lines: list[str], i: int, need: int, first: str) -> tuple[int, list[str]]:
        """An item's lines: its first, then those indented under it, with the blank lines between them."""
        body, n = [first], len(lines)
        while i < n:
            if lines[i].strip():
                if _indent(lines[i]) < need:
                    break
                body.append(lines[i][need:])
                i += 1
                continue
            k = i
            while k < n and not lines[k].strip():
                k += 1
            if k == n or _indent(lines[k]) < need:
                break
            body.extend([""] * (k - i))
            i = k
        return i, body

    def _list(self, lines: list[str], i: int, depth: int) -> tuple[int, str]:
        first = _ITEM.match(lines[i])
        assert first is not None                  # blocks() has just matched it
        ordered, n = first[3] is not None, len(lines)
        items: list[str] = []
        while i < n and (m := _ITEM.match(lines[i])) and (m[3] is not None) == ordered:
            i, body = self._item_lines(lines, i + 1, min(m.end(), len(m[1]) + 2), lines[i][m.end():])
            inner = self.blocks(body, depth + 1)
            head, closed, rest = inner.partition("</p>")
            if "" not in body and closed and head.startswith("<p>"):      # a tight item's words stand in the item itself
                inner = head[3:] + rest
            items.append(f"<li>{inner}</li>")
            k = i
            while k < n and not lines[k].strip():
                k += 1
            if k < n and k != i and (next_one := _ITEM.match(lines[k])) and (next_one[3] is not None) == ordered:
                i = k
        self.formatted = True
        tag = "ol" if ordered else "ul"
        start = f' start="{int(first[3])}"' if ordered and int(first[3]) != 1 else ""
        return i, f"<{tag}{start}>{''.join(items)}</{tag}>"

    def _interrupts(self, lines: list[str], i: int) -> bool:
        """Whether line `i` ends the paragraph above it. A numbered line does only if it starts at 1: "1985. A vintage year"
        in the middle of a paragraph is a sentence."""
        line = lines[i]
        if _QUOTE.match(line) or _title(line) or _closing_fence(lines, i) is not None:
            return True
        item = _ITEM.match(line)
        return bool(item) and (item[3] is None or item[3] == "1")

    def _paragraph(self, lines: list[str], i: int) -> tuple[int, str]:
        para = [lines[i].strip()]
        i += 1
        while i < len(lines) and lines[i].strip() and not self._interrupts(lines, i):
            para.append(lines[i].strip())
            i += 1
        return i, f"<p>{self.spans(chr(10).join(para))}</p>"

    def spans(self, s: str, depth: int = 0, in_link: bool = False) -> str:
        out: list[str] = []
        pos, end = 0, len(s)
        dead: set[str] = set()                    # the marks with nothing left to close them in this text
        link_end, link = -1, None                 # the next "](" and what follows it, kept while it is still ahead
        while pos < end:
            found = _SPECIAL.search(s, pos)
            if found is None:
                out.append(_text(s[pos:]))
                break
            at = found.start()
            out.append(_text(s[pos:at]))
            ch = s[at]
            pos = at + 1
            if ch == "\n":
                out.append("<br>\n")
            elif ch == "\\":
                if s[pos:pos + 1] and s[pos] in _PUNCTUATION:
                    out.append(_text(s[pos]))
                    pos += 1
                else:
                    out.append("\\")
            elif ch == "<":
                angled = _ANGLED.match(s, at) if not in_link else None
                if angled:
                    out.append(self._link(angled[1], _text(angled[1])))
                    pos = angled.end()
                else:
                    out.append("&lt;")
            elif ch == "`":
                pos, span = self._code(s, at, dead)
                out.append(span)
            elif ch in "*_":
                pos, span = self._emphasis(s, at, dead, depth, in_link)
                out.append(span)
            elif ch == "[":
                if not in_link and link_end < at and "](" not in dead:
                    link_end = s.find("](", at + 1)
                    if link_end < 0:
                        dead.add("](")
                    else:
                        link = _LINK_TARGET.match(s, link_end + 1)
                if not in_link and link_end >= at + 2 and link_end - at <= _MAX_LINK_TEXT and link and "](" not in dead:
                    inner = self.spans(s[at + 1:link_end], depth + 1, True) if depth < _MAX_SPAN_DEPTH else _text(s[at + 1:link_end])
                    out.append(self._link(link[1], inner))
                    self.formatted = True
                    pos = link.end()
                else:
                    out.append("[")
            else:                                 # the start of a bare address
                pos, span = self._bare_url(s, at, in_link)
                out.append(span)
        return "".join(out)

    def _link(self, url: str, inner: str) -> str:
        return f'<a href="{escape(url, quote=True)}">{inner}</a>'

    def _code(self, s: str, at: int, dead: set[str]) -> tuple[int, str]:
        run = _BACKTICKS.match(s, at)
        assert run is not None                    # spans() stands on a backtick
        width, mark = len(run[0]), f"`{len(run[0])}"
        if mark not in dead:
            for closing in _BACKTICKS.finditer(s, run.end()):
                if len(closing[0]) == width:
                    code = s[run.end():closing.start()].replace("\n", " ")
                    if len(code) > 2 and code[0] == " " == code[-1] and code.strip():
                        code = code[1:-1]
                    self.formatted = True
                    return closing.end(), f"<code>{_text(code)}</code>"
            dead.add(mark)
        return run.end(), run[0]

    def _emphasis(self, s: str, at: int, dead: set[str], depth: int, in_link: bool) -> tuple[int, str]:
        ch = s[at]
        mark = ch * 2 if s[at + 1:at + 2] == ch else ch
        width = len(mark)
        if mark not in dead and _opens(s, at, width):
            close = _closer(s, at + width, mark)
            if close is None:
                dead.add(mark)
            else:
                body = s[at + width:close]
                inner = self.spans(body, depth + 1, in_link) if depth < _MAX_SPAN_DEPTH else _text(body)
                self.formatted = True
                tag = "strong" if width == 2 else "em"
                return close + width, f"<{tag}>{inner}</{tag}>"
        return at + 1, _text(ch)

    def _bare_url(self, s: str, at: int, in_link: bool) -> tuple[int, str]:
        m = _BARE_URL.match(s, at)
        if m is None or in_link or (at and s[at - 1].isalnum()):
            return at + 1, _text(s[at])
        url = m[0].rstrip(".,;:!?*_~")
        while url.endswith(")") and url.count(")") > url.count("("):
            url = url[:-1].rstrip(".,;:!?*_~")
        return at + len(url), self._link(url, _text(url))


def render(text: str | None) -> str | None:
    """The sanitized HTML of what a person typed, or None where there is no Markdown in it to render."""
    if not text or len(text) > MAX_TEXT:
        return None
    r = _Render()
    html = r.blocks(text.replace("\r\n", "\n").replace("\r", "\n").split("\n"), 0)
    if not r.formatted:
        return None
    return sanitize.sanitize(html, load_images=False, fold=False)[0]
