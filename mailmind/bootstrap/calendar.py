"""Calendar's wiring: the one place that binds its ports, to its own adapters and, until those contexts move, to the
legacy modules behind them. The legacy modules reach Calendar through here.

Loading this loads `icalendar`, through its adapter. The command line runs on Python alone and reads no invite, so nothing
it loads imports this at the top: the Mailbox's `_Reader` asks for it as a message opens, which only a route does.
"""
from mailmind.calendar.adapters import icalendar_acl
from mailmind.calendar.adapters.sqlite import StoredInvites
from mailmind.calendar.application.agenda import Agenda
from mailmind.calendar.application.invites import Invites
from mailmind.calendar.application.rsvp import Rsvp
from mailmind.mailbox.domain import roles
from mailmind.platform.db.uow import UnitOfWork
from mailmind.platform.workers import gates
from mailmind.compose.api import Built, MessageSpec, build_message, reference_chain
from mailmind.bootstrap import accounts

__all__ = ["agenda", "ics", "invites", "rsvp"]

class _Folders:
    def role_by_name(self, folder):
        return roles.role_by_name(folder)


class _Messages:
    """A message and its account, until the Mailbox and Accounts hold them."""
    def message(self, pk):
        from mailmind.bootstrap.mailbox import store
        with UnitOfWork().read() as c:
            return store.get_message(c, pk)

    def account(self, email):
        from mailmind.bootstrap import accounts
        return accounts.get(email)

    def fetched(self, account, pk):
        from mailmind.bootstrap.mailbox import ingest
        return gates.run(account, lambda: ingest.fetch_body(pk), priority=gates.Priority.INTERACTIVE) or {}

    def quiet_error(self, e):
        return accounts.quiet_error(e)


class _Send:
    """Through Compose's outbox, until Compose holds it."""
    def build(self, spec, original) -> Built:
        return build_message(MessageSpec(**{
            **spec, "in_reply_to": original["message_id"],
            "references": reference_chain(original["message_id"], original["refs"], original["in_reply_to"])}))

    def enqueue(self, built, meta):
        from mailmind.bootstrap import compose
        return compose.outbox.enqueue(built, meta)


_stored = StoredInvites(_Folders())
ics = icalendar_acl
invites = Invites(_stored, ics)
agenda = Agenda(_stored, ics)
rsvp = Rsvp(invites, _stored, _Messages(), _Send())
