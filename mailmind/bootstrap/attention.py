"""Attention's wiring: the one place that binds its ports, to its own adapter and, until those contexts move, to the
legacy modules behind them. The legacy modules reach Attention through here.
"""
from mailmind.attention.adapters import sqlite as book
from mailmind.attention.application.followups import Clocked, FollowUps
from mailmind.attention.application.muting import Muting
from mailmind.attention.application.snoozing import Snoozing
from mailmind.attention.application.tasks import Tasks
from mailmind.attention.application.threads import Threads
from mailmind.bootstrap import mailbox
from mailmind.platform.db.uow import UnitOfWork

__all__ = ["book", "clocked", "followups", "muting", "snoozing", "tasks"]

class _Messages:
    def get(self, c, pk):
        from mailmind.bootstrap.mailbox import stored_mail
        return stored_mail.get_message(c, pk)

    def queue_flags(self, c, row, flags, mutation_id, when):
        from mailmind.bootstrap.mailbox import stored_mail
        return stored_mail.queue_flags(c, row, flags, mutation_id, when)

    def flags_queued(self):
        mailbox.filing.flags_queued()

    def copy_in_folder(self, c, key, folder):
        from mailmind.bootstrap.mailbox import stored_mail
        return stored_mail.copy_in_folder(c, key, folder)

    def set_local_flags(self, c, pk, **flags):
        from mailmind.bootstrap.mailbox import stored_mail
        return stored_mail.set_local_flags(c, pk, **flags)

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


_threads = Threads(_Messages(), _Accounts())
_uow = UnitOfWork()
snoozing = Snoozing(_uow, book, _Messages(), _Undo())
muting = Muting(_uow, book, _Messages(), _threads, _Undo())
followups = FollowUps(_uow, book, _threads, _Accounts())
clocked = Clocked(snoozing, followups)
tasks = Tasks(_uow, book, _Messages(), _Accounts())
