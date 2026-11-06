"""Attention's wiring: the one place that binds its ports, to its own adapter and, until those contexts move, to the
legacy modules behind them. The legacy modules reach Attention through here.

What a change makes stale, and undo, go through the server module, which `attach` is handed rather than importing:
`python3 server.py` runs as __main__, and an import here would load a second copy with state of its own.
"""
from mailmind.attention.adapters import sqlite as book
from mailmind.attention.application.followups import Clocked, FollowUps
from mailmind.attention.application.muting import Muting
from mailmind.attention.application.snoozing import Snoozing
from mailmind.attention.application.threads import Threads
from mailmind.bootstrap import mailbox

__all__ = ["attach", "book", "clocked", "followups", "muting", "snoozing"]

_server = None


def attach(server):
    """Name the running server module."""
    global _server
    _server = server


class _Messages:
    def get(self, c, pk):
        from mailmind.bootstrap.mailbox import store
        return store.get_message(c, pk)

    def queue_flags(self, c, row, flags, mutation_id, when):
        from mailmind.bootstrap.mailbox import store
        return store.queue_flags(c, row, flags, mutation_id, when)

    def copy_in_folder(self, c, key, folder):
        from mailmind.bootstrap.mailbox import store
        return store.copy_in_folder(c, key, folder)

    def set_local_flags(self, c, pk, **flags):
        from mailmind.bootstrap.mailbox import store
        return store.set_local_flags(c, pk, **flags)

    def index(self, c, pk):
        from mailmind.bootstrap import discovery
        return discovery.index_message(c, pk)


class _Accounts:
    def emails(self):
        from mailmind.bootstrap import accounts
        return [a["email"] for a in accounts.accounts()]


class _Undo:
    def push(self, entry, group):
        mailbox.undo.push(entry, group)


class _Screen:
    """With no server running, nothing is on a screen and no queue's worker is waiting."""
    def stats_changed(self):
        if _server:
            mailbox.stats.invalidate()

    def flags_queued(self):
        mailbox.filing.flags_queued()


_threads = Threads(_Messages(), _Accounts())
snoozing = Snoozing(book, _Messages(), _Undo(), _Screen())
muting = Muting(book, _Messages(), _threads, _Undo(), _Screen())
followups = FollowUps(book, _threads, _Accounts(), _Screen())
clocked = Clocked(snoozing, followups)
