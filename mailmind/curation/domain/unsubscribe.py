"""What a sender's mail says about leaving its list, what the way out is, and what trying it comes to. An attempt that
got somewhere is latched by the sender's key, so a repeat never fires again, and anything else stays retryable."""
import email
import re
import urllib.parse
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from mailmind.content import mime
from mailmind.content.mime import UnsubWay
from mailmind.kernel.row import Row

NO_METHOD: UnsubWay = {"method": "none", "https_uri": None, "mailto_uri": None, "mailto_to": None,
                       "mailto_subject": None, "mailto_body": None, "raw": ""}
TOKEN_LIFE = 600           # seconds a preview's confirm token stands
TOKEN_PURPOSE = "cleanup"
TERMINAL = ("unsubscribed_oneclick", "mailto_sent", "needs_browser")   # never fired twice; anything else stays retryable
PLAIN_ADDRESS = r"[^\s@<>,;:\"()\[\]\\]+@[^\s@<>,;:\"()\[\]\\]+"
SUBJECT_MAX = 200
TEXT_MAX = 1000


def preview_said(from_addr: str | None, info: UnsubWay) -> tuple[str, str, str | None]:
    """What a preview's confirm token stands for: what would go out, and where. A list that changes its way out since
    then needs a fresh look."""
    target = info.get("mailto_to") if info["method"] == "mailto" else info.get("https_uri")
    return (from_addr or "").lower(), info["method"], target


def offers_a_way(info: UnsubWay) -> bool:
    """Whether a message offers any way out at all. One that doesn't ends what was kept: the list has stopped offering one."""
    return info["method"] != "none"


def said(parsed: UnsubWay) -> UnsubWay:
    """What a message's headers say, with no way out said one way however they said it."""
    return parsed if offers_a_way(parsed) else NO_METHOD


def preview(info: UnsubWay) -> dict[str, str | None]:
    """What a person is shown before confirming: the way, and only where it goes."""
    shown: dict[str, str | None] = {"method": info["method"]}
    if info["method"] == "one-click" or info["method"] == "http-page":
        shown["https_host"] = urllib.parse.urlparse(info["https_uri"] or "").hostname
    if info["method"] == "mailto":
        shown["mailto_to"] = info.get("mailto_to")
    return shown


def attempt_key(from_addr: str) -> str:
    return "unsub:" + from_addr


@dataclass(frozen=True)
class Attempt:
    """What trying a way out came to: its `status`, which `method` it took, the server's answer to a one-click and a page
    for the person to open."""
    status: str
    method: str = "none"
    http_status: int | None = None
    browser_url: str | None = None

    @property
    def latched(self) -> bool:
        return self.status in TERMINAL


READ_ONLY = Attempt("read_only")       # sends nothing outside; not latched, so retryable


def not_tried(asked: bool) -> Attempt:
    """An unsubscribe that didn't go: nobody asked, or nobody showed a preview's good token. Block and sweep still stand."""
    return Attempt("no_token" if asked else "skipped")


def repeat_of(prior: Row | None, info: UnsubWay) -> Attempt | None:
    """What an attempt already latched answers, with nothing fired, or None if there's none. A page can be opened again."""
    if not prior or prior["status"] not in TERMINAL:
        return None
    return Attempt(prior["status"], prior["method"], None, info.get("https_uri") if prior["method"] == "http-page" else None)


def after_one_click(ok: bool, http_status: int | None) -> Attempt:
    return Attempt("unsubscribed_oneclick" if ok else "failed", "one-click", http_status)


def after_mailto(sent: bool) -> Attempt:
    return Attempt("mailto_sent" if sent else "failed", "mailto")


def without_sending(info: UnsubWay) -> Attempt:
    """A way that has nothing to send: a page is for the person to open, and otherwise there's no way at all."""
    if info["method"] == "http-page":
        return Attempt("needs_browser", "http-page", None, info.get("https_uri"))
    return Attempt("no_method", info["method"])


@dataclass(frozen=True)
class Mailto:
    """A mailto: way out as it may go: to one plain address, under a subject on one line, with a short text."""
    to: str
    subject: str
    text: str


def mailto_of(info: UnsubWay) -> Mailto | None:
    """What a list's mailto: asks to send, or None if it doesn't name one plain address, and then nothing is sent."""
    to = info.get("mailto_to") or ""
    if not re.fullmatch(PLAIN_ADDRESS, to):
        return None
    return Mailto(to, re.sub(r"[\r\n]", " ", (info.get("mailto_subject") or "unsubscribe"))[:SUBJECT_MAX],
                  re.sub(r"[\r\n]{3,}", "\n\n", (info.get("mailto_body") or "Please unsubscribe me."))[:TEXT_MAX])


def parse_list_unsub(raw_bytes: bytes | None) -> UnsubWay:
    """From a raw RFC822 message → the resolvable unsubscribe method (`mime.list_unsub`)."""
    try:
        msg = email.message_from_bytes(raw_bytes or b"")
    except Exception:
        return NO_METHOD
    return mime.list_unsub(mime.dec(msg.get("List-Unsubscribe")), mime.dec(msg.get("List-Unsubscribe-Post")))


def stored(rows: Iterable[Row], body_of: Callable[[int], bytes | None],
           remember: Callable[[int, UnsubWay], None]) -> UnsubWay | None:
    """What the sender's newest message that can say says, newest first through `rows`: from what ingest
    kept of its headers, else from its stored body. None if no row can say. `body_of(pk)` gives a stored
    body or None, and `remember(pk, parsed)` keeps what a body said."""
    for r in rows:
        m = r["list_unsub_method"]
        if m == "none":
            return NO_METHOD                            # cached authoritative none → stop (no re-fetch)
        if m:
            parsed = mime.list_unsub_stored(r["list_unsub_raw"])
            if offers_a_way(parsed):
                return parsed
        raw = body_of(r["pk"])
        if raw is None:
            continue                                    # body not fetched yet → can't resolve this row
        parsed = parse_list_unsub(raw)
        if not m:                                       # uncached → cache the resolution
            remember(r["pk"], parsed)
        return said(parsed)                             # newest with a body has no header → none
    return None
