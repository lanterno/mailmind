"""An RSVP (mailmind.calendar.application.rsvp.Rsvp) and the invite it answers (invites.Invites), on fakes behind
Calendar's ports: what is queued for the organizer, and each way a request is refused before anything is."""
from email.message import EmailMessage

import pytest

from mailmind.bootstrap.calendar import ics
from mailmind.calendar.application.invites import Invites
from mailmind.calendar.application.rsvp import Rsvp
from mailmind.kernel.errors import NotFound, Refused, Unreachable

ADA = "ada@example.test"
ICS = "\r\n".join(["BEGIN:VCALENDAR", "METHOD:REQUEST", "BEGIN:VEVENT", "UID:standup-1", "SUMMARY:Standup",
                   "DTSTART:20261103T100000Z", "ORGANIZER;CN=Ren:mailto:ren@example.test",
                   'ATTENDEE;CN="Ada L.":mailto:ada@example.test', "END:VEVENT", "END:VCALENDAR", ""])


def raw_invite(ics_text=ICS, content_type="text/calendar", filename=None):
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = "ren@example.test", ADA, "Invitation: Standup"
    msg.set_content("You're invited.")
    maintype, subtype = content_type.split("/")
    msg.add_attachment(ics_text.encode(), maintype=maintype, subtype=subtype, filename=filename)
    return msg.as_bytes()


CALENDAR_PART = {"part_id": "2", "content_type": "text/calendar", "filename": None}


class World:
    def __init__(self, raw=None, attachments=(CALENDAR_PART,)):
        self.raw = raw_invite() if raw is None else raw
        self.attachments = list(attachments)
        self.rows = {7: {"pk": 7, "account": ADA, "subject": "Invitation: Standup"}}
        self.accounts = {ADA: {"email": ADA, "alias": "Ada"}}
        self.fetch_fails = None
        self.build_refuses = None
        self.sent_keys = []
        self.queued = []

    # StoredInvites
    def body(self, pk):
        return self.raw

    def keys_sent(self, prefix):
        return [k for k in self.sent_keys if k.startswith(prefix)]

    # Messages
    def message(self, pk):
        return self.rows.get(pk)

    def account(self, email):
        return self.accounts[email]

    def fetched(self, account, pk):
        if self.fetch_fails:
            raise self.fetch_fails
        return {"attachments": self.attachments}

    def quiet_error(self, e):
        return type(e).__name__

    # SystemSend
    def build(self, spec, original):
        if self.build_refuses:
            raise ValueError(self.build_refuses)
        self.spec = spec
        return {"to_disp": spec["to"], "cc_disp": [], "bcc_disp": []}

    def enqueue(self, built, meta):
        self.queued.append(meta)
        return 41, True

    def undo_seconds(self):
        return 10

    def rsvp(self, **body):
        return Rsvp(Invites(self, ics), self, self, self).respond(body)


# -- the invite a message carries --------------------------------------------------------------------------------

@pytest.mark.parametrize("part", [CALENDAR_PART, {"part_id": "2", "content_type": "application/octet-stream", "filename": "INVITE.ICS"}],
                         ids=["by-type", "by-name"])
def test_a_calendar_part_is_read_into_the_invite_and_its_part(part):
    world = World(attachments=[{"part_id": "1", "content_type": "text/plain"}, part])
    invite, part_id = Invites(world, ics).of(7, {"attachments": world.attachments})
    assert (invite["summary"], invite["uid"], part_id) == ("Standup", "standup-1", "2")


@pytest.mark.parametrize("world", [
    pytest.param(World(attachments=[]), id="no-calendar-part"),
    pytest.param(World(raw=b""), id="no-stored-body"),
    pytest.param(World(attachments=[{**CALENDAR_PART, "part_id": "9"}]), id="no-such-part"),
    pytest.param(World(attachments=[{**CALENDAR_PART, "part_id": None}]), id="a-part-id-that-isnt-one"),
])
def test_a_message_with_no_invite_to_read_has_none(world):
    assert Invites(world, ics).of(7, {"attachments": world.attachments}) == (None, None)


def test_a_reader_that_fails_is_no_invite():
    class Broken:
        def parse(self, text):
            raise ValueError("not a calendar")
    world = World()
    assert Invites(world, Broken()).of(7, {"attachments": world.attachments}) == (None, None)


# -- an answer ---------------------------------------------------------------------------------------------------

def test_an_answer_queues_a_reply_to_the_organizer_under_its_own_key():
    world = World()
    out = world.rsvp(pk=7, decision=" Accept ")
    assert out == {"ok": True, "outbox_id": 41, "queued": True, "decision": "accept", "organizer": "ren@example.test",
                   "summary": "Standup", "undo_seconds": 10}
    [meta] = world.queued
    assert (meta["idempotency_key"], meta["subject"], meta["to"], meta["in_reply_to_pk"]) == \
        ("rsvp:ada@example.test:standup-1:ACCEPTED:0", "Accepted: Standup", ["ren@example.test"], 7)
    [attachment] = world.spec["attachments"]
    assert attachment["params"] == {"method": "REPLY", "charset": "UTF-8"}
    assert b"PARTSTAT=ACCEPTED:mailto:ada@example.test" in attachment["data"]
    assert world.spec["account_name"] == "Ada L."
    assert world.spec["text"].startswith('Ada L. has responded "Accepted"')


def test_the_same_answer_again_is_the_same_send_and_a_changed_mind_a_new_one():
    world = World()
    world.sent_keys = ["rsvp:ada@example.test:standup-1:DECLINED:0"]
    world.rsvp(pk=7, decision="decline")
    world.rsvp(pk=7, decision="tentative")
    assert [m["idempotency_key"] for m in world.queued] == ["rsvp:ada@example.test:standup-1:DECLINED:0",
                                                           "rsvp:ada@example.test:standup-1:TENTATIVE:1"]


@pytest.mark.parametrize("body, refusal, said", [
    ({"decision": "accept"}, Refused, "missing message"),
    ({"pk": "seven", "decision": "accept"}, Refused, "missing message"),
    ({"pk": 7, "decision": "maybe"}, Refused, "bad decision"),
    ({"pk": 7}, Refused, "bad decision"),
    ({"pk": 8, "decision": "accept"}, NotFound, "not found"),
], ids=repr)
def test_a_request_that_names_no_answer_or_no_message_is_refused(body, refusal, said):
    world = World()
    with pytest.raises(refusal, match=said):
        world.rsvp(**body)
    assert world.queued == []


def test_a_message_of_an_account_gone_is_refused():
    world = World()
    world.accounts = {}
    with pytest.raises(Refused, match="unknown account"):
        world.rsvp(pk=7, decision="accept")


def test_a_message_that_cant_be_fetched_says_so_and_names_no_one(capsys):
    world = World()
    world.fetch_fails = OSError(f"{ADA}: no route to host")
    with pytest.raises(Unreachable, match="could not load the invite"):
        world.rsvp(pk=7, decision="accept")
    out = capsys.readouterr().out
    assert "[rsvp] fetch failed for pk 7: OSError" in out and ADA not in out


def test_a_message_with_no_invite_is_refused():
    world = World(attachments=[])
    with pytest.raises(Refused, match="no invite on this message"):
        world.rsvp(pk=7, decision="accept")


@pytest.mark.parametrize("organizer", ["", "ORGANIZER;CN=Ren:mailto:ren", None], ids=["none", "no-at", "dropped"])
def test_an_invite_with_no_organizer_to_reply_to_is_refused(organizer):
    lines = [line for line in ICS.split("\r\n") if not line.startswith("ORGANIZER")]
    if organizer:
        lines.insert(3, organizer)
    world = World(raw=raw_invite("\r\n".join(lines)))
    with pytest.raises(Refused, match="this invite has no organizer to reply to"):
        world.rsvp(pk=7, decision="accept")
    assert world.queued == []


def test_a_reply_the_composer_wont_build_is_refused_in_its_own_words():
    world = World()
    world.build_refuses = "the account has no address to send from"
    with pytest.raises(Refused, match="the account has no address to send from"):
        world.rsvp(pk=7, decision="accept")
    assert world.queued == []
