"""iMIP RSVP: reply to a calendar invite's organizer with a METHOD:REPLY .ics carrying the responder's PARTSTAT. Goes
through the durable outbox (so it's Undo-Send-able), behind the same mutation + strict-origin guards as /api/send."""
from collections.abc import Mapping
from typing import Any

from mailmind.calendar.application.invites import Invites
from mailmind.calendar.application.ports import Messages, StoredInvites, SystemSend
from mailmind.calendar.domain import rsvp
from mailmind.calendar.domain.invite import Party
from mailmind.kernel.errors import NotFound, Refused, Unreachable
from mailmind.kernel.parsed import as_int
from mailmind.platform.logging import get_logger

log = get_logger("rsvp")


class Rsvp:
    def __init__(self, invites: Invites, stored: StoredInvites, messages: Messages, send: SystemSend):
        self._invites = invites
        self._stored = stored
        self._messages = messages
        self._send = send

    def respond(self, body: Mapping[str, Any]) -> dict[str, Any]:
        pk = as_int(body.get("pk"))
        if pk is None:
            raise Refused("missing message")
        decision = (body.get("decision") or "").strip().lower()
        if decision not in rsvp.PARTSTAT:
            raise Refused("bad decision")
        row = self._messages.message(pk)
        if not row:
            raise NotFound("not found")
        try:
            acct = self._messages.account(row["account"])
        except KeyError:
            raise Refused("unknown account")
        try:
            parsed = self._messages.fetched(row["account"], pk)
        except Exception as e:
            log.warning("fetch failed for pk %(id)s: %(why)s", {"id": pk, "why": self._messages.quiet_error(e)})
            raise Unreachable("could not load the invite")
        invite, _part = self._invites.of(pk, parsed)
        if not invite:
            raise Refused("no invite on this message")
        organizer = ((invite.get("organizer") or Party()).get("email") or "").strip()
        if not organizer or "@" not in organizer:
            raise Refused("this invite has no organizer to reply to")
        me_email = acct["email"]
        me_name = rsvp.name_for(invite, me_email, acct.get("alias") or me_email)
        partstat = rsvp.PARTSTAT[decision]
        cal = rsvp.build_reply(invite, me_email, me_name, partstat)
        summary = invite.get("summary") or "(no title)"
        subject = f"{rsvp.VERB[decision]}: {summary}"
        text = (f"{me_name} has responded \"{partstat.title()}\" to the invitation "
                f"“{summary}”.\n\n— sent from MailMind")
        spec: dict[str, Any] = {
            "account": acct["email"], "account_name": me_name,
            "to": [organizer], "cc": [], "bcc": [],
            "subject": subject, "text": text, "html": None,
            "attachments": [{"filename": "invite.ics",
                             "content_type": "text/calendar",
                             "params": {"method": "REPLY", "charset": "UTF-8"},
                             "data": cal.encode("utf-8")}],
        }
        try:
            built = self._send.build(spec, row)
        except ValueError as e:               # our own words: they hold nothing secret
            raise Refused(str(e)) from e
        prefix = rsvp.key_prefix(row["account"], invite.get("uid") or pk)
        meta: dict[str, Any] = {
            "account": acct["email"],
            "idempotency_key": rsvp.key(prefix, self._stored.keys_sent(prefix), partstat),
            "in_reply_to_pk": pk, "subject": subject,
            "to": built["to_disp"], "cc": built["cc_disp"], "bcc": built["bcc_disp"], "send_after": 0,
        }
        oid, created = self._send.enqueue(built, meta)
        return {"ok": True, "outbox_id": oid, "queued": created, "decision": decision,
                "organizer": organizer, "summary": summary, "undo_seconds": self._send.undo_seconds()}
