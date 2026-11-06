"""Calendar's wiring: the one place that binds its ports, to its own adapters and, until those contexts move, to the
legacy modules behind them. The legacy modules reach Calendar through here.
"""
from mailmind.calendar.adapters import ics_reader as ics
from mailmind.calendar.adapters.sqlite import StoredInvites
from mailmind.calendar.application.agenda import Agenda
from mailmind.calendar.application.invites import Invites
from mailmind.calendar.application.rsvp import Rsvp
from mailmind.mailbox.domain import roles
from mailmind.platform.workers import gates
from mailmind.compose.api import build_message, reference_chain

__all__ = ["agenda", "attach", "ics", "invites", "rsvp"]

_server = None


def attach(server):
    """Name the running server module, the one that says a connection's trouble without its details."""
    global _server
    _server = server


class _Folders:
    def role_by_name(self, folder):
        return roles.role_by_name(folder)


class _Messages:
    """A message and its account, until the Mailbox and Accounts hold them."""
    def message(self, pk):
        from mailmind.bootstrap.mailbox import store
        c = store.conn()
        try:
            return store.get_message(c, pk)
        finally:
            c.close()

    def account(self, email):
        from mailmind.bootstrap import accounts
        return accounts.get(email)

    def fetched(self, account, pk):
        import ingest
        return gates.run(account, lambda: ingest.fetch_body(pk), priority=gates.Priority.INTERACTIVE) or {}

    def quiet_error(self, e):
        return _server._sanitize_conn_error(e)


class _Send:
    """Through Compose's outbox, until Compose holds it."""
    def build(self, spec, original):
        return build_message({
            **spec, "in_reply_to": original["message_id"],
            "references": reference_chain(original["message_id"], original["refs"], original["in_reply_to"])})

    def enqueue(self, built, meta):
        from mailmind.bootstrap import compose
        return compose.outbox.enqueue(built, meta)


_stored = StoredInvites(_Folders())
invites = Invites(_stored, ics)
agenda = Agenda(_stored, ics)
rsvp = Rsvp(invites, _stored, _Messages(), _Send())
