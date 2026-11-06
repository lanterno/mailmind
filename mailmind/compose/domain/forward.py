"""A forward's body: what you typed, a banner, then the original. An HTML original goes through the
sanitizer's allowlist first, as a reply's does."""
from mailmind.compose.domain.reply import Original, plain_from_html
from mailmind.content import sanitize
from mailmind.content.quote import html_escape, own_html


def forward_bodies(new_text: str | None, orig: Original) -> tuple[str, str | None]:
    """(text, html|None) for a forward — banner + original (un-quoted)."""
    rows = [("From", f"{orig.get('from_name') or ''} <{orig.get('from_addr') or ''}>".strip()),
            ("Date", orig.get("date") or ""), ("Subject", orig.get("subject") or ""),
            ("To", orig.get("to") or "")]
    if cc := orig.get("cc"):
        rows.append(("Cc", cc))
    banner = "---------- Forwarded message ----------\n" + \
        "\n".join(f"{k}: {v}" for k, v in rows if v)
    html = orig.get("html")
    o_text = orig.get("text") or (plain_from_html(html) if html else "")
    text = f"{new_text or ''}\n\n{banner}\n\n{o_text}"
    if html:
        safe, _ = sanitize.sanitize(html, load_images=False, fold=False)
        banner_html = "<br>".join(html_escape(f"{k}: {v}") for k, v in rows if v)
        html_body = (
            own_html(new_text) + f"<div>---------- Forwarded message ----------<br>{banner_html}</div><hr>{safe}")
        return text, html_body
    return text, None
