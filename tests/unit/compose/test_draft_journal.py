"""A draft is local first (decision D5). A save lands in the journal and answers at once; the server's copy follows it on
close, on an explicit save and at most once a minute while someone types, and a server that's away costs nothing but
waiting. The journal holds one row a draft, the newest save, and the copy a try may have left never stays behind.

Here Drafts (mailmind.compose.application.drafts) is built over a temp database with its own tables, and fakes stand behind
the ports that reach a server: the copies in Drafts (`Server`), the accounts, the stored mail and the screen. What a save
asks of the server, when, and what it may not do. A real server's answers, the gate, a crash and a send that goes out are
tests/integration/compose/test_draft_journal.py's."""
import itertools
from datetime import datetime, timedelta, timezone

import pytest
import time_machine

import problems
from answered import answered
from mailmind.bootstrap import compose
from mailmind.compose.adapters import sqlite as book
from mailmind.compose.application.composing import Composing
from mailmind.compose.application.drafts import Drafts
from mailmind.compose.application.outbox import Outbox
from mailmind.compose.application.sending import Sending
from mailmind.compose.domain.draft_journal import PUSH_EVERY, PUSH_TICK
from mailmind.kernel.errors import Conflict, Locked
from mailmind.platform.db.uow import UnitOfWork

ADA = "ada@example.test"
NOW = datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc)


class Server:
    """The account's Drafts folder, as the Mailbox's copies keep it: what is there by UID, and every call made of it. With
    `away` set, every call raises it, as a connection lost does."""
    def __init__(self):
        self.live, self.calls, self.away = {}, [], None
        self._uids = itertools.count(101)

    def _asked(self, what):
        self.calls.append(what)
        if self.away:
            raise self.away

    def append_to_drafts(self, acct, raw, prev_uid=None, prev_uidvalidity=None, prev_msgid=None, prev_folder=None,
                         message_id=None, tried=None):
        self._asked("append")
        if prev_folder not in (None, "Drafts"):
            prev_uid = None
        uid = next(self._uids)
        self.live[uid] = raw
        removed = prev_uid if self.live.pop(prev_uid, None) is not None else None
        return "Drafts", uid, 777, removed

    def delete_draft_uid(self, acct, folder, uid, uidvalidity=None):
        self._asked("delete")
        return self.live.pop(uid, None) is not None

    def delete_left_copy(self, acct, message_id):
        self._asked("delete left")
        return False

    def holds(self, *texts):
        """Whether Drafts holds a copy of each text, and no other."""
        copies = list(self.live.values())
        return len(copies) == len(texts) and all(t.encode() in c for t, c in zip(texts, copies))


class Accounts:
    def get(self, email):
        if email != ADA:
            raise KeyError(email)
        return {"email": ADA, "type": "imap", "host": "imap.example.test"}

    def saves_sent(self, acct):
        return False

    def undo_send_seconds(self):
        return None


class Mail:
    """No mail stored: these drafts answer nothing."""
    def original(self, pk, account=None, message_id=None):
        return None

    def message(self, pk):
        return None

    def by_msgid(self, account, message_id):
        return None

    def body(self, pk):
        return None

    def uidvalidity(self, account, folder):
        return None

    def remove_copies(self, c, account, folder, uids):
        pass


class Screen:
    def names_drafts(self, folder):
        return folder == "Drafts"

    def plays_drafts(self, account, folder):
        return folder == "Drafts"

    def quiet_error(self, e):
        return type(e).__name__

    def touched(self, account, folder):
        pass

    def list_changed(self, account, folder):
        pass

    def outbox_changed(self):
        pass

    def stats_changed(self):
        pass


class Unused:
    """A port these tests never reach: nothing here is delivered."""
    def __getattr__(self, name):
        raise AssertionError(f"nothing here should ask for {name}")


class Compose:
    """Drafts, and the send that holds one, over one temp database."""
    def __init__(self):
        uow, accounts, mail, screen = UnitOfWork(), Accounts(), Mail(), Screen()
        self.server = Server()
        composing = Composing(uow, book, accounts, mail, screen)
        self.outbox = Outbox(uow, book, Unused(), self.server, accounts, mail, Unused(), screen)
        self.drafts = Drafts(uow, composing, self.outbox, book, accounts, mail, self.server, screen)
        self.sending = Sending(uow, composing, self.outbox, book)

    def save(self, **kw):
        r = self.drafts.save(new(**kw))
        assert r["ok"], r
        return r


@pytest.fixture
def clock():
    with time_machine.travel(NOW, tick=False) as traveller:
        yield traveller


@pytest.fixture
def it(data_dir, clock):
    return Compose()


def new(**kw):
    return {"account": ADA, "mode": "new", "to": ["lin@example.test"], "subject": "Swatch order", "text": "Two of each.",
            "idempotency_key": "cmp-1", **kw}


def row(did):
    with UnitOfWork().read() as c:
        return book.draft_get(c, did)


def waiting(did):
    with UnitOfWork().read() as c:
        return book.journal_get(c, did)


def a_minute_on(clock):
    clock.shift(timedelta(seconds=PUSH_EVERY + 1))


def test_discarding_a_draft_with_no_row_answers_as_gone(data_dir):
    assert compose.drafts.save({"delete": True, "draft_id": 12345}) == {"ok": True, "deleted": True}


# -- a save and its push ----------------------------------------------------------------

def test_an_autosave_touches_no_server(it):
    r = it.save(autosave=True)
    assert r["pushed"] is False and r["version"] == 1
    assert it.server.calls == []
    assert b"Two of each." in waiting(r["draft_id"])["raw"]


def test_autosaves_make_one_push_a_minute_of_the_newest_text(it, clock):
    did = it.save(autosave=True, text="One.")["draft_id"]
    assert it.save(autosave=True, draft_id=did, text="Two.")["version"] == 2
    it.drafts.push_round()
    assert it.server.calls == []
    a_minute_on(clock)
    it.drafts.push_round()
    assert it.server.holds("Two.") and waiting(did) is None
    it.save(autosave=True, draft_id=did, text="Three.")
    it.drafts.push_round()                                    # the last push was only now
    assert it.server.holds("Two.")
    a_minute_on(clock)
    it.drafts.push_round()
    assert it.server.holds("Three.") and waiting(did) is None


def test_a_save_someone_asked_for_goes_now_and_takes_what_autosave_left(it):
    did = it.save(autosave=True, text="One.")["draft_id"]
    r = it.save(draft_id=did, text="Two.")
    assert r["pushed"] is True and row(did)["uid"] == r["uid"]
    assert it.server.holds("Two.") and waiting(did) is None


# -- two windows ---------------------------------------------------------------------------

def test_a_save_that_never_saw_the_last_one_is_refused(it):
    did = it.save(idempotency_key="tab-1")["draft_id"]
    assert it.save(idempotency_key="tab-2", draft_id=did, base_version=1, text="From tab two.")["version"] == 2
    said = answered(it.drafts.save, new(idempotency_key="tab-1", draft_id=did, base_version=1, text="From tab one."))
    assert said == (problems.conflict("This draft changed in another window.", conflict=True, version=2), 409)
    assert it.server.holds("From tab two.") and row(did)["version"] == 2
    assert it.save(idempotency_key="tab-1", draft_id=did, text="From tab one.")["version"] == 3   # asked for by name


def test_a_save_that_names_no_composer_is_held_to_its_version(it):
    did = it.save()["draft_id"]
    body = new(draft_id=did, base_version=0)
    del body["idempotency_key"]
    with pytest.raises(Conflict):
        it.drafts.save(body)


def test_a_composer_whose_answer_was_lost_is_not_its_own_conflict(it):
    did = it.save(autosave=True)["draft_id"]
    it.save(autosave=True, draft_id=did, base_version=1, text="One.")
    assert it.save(autosave=True, draft_id=did, base_version=1, text="Two.")["version"] == 3


# -- reopening --------------------------------------------------------------------------------

def test_a_draft_only_the_journal_holds_opens_from_the_tray(it, clock):
    r = it.save(autosave=True, bcc=["cy@example.test"])
    opened = it.drafts.resume({"draft_id": r["draft_id"]})
    assert (opened["text"], opened["bcc"], opened["version"], opened["account"]) == ("Two of each.", ["cy@example.test"], 1, ADA)
    a_minute_on(clock)
    it.drafts.push_round()
    with pytest.raises(Conflict, match="Open it from Drafts"):           # it's in Drafts now
        it.drafts.resume({"draft_id": r["draft_id"]})


# -- discarding --------------------------------------------------------------------------------

def test_discarding_what_never_reached_the_server_asks_nothing_of_it(it):
    did = it.save(autosave=True)["draft_id"]
    assert it.drafts.save({"delete": True, "draft_id": did}) == {"ok": True, "deleted": True}
    assert it.server.calls == [] and row(did) is None and waiting(did) is None


def test_a_discard_the_server_hasnt_heard_leaves_what_the_row_says_of_the_composition(it):
    resume = {"in_reply_to": "<lunch@example.test>", "references": "<lunch@example.test>", "tail_text": ""}
    did = it.save(mode="reply", subject="Re: Lunch on Thursday?", resume=resume)["draft_id"]
    before = dict(row(did))
    it.server.away = OSError("connection reset")
    assert it.drafts.save({"delete": True, "draft_id": did})["deleted"] is True
    after = dict(row(did))
    assert after["version"] == before["version"] + 1
    assert {k: after[k] for k in ("mode", "subject", "saved_by")} == {k: before[k] for k in ("mode", "subject", "saved_by")}
    assert before["mode"] == "reply"


def test_a_save_after_a_discard_is_a_draft_of_its_own(it):
    r = it.save()
    it.server.away = OSError("connection reset")
    it.drafts.save({"delete": True, "draft_id": r["draft_id"]})
    again = it.save(draft_id=r["draft_id"], text="Changed my mind.", autosave=True)
    assert again["draft_id"] != r["draft_id"]


# -- a send --------------------------------------------------------------------------------------

def test_the_job_looks_again_when_the_next_save_comes_due(it, clock):
    assert it.drafts.push_round() == PUSH_TICK
    did = it.save(autosave=True)["draft_id"]
    assert it.drafts.push_round() == PUSH_TICK                 # a minute off
    assert it.sending.send(new(draft_id=did))["ok"]
    a_minute_on(clock)
    assert it.drafts.push_round() == PUSH_TICK                 # due, but held: nothing to look for sooner


def test_a_held_draft_refuses_a_save_and_isnt_named_in_the_tray(it, clock):
    did = it.save(autosave=True)["draft_id"]
    assert it.sending.send(new(draft_id=did))["ok"]
    with pytest.raises(Locked):
        it.save(draft_id=did, text="Too late.")
    clock.shift(timedelta(minutes=5))                          # long past when the tray would name a save
    assert it.drafts.waiting() == []                           # the outbox's row says it


def test_a_send_about_to_go_keeps_its_draft_from_being_pushed(it):
    did = it.save(autosave=True)["draft_id"]
    assert it.sending.send(new(draft_id=did))["ok"]
    with UnitOfWork().write() as c:
        book.journal_failed(c, did, 0, 0)                      # as good as due
    it.drafts.push_round()
    assert it.server.calls == []


def test_undo_send_leaves_the_journal_to_go_on(it, clock):
    did = it.save(autosave=True)["draft_id"]
    sent = it.sending.send(new(draft_id=did))
    assert it.outbox.cancel(sent["outbox_id"])
    a_minute_on(clock)
    it.drafts.push_round()
    assert it.server.holds("Two of each.") and waiting(did) is None
