"""A mail body as the document its frame loads, and an attachment's image as the document its preview loads.

The frame's look is its parent's to give, so this holds no style: changing a reading setting then asks nothing of the
server. `<base target="_blank">` is here for a click the parent hasn't caught yet: a sandbox with no popups stops a new
tab, so the frame stays on the message instead of navigating away.

`html` must have been through the sanitizer.
"""
from base64 import b64encode
from html import escape


def _document(inner: str) -> str:
    return f'<!doctype html><html lang="en"><head><meta charset="utf-8"><base target="_blank"></head><body>{inner}</body></html>'


def of_html(safe_html: str | None) -> str:
    return _document(safe_html or "")


def of_text(text: str | None) -> str:
    """Plain text, whole and escaped. Folding a quote away is the parent's, which knows how (`foldPlainQuote`)."""
    return _document(f"<pre>{escape(text or '(no content)')}</pre>")


def image_type(data: bytes) -> str | None:
    """Which of the four kinds of image a preview shows these bytes are, by how they begin and never by what the sender called
    them. An SVG is none of them: it is a document, and can hold a script."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def of_image(data: bytes, media_type: str) -> str:
    """One image and nothing else, as large as fits its frame. `media_type` is `image_type`'s. The bytes are in the document, since
    the policy on it fetches nothing."""
    return ('<!doctype html><html lang="en"><head><meta charset="utf-8"><style>html,body{height:100%;margin:0}'
            'body{display:flex;align-items:center;justify-content:center}img{max-width:100%;max-height:100%}</style></head>'
            f'<body><img alt="" src="data:{media_type};base64,{b64encode(data).decode("ascii")}"></body></html>')
