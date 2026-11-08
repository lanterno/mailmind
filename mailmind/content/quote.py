"""The part of a reply or forward you typed, and the quote that comes after it."""
import html as _html
from itertools import groupby

BLOCKQUOTE = '<blockquote type="cite" style="margin:0 0 0 .8ex;border-left:2px solid #ccc;padding-left:1ex">'


def html_escape(s: str | None) -> str:
    return _html.escape(s or "")


def own_html(new_text: str | None) -> str:
    """The HTML a reply or forward opens with: what you typed, before the quote."""
    return f"<div>{html_escape(new_text or '').replace(chr(10), '<br>')}</div><br>"


def quoted_text_html(tail: str) -> str:
    """A quote with no HTML of its own, for a message whose own words have some: line for line, its `>` lines as a quote."""
    out = []
    for quoted, lines in groupby(tail.strip("\n").split("\n"), lambda line: line.startswith(">")):
        inner = "<br>".join(html_escape(line[2:] if line.startswith("> ") else line[1:] if quoted else line) for line in lines)
        out.append(f"{BLOCKQUOTE}{inner}</blockquote>" if quoted else f"<div>{inner}</div>")
    return "".join(out)


def quote_tail(new_text: str | None, text: str | None, html: str | None) -> tuple[str | None, str | None]:
    """(tail_text|None, tail_html|None): what reply_bodies or forward_bodies added after what
    you typed, kept so a reopened draft can send its quote again without quoting twice."""
    own = new_text or ""
    tail = text[len(own):] if text is not None and text.startswith(own) else None
    head = own_html(own)
    tail_h = html[len(head):] if html and html.startswith(head) else None
    return (tail or None), (tail_h or None)
