"""A reply's body: what you typed, then the original quoted. An HTML original goes through the
sanitizer's allowlist first, so a hostile message can't put active content into what you send."""
import re

from mailmind.content import sanitize
from mailmind.content.quote import html_escape, own_html


def _attribution(orig):
    who = orig.get("from_name") or orig.get("from_addr") or "someone"
    addr = orig.get("from_addr") or ""
    date = orig.get("date") or ""
    tail = f" <{addr}>" if addr and addr != who else ""
    return f"On {date}, {who}{tail} wrote:".strip()


def _quote_plain(text):
    return "\n".join("> " + ln for ln in (text or "").splitlines()) or ">"


def plain_from_html(html_str):
    """Crude HTML->text fallback when an original has no text/plain part."""
    safe, _ = sanitize.sanitize(html_str or "", load_images=False, fold=False)
    txt = re.sub(r"(?is)<br\s*/?>", "\n", safe)
    txt = re.sub(r"(?is)</p\s*>", "\n\n", txt)
    txt = re.sub(r"(?s)<[^>]+>", "", txt)
    import html as _h
    return _h.unescape(txt).strip()


def reply_bodies(new_text, orig):
    """(text, html|None) for a reply. orig: {from_name,from_addr,date,text,html}."""
    attr = _attribution(orig)
    o_text = orig.get("text") or (plain_from_html(orig.get("html")) if orig.get("html") else "")
    text = f"{new_text or ''}\n\n{attr}\n{_quote_plain(o_text)}\n"
    if orig.get("html"):
        safe, _ = sanitize.sanitize(orig["html"], load_images=False, fold=False)
        html_body = (
            own_html(new_text) + f"<div>{html_escape(attr)}</div>"
            f'<blockquote type="cite" style="margin:0 0 0 .8ex;'
            f'border-left:2px solid #ccc;padding-left:1ex">{safe}</blockquote>')
        return text, html_body
    return text, None
