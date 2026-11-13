"""The allowlist HTML sanitizer for mail bodies, and the CSS sanitizer under it.

It is defence in depth. The real isolation when a message is shown is the reader's sandboxed frame (opaque origin, no
script) and its own strict Content-Security-Policy (docs/security.md). This is the backstop: it writes the message
again from an allowlist, and leaves out scripts, event handlers, dangerous URLs, embedded objects and, by default,
remote images, which would tell a sender the message was opened.

  clean, info = sanitize(html, load_images=False)
  info = {"blocked_images": int, "blocked_links": int, "blocked_css_urls": int}

`load_images=False` leaves remote <img src> and CSS url() out, and counts them, so a 1x1 tracker can't beacon the open;
the reader offers a per-message "Load images" that renders it again with True.

The Rust core sanitizes (`mm_content::sanitize`), reading the HTML with the tokenizer browsers' parsing follows, and
writes every tag, attribute and text itself, escaped. tests/unit/content/test_sanitize_in_rust.py holds it to the
Python sanitizer it replaced.
"""
from typing import Protocol

import mailmind_leaves

_CSS_SHEET_MAX = mailmind_leaves.CSS_SHEET_MAX            # <style> text read, in characters
_CSS_SHEET_RULES = mailmind_leaves.CSS_SHEET_RULES        # rules read in a sheet, nested ones included


class CssContext(Protocol):
    """What the CSS sanitizer reads of its caller and tells it: whether images load, and how many url() it blocked."""
    load_images: bool
    blocked_css_urls: int


def _count_blocked(ctx: CssContext, blocked: int) -> None:
    if blocked:
        ctx.blocked_css_urls = getattr(ctx, "blocked_css_urls", 0) + blocked


def sanitize_inline_style(value: str | None, ctx: CssContext) -> str:
    """One style="..." attribute's value, sanitized: 'p:v;p:v', or ''. Never raises."""
    css, blocked = mailmind_leaves.sanitize_inline_style(value or "", bool(ctx.load_images))
    _count_blocked(ctx, blocked)
    return css


def sanitize_css_stylesheet(css: str | None, ctx: CssContext) -> str:
    """A <style> element's text, sanitized: safe CSS, or ''. It holds no '<' or '>', so it can't end its <style>.
    Never raises."""
    sheet, blocked = mailmind_leaves.sanitize_css_stylesheet(css or "", bool(ctx.load_images))
    _count_blocked(ctx, blocked)
    return sheet


def without_fold(html: str) -> str:
    """`html`, sanitized before replies stopped carrying the fold, without it: a draft's stored
    quote. sanitize closes the fold at the very end of what it returns."""
    return mailmind_leaves.without_fold(html)


def sanitize(html: str | None, load_images: bool = False, fold: bool = True) -> tuple[str, dict[str, int]]:
    """Return (clean_html, info). Never raises on malformed input. `fold` is for reading: it puts a
    quoted chain behind a toggle the reader's stylesheet draws, which mail going out must not carry."""
    clean, images, links, css_urls = mailmind_leaves.sanitize_html(html or "", bool(load_images), bool(fold))
    return clean, {"blocked_images": images, "blocked_links": links, "blocked_css_urls": css_urls}
