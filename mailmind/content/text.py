"""A message's text: what a list shows of it, what search indexes, what the AI reads."""
import html
import re

from mailmind.content.mime import AnyMessage, Parsed, decoded, holds_no_parts, walk

BODY_CHARS = 3000

_TAG = re.compile(r"<[^>]+>")
# Drop the *contents* of non-text elements before tag-stripping, else CSS rules,
# <script> JSON-LD and <head> metadata leak into the "plain text" (and snippets).
_NOTEXT = re.compile(r"(?is)<(script|style|head|title)\b[^>]*>.*?</\1>")
_COMMENT = re.compile(r"(?s)<!--.*?-->")
# Invisible "preheader padding" newsletters stuff after the hook line (zero-width spaces,
# combining grapheme joiner, soft hyphen, BOM, bidi marks) — strip for a clean preview.
INVISIBLE = re.compile("[​‌‍⁠﻿͏­‎‏]")
_FTS_CAP = 100_000  # bound a single body's indexed text


def snippet_of(body_text: str | None) -> str:
    """A clean one-paragraph preview for the list/river: decode stray entities, drop invisible
    padding (incl. &zwnj;/&nbsp; spacers once decoded), collapse whitespace."""
    return " ".join(INVISIBLE.sub("", html.unescape(body_text or "")).split())[:280]


def strip_jsonld(t: str | None) -> str:
    """Some senders embed a JSON-LD block (schema.org structured data) in their text part.
    Remove each balanced [..]/{..} block carrying an @context, so previews read as prose."""
    out = t or ""
    guard = 0
    while "@context" in out and guard < 12:
        guard += 1
        idx = out.find("@context")
        start = max(out.rfind("[", 0, idx), out.rfind("{", 0, idx))
        if start < 0:
            break
        j = start - 1                      # extend back over a wrapping opener (e.g. the [ before {)
        while j >= 0 and out[j] in "[{ \t\r\n":
            if out[j] in "[{":
                start = j
            j -= 1
        depth = 0
        end = -1
        for i in range(start, len(out)):
            if out[i] in "[{":
                depth += 1
            elif out[i] in "]}":
                depth -= 1
                if depth == 0:
                    end = i
                    break
        if end < 0:
            break
        out = out[:start] + " " + out[end + 1:]
    return out


def body_text_of(parsed: Parsed) -> str:
    """Plain-text body for indexing: prefer text/plain, else strip tags from html."""
    t = parsed.get("text")
    marked_up = parsed.get("html")
    if not t and marked_up:
        h = _COMMENT.sub(" ", marked_up)
        h = _NOTEXT.sub(" ", h)
        t = html.unescape(_TAG.sub(" ", h))
    return strip_jsonld(t or "")[:_FTS_CAP]


def text_body(msg: AnyMessage) -> str:
    plain, htmltxt = None, None
    for part in walk(msg):
        container = part.get_content_maintype() == "multipart" and not holds_no_parts(part)
        if container or part.get_filename():
            continue
        try:
            payload = decoded(part)
            if payload is None:
                continue
            text = payload.decode(part.get_content_charset() or "utf-8", "replace")
        except Exception:
            continue
        ct = "text/plain" if holds_no_parts(part) else part.get_content_type()
        if ct == "text/plain" and plain is None:
            plain = text
        elif ct == "text/html" and htmltxt is None:
            htmltxt = text
    body = plain
    if body is None and htmltxt is not None:
        body = html_to_snippet_text(htmltxt)
    body = INVISIBLE.sub("", (body or "").replace("\r\n", "\n").replace("\r", "\n"))
    body = re.sub(r"[ \t\u00a0]+", " ", body).strip()
    body = re.sub(r"\n\s*\n+", "\n\n", body)
    return body[:BODY_CHARS]


# non-content blocks whose TEXT must never leak into a preview (a bare tag-strip
# leaves the CSS/JS body between the tags — the "ReadMsgBody{width:100%}" bug)
_NONCONTENT_RE = re.compile(r"(?is)<(script|style|head|title|noscript)\b[^>]*>.*?</\1\s*>")
_COMMENT_RE = re.compile(r"(?is)<!--.*?-->")


def html_to_snippet_text(htmltxt: str) -> str:
    h = _NONCONTENT_RE.sub(" ", htmltxt)
    h = _COMMENT_RE.sub(" ", h)
    return html.unescape(re.sub(r"<[^>]+>", " ", h))
