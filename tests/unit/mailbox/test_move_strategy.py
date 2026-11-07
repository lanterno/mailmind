"""Which way a message moves (mailmind.mailbox.domain.move_strategy), over every kind of provider and what its server
offers. No server is asked anything: ServerOps carrying it out is the state machine's and the goldens' to hold."""
import dataclasses
import imaplib

import pytest

from mailmind.accounts.domain import provider
from mailmind.mailbox.application.session import move_by_copy
from mailmind.mailbox.domain import destination, move_strategy
from mailmind.mailbox.domain.destination import Destination
from mailmind.mailbox.domain.move_strategy import MoveStrategy, Way

GMAIL = {"kind": "gmail", "host": "imap.gmail.com", "starttls": False}
PROTON = {"kind": "proton", "host": "127.0.0.1", "starttls": True}
BRIDGE_LIKE = {"kind": "imap", "host": "127.0.0.1", "starttls": True}      # taken for Bridge, but says nothing of Trash
PLAIN = {"kind": "imap", "host": "mail.example.test", "starttls": False}
PROVIDERS = {"gmail": GMAIL, "proton": PROTON, "bridge-like": BRIDGE_LIKE, "plain": PLAIN}

VERBS = ("trash", "archive", "junk", "move")

WITH_MOVE = (frozenset({"MOVE"}), frozenset({"MOVE", "UIDPLUS"}),
             frozenset({"IMAP4REV1", "MOVE", "UIDPLUS", "IDLE", "SPECIAL-USE", "CONDSTORE", "ENABLE", "X-GM-EXT-1"}))
WITHOUT_MOVE = (frozenset(), frozenset({"IMAP4REV1"}), frozenset({"UIDPLUS"}),
                frozenset({"IMAP4REV1", "UIDPLUS", "IDLE", "SPECIAL-USE", "CONDSTORE", "ENABLE", "X-GM-EXT-1"}))
OFFERS = [*((c, Way.MOVE) for c in WITH_MOVE), *((c, Way.COPY) for c in WITHOUT_MOVE)]


def strategy(acct, verb, capabilities, named=None):
    """What an account's profile says, handed over as ServerOps hands it: a Mailbox domain module can't read one."""
    p = provider.resolve(acct)
    where = destination.resolve(verb, named=named, archive_is_label_removal=p.archive_is_label_removal, trash="Trash",
                                junk_fallback=p.junk_fallback, find=lambda *roles: roles[0].title())
    return move_strategy.choose(verb, where, capabilities=capabilities, marks_trash_read=p.marks_trash_read)


@pytest.mark.parametrize("capabilities, way", OFFERS)
@pytest.mark.parametrize("name, verb", [(name, verb) for name in PROVIDERS for verb in VERBS
                                        if (name, verb) != ("gmail", "archive")])
def test_every_filing_but_gmails_archive_goes_as_the_server_offers(name, verb, capabilities, way):
    assert strategy(PROVIDERS[name], verb, capabilities).way is way


@pytest.mark.parametrize("capabilities, way", OFFERS)
def test_gmails_archive_drops_the_label_whatever_its_server_offers(capabilities, way):
    assert strategy(GMAIL, "archive", capabilities) == MoveStrategy(Way.LABELS)


@pytest.mark.parametrize("verb", ("trash", "junk", "move"))
def test_the_rest_of_gmails_filing_is_a_real_move(verb):
    assert strategy(GMAIL, verb, frozenset({"MOVE"})) == MoveStrategy(Way.MOVE)
    assert strategy(GMAIL, verb, frozenset()) == MoveStrategy(Way.COPY)


def test_gmails_archive_ignores_a_folder_the_command_names():
    assert strategy(GMAIL, "archive", frozenset({"MOVE"}), named="Archive").way is Way.LABELS


@pytest.mark.parametrize("acct", (PROTON, BRIDGE_LIKE, PLAIN), ids=("proton", "bridge-like", "plain"))
def test_an_archive_elsewhere_is_a_real_move(acct):
    assert strategy(acct, "archive", frozenset({"MOVE"})) == MoveStrategy(Way.MOVE)
    assert strategy(acct, "archive", frozenset()) == MoveStrategy(Way.COPY)


@pytest.mark.parametrize("capabilities, way", OFFERS)
def test_a_trash_on_proton_reads_seen_first_since_it_will_mark_it_read(capabilities, way):
    assert strategy(PROTON, "trash", capabilities) == MoveStrategy(way, read_seen_first=True)


@pytest.mark.parametrize("verb", ("archive", "junk", "move"))
def test_nothing_else_on_proton_reads_seen_first(verb):
    assert not strategy(PROTON, verb, frozenset({"MOVE"})).read_seen_first


@pytest.mark.parametrize("acct", (GMAIL, BRIDGE_LIKE, PLAIN), ids=("gmail", "bridge-like", "plain"))
def test_no_other_server_marks_what_it_trashes_read(acct):
    assert not strategy(acct, "trash", frozenset({"MOVE"})).read_seen_first


def test_it_goes_by_the_verb_so_a_plain_move_to_trash_is_a_move():
    assert not strategy(PROTON, "move", frozenset({"MOVE"}), named="Trash").read_seen_first


@pytest.mark.parametrize("capabilities, way", OFFERS)
@pytest.mark.parametrize("verb", VERBS)
def test_a_bridge_takes_the_same_way_as_any_server_offering_the_same(verb, capabilities, way):
    """It breaks on a bulk EXPUNGE, and no way here sends one."""
    assert provider.resolve(PROTON).bulk_expunge_unsafe and provider.resolve(BRIDGE_LIKE).bulk_expunge_unsafe
    assert strategy(PROTON, verb, capabilities).way is strategy(BRIDGE_LIKE, verb, capabilities).way is \
        strategy(PLAIN, verb, capabilities).way is way


def test_a_destination_with_no_folder_is_its_own_to_say_and_not_a_way():
    assert move_strategy.choose("junk", Destination(), capabilities=frozenset({"MOVE"}),
                                marks_trash_read=False).way is Way.MOVE


def test_a_strategy_is_a_value():
    assert MoveStrategy(Way.COPY) == MoveStrategy(Way.COPY, read_seen_first=False)
    assert MoveStrategy(Way.COPY) != MoveStrategy(Way.COPY, read_seen_first=True)
    with pytest.raises(dataclasses.FrozenInstanceError):
        MoveStrategy(Way.COPY).way = Way.MOVE


class _Recording:
    """What Way.COPY's session sees. Nothing behind it, and it refuses what it's told to."""

    def __init__(self, refuse=(), lose=()):
        self.calls, self.refuse, self.lose = [], refuse, lose

    def _call(self, name, *args):
        self.calls.append((name, *args))
        if name in self.lose:
            raise imaplib.IMAP4.abort(f"{name}: connection lost")
        if name in self.refuse:
            raise imaplib.IMAP4.error(f"{name}: refused")

    def copy(self, uids, dest):
        self._call("copy", list(uids), dest)

    def add_flags(self, uids, flags):
        self._call("add_flags", list(uids), list(flags))

    def expunge(self, uids):
        self._call("expunge", list(uids))


def test_going_by_copy_never_expunges_in_bulk():
    s = _Recording()
    move_by_copy(s, [41, "42", 43], "Archive")
    assert s.calls == [("copy", [41, 42, 43], "Archive"), ("add_flags", [41, 42, 43], ["\\Deleted"]),
                       ("expunge", [41]), ("expunge", [42]), ("expunge", [43])]


def test_a_refused_copy_touches_no_original():
    s = _Recording(refuse=("copy",))
    with pytest.raises(imaplib.IMAP4.error):
        move_by_copy(s, [41], "Gone")
    assert s.calls == [("copy", [41], "Gone")]


def test_a_refused_expunge_leaves_the_original_flagged_and_goes_on():
    s = _Recording(refuse=("expunge",))
    move_by_copy(s, [41, 42], "Archive")
    assert s.calls[-2:] == [("expunge", [41]), ("expunge", [42])]


def test_a_lost_connection_during_the_expunge_still_raises():
    s = _Recording(lose=("expunge",))
    with pytest.raises(imaplib.IMAP4.abort):
        move_by_copy(s, [41, 42], "Archive")
    assert s.calls[-1] == ("expunge", [41])
