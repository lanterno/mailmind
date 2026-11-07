"""What a request to send or save comes to, before it is a message: its text and HTML, how much of that you typed and how much
is the quote, and its threading. Reading the original is the use case's, which hands over what it found."""
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from mailmind.compose.domain.attachments import Attachment
from mailmind.compose.domain.builder import Composed, reference_chain, thread_ids
from mailmind.compose.domain.forward import forward_bodies
from mailmind.compose.domain.reply import Original, reply_bodies
from mailmind.compose.domain.resume import lf
from mailmind.content import sanitize
from mailmind.content.quote import own_html, quote_tail
from mailmind.kernel.row import Row


@dataclass(frozen=True)
class Body:
    own: str                                # what you typed, before any quote is added
    text: str                               # that and the quote
    html: str | None = None
    tail_text: str | None = None
    tail_html: str | None = None
    in_reply_to: str | None = None
    references: list[str] | None = None


def as_typed(text: str) -> Body:
    return Body(text, text)


def tail_of(resume: Mapping[str, Any]) -> str:
    """The quote or forwarded section a reopened draft carries."""
    return lf(str(resume.get("tail_text") or ""))


def as_drafted(text: str, tail: str, recorded: Row | None, in_reply_to: object, references: object) -> Body:
    """A reopened draft goes out as drafted: your text, the quote it already carried and its own threading headers. The
    original is never fetched, quoted again or attached again. `recorded` is what the draft's newest save holds of its
    quote. Its HTML goes only where that vouches for this exact quote: built and sanitized by us at that save, it isn't
    sanitized again."""
    own = lf(text)
    html = tail_html = None
    if tail and recorded and recorded["tail_html"] and lf(recorded["tail_text"] or "") == tail:
        tail_html = sanitize.without_fold(recorded["tail_html"])
        html = own_html(own) + tail_html
    irt, refs = thread_ids(in_reply_to, references)
    return Body(own, own + tail, html, tail or None, tail_html, irt, refs)


def named_original(named: Row | None, in_reply_to: str) -> Row | None:
    """The row a reopened reply names by pk, if it is the message its own In-Reply-To says: a pk may have been renumbered."""
    return named if named and named["message_id"] == in_reply_to else None


def _original(row: Row, parsed: Mapping[str, Any]) -> Original:
    return {"from_name": row["from_name"], "from_addr": row["from_addr"], "subject": row["subject"], "date": parsed.get("date"),
            "to": parsed.get("to"), "cc": parsed.get("cc"), "text": parsed.get("text"), "html": parsed.get("html")}


def as_reply(own: str, original: Row, parsed: Mapping[str, Any]) -> Body:
    """`original` is the message's row, `parsed` its body as far as it could be read."""
    text, html = reply_bodies(own, _original(original, parsed))
    tail_text, tail_html = quote_tail(own, text, html)
    return Body(own, text, html, tail_text, tail_html, original["message_id"],
                reference_chain(original["message_id"], original["refs"], original["in_reply_to"]))


def as_forward(own: str, original: Row, parsed: Mapping[str, Any]) -> Body:
    """A forward starts a thread of its own, so it names no original."""
    text, html = forward_bodies(own, _original(original, parsed))
    tail_text, tail_html = quote_tail(own, text, html)
    return Body(own, text, html, tail_text, tail_html)


def composed(acct: Mapping[str, Any], body: Mapping[str, Any], made: Body, attachments: list[Attachment],
             forwarded: list[Attachment]) -> Composed:
    """`acct` is the sending account, `body` the request and `forwarded` those of `attachments` a forward added. From is only
    ever the account: nothing in the request names it. `own` and `tail` are recorded by a save, and building ignores them."""
    return {"account": acct["email"], "account_name": acct.get("alias") or acct["email"],
            "to": body.get("to") or [], "cc": body.get("cc") or [], "bcc": body.get("bcc") or [],
            "subject": body.get("subject") or "", "text": made.text, "html": made.html, "attachments": attachments,
            "in_reply_to": made.in_reply_to, "references": made.references,
            "own": made.own, "tail": {"text": made.tail_text, "html": made.tail_html}, "forwarded": forwarded}
