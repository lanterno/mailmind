"""Calendar's wiring: the one place that binds its ports, to its own adapters and, until those contexts move, to the
legacy modules behind them. The legacy modules reach Calendar through here.
"""
from mailmind.calendar.adapters import ics_reader
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

KINDS = ("stdlib", "icalendar")
_kind = "stdlib"
_reader = ics_reader


def use(kind):
    """Read invites through `kind` from now on: `ics_reader`, or the library one, which only front.build asks for, so it's
    imported here and only now (missing: fail now, not on the first invite)."""
    global _kind, _reader
    if kind not in KINDS:
        raise ValueError(f"no reader of invites {kind!r}")
    if kind == "icalendar":
        from mailmind.calendar.adapters import icalendar_acl
        _reader = icalendar_acl
    else:
        _reader = ics_reader
    _kind = kind


def reset():
    """`ics_reader`: how a process starts."""
    use("stdlib")


def kind():
    return _kind


class _Reader:
    """The IcsReader this process reads invites through."""
    def parse(self, text):
        return _reader.parse(text)

    def events(self, text):
        return _reader.events(text)

    def next_occurrence(self, inv, after):
        return _reader.next_occurrence(inv, after)


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
ics = _Reader()
invites = Invites(_stored, ics)
agenda = Agenda(_stored, ics)
rsvp = Rsvp(invites, _stored, _Messages(), _Send())
