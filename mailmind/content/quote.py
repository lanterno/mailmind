"""The part of a reply or forward you typed, and the quote that comes after it."""
import html as _html


def html_escape(s):
    return _html.escape(s or "")


def own_html(new_text):
    """The HTML a reply or forward opens with: what you typed, before the quote."""
    return f"<div>{html_escape(new_text or '').replace(chr(10), '<br>')}</div><br>"


def quote_tail(new_text, text, html):
    """(tail_text|None, tail_html|None): what reply_bodies or forward_bodies added after what
    you typed, kept so a reopened draft can send its quote again without quoting twice."""
    own = new_text or ""
    tail = text[len(own):] if text is not None and text.startswith(own) else None
    head = own_html(own)
    tail_h = html[len(head):] if html and html.startswith(head) else None
    return (tail or None), (tail_h or None)
