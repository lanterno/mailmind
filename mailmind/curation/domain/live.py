"""INBOX mail as the server shows it right now, read for a sweep or for expiry. Mail is read live before anything moves,
so a stored row can't send the wrong message anywhere. `from` being a keyword, the base shapes are written as calls."""
from typing import TypedDict

LiveMail = TypedDict("LiveMail", {"uid": str, "from": str, "subject": str, "mid": str})
Audited = TypedDict("Audited", {"uid": str, "from": str, "subject": str, "bucket": str, "reason": str})


class Read(LiveMail):
    """With what a classifier wants from its headers."""
    name: str
    bulk: bool


class Classified(Read):
    bucket: str
    conf: str
    reason: str


class Aged(LiveMail):
    """With when it arrived, in epoch seconds, if that's known."""
    ts: float | None


class Expired(Aged):
    """Past the shelf life of `hours` its sender's rule gives it."""
    hours: int
