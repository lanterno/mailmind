"""Calendar's wiring: the one place that binds its ports, to its own adapters and, until those contexts move, to the
legacy modules behind them. The legacy modules reach Calendar through here.
"""
from mailmind.calendar.adapters import ics_reader as ics
from mailmind.calendar.adapters.sqlite import StoredInvites
from mailmind.calendar.application.agenda import Agenda
from mailmind.calendar.application.invites import Invites
from mailmind.mailbox.domain import roles

__all__ = ["agenda", "ics", "invites"]


class _Folders:
    def role_by_name(self, folder):
        return roles.role_by_name(folder)


_stored = StoredInvites(_Folders())
invites = Invites(_stored, ics)
agenda = Agenda(_stored, ics)
