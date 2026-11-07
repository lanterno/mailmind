"""A mail body as the document its frame loads.

The frame's look is its parent's to give, so this holds no style: changing a reading setting then asks nothing of the
server. `<base target="_blank">` is here for a click the parent hasn't caught yet: a sandbox with no popups stops a new
tab, so the frame stays on the message instead of navigating away.

`html` must have been through the sanitizer.
"""
from html import escape


def _document(inner: str) -> str:
    return f'<!doctype html><html lang="en"><head><meta charset="utf-8"><base target="_blank"></head><body>{inner}</body></html>'


def of_html(safe_html: str | None) -> str:
    return _document(safe_html or "")


def of_text(text: str | None) -> str:
    """Plain text, whole and escaped. Folding a quote away is the parent's, which knows how (`foldPlainQuote`)."""
    return _document(f"<pre>{escape(text or '(no content)')}</pre>")
