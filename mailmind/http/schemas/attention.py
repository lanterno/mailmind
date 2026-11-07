"""What the page asks of Attention, and what it answers."""
from typing import Literal

from mailmind.http.schemas.common import Answer, Flag, Number, RequestBody


class NamesAThread(RequestBody):
    """A thread, by its account and id, or by a message of it (`Threads.target`)."""
    account: str | None = None
    thread_id: str | None = None
    pk: Number | None = None


class NamesThreadOrMessages(NamesAThread):
    """A whole thread, by its account and id, else the messages named by pk (`snooze.named_thread`, `named_pks`)."""
    pks: list[Number] | None = None


class SnoozeBody(NamesThreadOrMessages):
    preset: str | None = None               # unknown to the use case is its `bad snooze time`
    until: Number | str | None = None       # seconds, or an ISO time
    undo_group: str | None = None


class MuteBody(NamesAThread):
    undo_group: str | None = None


class ReplyLaterBody(NamesAThread):
    on: Flag | None = None                  # left out, it pins


class Snoozed(Answer):
    ok: Literal[True]
    count: int
    until: int


class Unsnoozed(Answer):
    ok: Literal[True]
    count: int


class OnThread(Answer):
    ok: Literal[True]
    thread_id: str


class RepliedLater(OnThread):
    on: bool
