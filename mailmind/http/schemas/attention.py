"""What the page asks of Attention, and what it answers."""
from typing import Literal

from mailmind.http.schemas.common import ACopy, Answer, Flag, NamesAMessage, Number
from mailmind.kernel.snoozed import SnoozePreset


class NamesAThread(NamesAMessage):
    """A thread, by its account and id, or by a message of it (`Threads.target`), its key and the folder of the copy the
    page has."""
    account: str | None = None
    thread_id: str | None = None


class NamesThreadOrMessages(NamesAThread):
    """A whole thread, by its account and id, else the copies named by key and folder (`snooze.named_thread`, `named_pks`):
    `copies`, or the one `key` and `folder` name."""
    copies: list[ACopy] | None = None


class SnoozeBody(NamesThreadOrMessages):
    preset: SnoozePreset | None = None
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
