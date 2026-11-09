"""What the store is handed when the app is wired, held in one place so every module of the store reads the same binding."""
from typing import Any


_index: Any = None                               # what keeps the search index up to date: bound by mailmind.bootstrap.mailbox
_merged: Any = None                              # who keeps state under a thread id: bound by mailmind.bootstrap.mailbox


def bind_index(index):
    global _index
    _index = index


def bind_merges(handler):
    """`handler(c, event)` hears each ConversationsMerged, on the connection whose transaction made it. If it raises,
    the merge doesn't happen: the caller's transaction never commits."""
    global _merged
    _merged = handler
