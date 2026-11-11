"""What a change asks of the server (mailmind.mailbox.application.server_ops.ServerOps), on an in-memory server behind the
Mailbox's ports: a command's try where an earlier one may have got part of the way, \\Answered wherever a replied-to message
is by now, the reversals Undo makes, and how each session ends. The paths through the fake IMAP server are the
characterization suite's; these are the ones it doesn't reach."""
import contextlib
import imaplib
import json
from types import SimpleNamespace

import pytest

from mailmind.mailbox.application.server_ops import ServerOps
from mailmind.mailbox.application.session import Selected
from mailmind.mailbox.domain import command
from mailmind.mailbox.domain.roles import Folder

ADA = "ada@example.test"
MID = "<note-1@example.test>"


class _Server:
    """One account's mailboxes: folder -> {uid: [message id, flags]}. `refuse` and `lose` name calls that get a NO, or lose
    the connection; a (call, folder) pair names one in that folder alone."""

    def __init__(self, boxes, *, roles=None, uidvalidity=5, capabilities=("MOVE",), refuse=(), lose=(), logs_out=True):
        self.boxes = {f: {u: [m, set(fl)] for u, (m, fl) in msgs.items()} for f, msgs in boxes.items()}
        self.roles = roles if roles is not None else {"inbox": "INBOX", "archive": "Archive", "trash": "Trash"}
        self.uidvalidity, self.capabilities = uidvalidity, frozenset(capabilities)
        self.refuse, self.lose, self.logs_out = set(refuse), set(lose), logs_out
        self.selected = None
        self.said = []
        self.next_uid = 100

    def _call(self, name, *args):
        self.said.append((name, *args))
        for faults, raised in ((self.lose, imaplib.IMAP4.abort), (self.refuse, imaplib.IMAP4.error)):
            if name in faults or (name, self.selected) in faults or (name == "select" and (name, args[0]) in faults):
                raise raised(f"{name}: no")

    def _box(self):
        return self.boxes.setdefault(self.selected, {})

    def folders(self):
        names = {v: k for k, v in self.roles.items()}
        return [Folder(f, frozenset({f"\\{names[f]}"} if f in names and names[f] != "inbox" else set())) for f in self.boxes]

    def select(self, folder, readonly=True):
        self._call("select", folder, readonly)
        self.selected = folder
        return Selected(len(self._box()), self.uidvalidity, None)

    def by_message_id(self, mid):
        self._call("by_message_id", mid)
        found = [u for u, (m, _) in self._box().items() if m == mid]
        return max(found) if found else None

    def uids(self, *criteria):
        self._call("search", *criteria)
        mid, undeleted = criteria[-1].strip('"'), "UNDELETED" in criteria
        return [u for u, (m, fl) in self._box().items() if m == mid and not (undeleted and "\\Deleted" in fl)]

    def flags(self, uids):
        self._call("flags", list(uids))
        return {u: frozenset(self._box()[u][1]) for u in uids if u in self._box()}

    def add_flags(self, uids, flags):
        self._call("add_flags", list(uids), list(flags))
        for u in uids:
            self._box()[u][1].update(flags)

    def remove_flags(self, uids, flags):
        self._call("remove_flags", list(uids), list(flags))
        for u in uids:
            self._box()[u][1].difference_update(flags)

    def add_labels(self, uids, labels):
        self._call("add_labels", list(uids), list(labels))

    def remove_labels(self, uids, labels):
        self._call("remove_labels", list(uids), list(labels))

    def move(self, uids, dest):
        self._call("move", list(uids), dest)
        for u in uids:
            self.boxes.setdefault(dest, {})[self.next_uid] = self._box().pop(u)
            self.next_uid += 1

    def copy(self, uids, dest):
        self._call("copy", list(uids), dest)
        for u in uids:
            m, fl = self._box()[u]
            self.boxes.setdefault(dest, {})[self.next_uid] = [m, set(fl)]
            self.next_uid += 1

    def expunge(self, uids):
        self._call("expunge", list(uids))
        for u in uids:
            self._box().pop(u, None)

    def logout(self):
        self.said.append(("logout",))
        if not self.logs_out:
            raise OSError("the connection went")

    def shutdown(self):
        self.said.append(("shutdown",))

    def where(self, mid=MID):
        return sorted((f, u) for f, msgs in self.boxes.items() for u, (m, _) in msgs.items() if m == mid)

    def calls(self, *names):
        return [s for s in self.said if s[0] in names]


class _Store:
    def __init__(self, rows=()):
        self.rows = {r["pk"]: dict(r) for r in rows}

    def get_message(self, c, pk):
        return self.rows.get(pk)

    def get_message_by_msgid(self, c, account, mid):
        return next((r for r in self.rows.values() if r["account"] == account and r["message_id"] == mid), None)

    def set_local_flags(self, c, pk, **columns):
        self.rows[pk].update(columns)


class _Uow:
    @contextlib.contextmanager
    def read(self):
        yield "c"

    write = read


def ops(server, *, store=None, marks_trash_read=False, junk_fallback=None):
    accounts = SimpleNamespace(
        get=lambda email: {"email": email},
        trash_folder=lambda acct: "Trash",
        profile=lambda acct: SimpleNamespace(archive_is_label_removal=False, junk_fallback=junk_fallback,
                                             marks_trash_read=marks_trash_read))

    def open_session(acct, timeout=None):
        return server
    return ServerOps(_Uow(), store or _Store(), accounts, open_session, lambda value: f'"{value}"')


def row(kind="archive", *, uid=7, epoch=5, dest=None, src="INBOX", mid=MID, seen=True):
    return {"account": ADA, "kind": kind, "src_folder": src, "dest": dest, "message_id": mid,
            "params": json.dumps({"uid": uid, "epoch": epoch, "seen": seen})}


# -- a command's try ---------------------------------------------------------------------------------------------

def test_a_try_after_one_whose_copy_landed_removes_the_original_alone_and_copies_nothing():
    server = _Server({"INBOX": {7: (MID, ())}, "Archive": {50: (MID, ())}})
    assert ops(server).apply(row()) == command.ServerAnswer(dest="Archive")
    assert server.where() == [("Archive", 50)]
    assert server.calls("move", "copy") == [] and server.calls("expunge") == [("expunge", [7])]
    assert server.selected == "INBOX"


def test_a_copy_flagged_deleted_doesnt_count_as_landed():
    server = _Server({"INBOX": {7: (MID, ())}, "Archive": {50: (MID, ("\\Deleted",))}})
    ops(server).apply(row())
    assert server.calls("move") == [("move", [7], "Archive")]


@pytest.mark.parametrize("fault", [("select", "Archive"), ("search", "Archive")], ids=["select", "search"])
def test_a_destination_that_wont_say_what_it_holds_is_taken_to_hold_nothing(fault):
    server = _Server({"INBOX": {7: (MID, ())}, "Archive": {50: (MID, ())}}, refuse={fault})
    assert ops(server).apply(row()) == command.ServerAnswer(dest="Archive")
    assert server.calls("move") == [("move", [7], "Archive")]


def test_a_retry_with_no_message_id_asks_nothing_first():
    server = _Server({"INBOX": {7: (None, ())}, "Archive": {}})
    ops(server).apply(row(mid=None))
    assert server.calls("search", "by_message_id") == [] and server.calls("move") == [("move", [7], "Archive")]


def test_a_retry_whose_search_is_refused_goes_by_the_uid_it_was_given():
    server = _Server({"INBOX": {7: (MID, ())}, "Archive": {}}, refuse={("by_message_id", "INBOX")})
    ops(server).apply(row())
    assert server.calls("move") == [("move", [7], "Archive")]


def test_a_retry_whose_search_is_refused_after_a_renumber_has_nothing_to_go_by():
    server = _Server({"INBOX": {7: (MID, ())}, "Archive": {}}, refuse={("by_message_id", "INBOX")}, uidvalidity=6)
    assert ops(server).apply(row()) == command.ServerAnswer()
    assert server.where() == [("INBOX", 7)]


def test_a_retry_whose_search_loses_the_connection_raises_and_hangs_up():
    server = _Server({"INBOX": {7: (MID, ())}}, lose={"by_message_id"})
    with pytest.raises(imaplib.IMAP4.abort):
        ops(server).apply(row())
    assert server.said[-1] == ("shutdown",)


def test_junk_with_nowhere_to_go_wont_happen():
    server = _Server({"INBOX": {7: (MID, ())}})
    assert ops(server).apply(row("junk"), first_try=True) == command.ServerAnswer(won_t=command.NO_FOLDER)
    assert server.where() == [("INBOX", 7)]


def test_junk_goes_to_the_fallback_where_no_folder_plays_junk():
    server = _Server({"INBOX": {7: (MID, ())}})
    assert ops(server, junk_fallback="Spam").apply(row("junk"), first_try=True) == command.ServerAnswer(dest="Spam")


@pytest.mark.parametrize("flags, seen, unseen", [
    ((), True, True),
    (("\\Seen",), False, False),
], ids=["unread-on-the-server", "read-on-the-server"])
def test_trash_on_a_server_that_marks_it_read_says_whether_it_was_unread_as_the_server_says(flags, seen, unseen):
    server = _Server({"INBOX": {7: (MID, flags)}, "Trash": {}})
    assert ops(server, marks_trash_read=True).apply(row("trash", seen=seen), first_try=True) == \
        command.ServerAnswer(dest="Trash", unseen=unseen)


@pytest.mark.parametrize("server, seen, unseen", [
    (_Server({"INBOX": {7: (MID, ())}, "Trash": {}}, refuse={"flags"}), False, True),
    (_Server({"INBOX": {7: (MID, ())}, "Trash": {}}, refuse={"flags"}), True, False),
], ids=["unread-here", "read-here"])
def test_when_the_server_wont_say_the_rows_word_on_read_stands(server, seen, unseen):
    assert ops(server, marks_trash_read=True).apply(row("trash", seen=seen), first_try=True).unseen is unseen


def test_a_lost_connection_while_reading_the_mark_raises():
    server = _Server({"INBOX": {7: (MID, ())}, "Trash": {}}, lose={"flags"})
    with pytest.raises(imaplib.IMAP4.abort):
        ops(server, marks_trash_read=True).apply(row("trash"), first_try=True)


def test_a_try_whose_logout_fails_still_answers():
    server = _Server({"INBOX": {7: (MID, ())}, "Archive": {}}, logs_out=False)
    assert ops(server).apply(row(), first_try=True) == command.ServerAnswer(dest="Archive")


def test_a_lost_connection_hangs_up_without_a_logout():
    server = _Server({"INBOX": {7: (MID, ())}, "Archive": {}}, lose={"move"})
    with pytest.raises(imaplib.IMAP4.abort):
        ops(server).apply(row(), first_try=True)
    assert server.said[-1] == ("shutdown",) and ("logout",) not in server.said


# -- \Answered ---------------------------------------------------------------------------------------------------

def test_a_reply_marks_its_original_through_its_row_where_there_is_one():
    store = _Store([{"pk": 3, "account": ADA, "message_id": MID, "folder": "Archive", "uid": 7}])
    server = _Server({"Archive": {7: (MID, ())}})
    assert ops(server, store=store).mark_answered(3, ADA, MID) == (True, "ok")
    assert server.boxes["Archive"][7][1] == {"\\Answered"} and store.rows[3]["answered"] == 1


def test_with_no_row_left_it_is_found_in_the_first_folder_filing_moves_mail_to_that_holds_it():
    server = _Server({"INBOX": {}, "Archive": {9: (MID, ())}, "Trash": {11: (MID, ())}},
                     roles={"inbox": "INBOX", "archive": "Archive", "trash": "Trash", "junk": "Spam"})
    assert ops(server).mark_answered(None, ADA, MID) == (True, "ok")
    assert server.boxes["Archive"][9][1] == {"\\Answered"} and server.boxes["Trash"][11][1] == set()
    assert [s[1] for s in server.calls("select")] == ["INBOX", "Archive"]


def test_a_folder_that_wont_open_is_passed_over():
    server = _Server({"INBOX": {}, "Archive": {9: (MID, ())}, "Trash": {11: (MID, ())}}, refuse={("select", "Archive")})
    assert ops(server).mark_answered(None, ADA, MID) == (True, "ok")
    assert server.boxes["Trash"][11][1] == {"\\Answered"}


def test_found_nowhere_it_is_not_found():
    server = _Server({"INBOX": {}, "Archive": {}})
    assert ops(server).mark_answered(None, ADA, MID) == (False, "not found")
    assert server.said[-1] == ("logout",)


@pytest.mark.parametrize("account, mid", [(None, MID), (ADA, None)])
def test_with_no_row_and_nothing_to_search_by_no_server_is_asked(account, mid):
    server = _Server({"INBOX": {}})
    assert ops(server).mark_answered(None, account, mid) == (False, "not found")
    assert server.said == []


def test_a_lost_connection_while_looking_raises():
    server = _Server({"INBOX": {}, "Archive": {9: (MID, ())}}, lose={("by_message_id", "Archive")}, logs_out=False)
    with pytest.raises(imaplib.IMAP4.abort):
        ops(server).mark_answered(None, ADA, MID)


def test_a_flag_change_on_a_row_gone_is_not_found():
    assert ops(_Server({})).set_flags(3, {"answered": True}) == (False, "not found")


def test_a_flag_change_the_server_refuses_leaves_the_row_as_the_server_has_it():
    store = _Store([{"pk": 3, "account": ADA, "message_id": MID, "folder": "INBOX", "uid": 7}])
    server = _Server({"INBOX": {7: (MID, ())}}, refuse={"add_flags"}, logs_out=False)
    assert ops(server, store=store).set_flags(3, {"answered": True}) == (False, "the server refused the flag change")
    assert "answered" not in store.rows[3]


def test_a_flag_change_that_loses_the_connection_raises():
    store = _Store([{"pk": 3, "account": ADA, "message_id": MID, "folder": "INBOX", "uid": 7}])
    with pytest.raises(imaplib.IMAP4.abort):
        ops(_Server({"INBOX": {7: (MID, ())}}, lose={"select"}), store=store).set_flags(3, {"answered": True})


# -- Undo's reversals --------------------------------------------------------------------------------------------

def test_a_move_back_goes_by_message_id():
    server = _Server({"INBOX": {}, "Archive": {9: (MID, ())}})
    assert ops(server).move_by_msgid(ADA, "Archive", "INBOX", MID) == (True, "moved back")
    assert server.where() == [("INBOX", 100)]


def test_a_move_back_takes_off_the_read_mark_the_server_set_on_the_way():
    server = _Server({"INBOX": {}, "Trash": {9: (MID, ("\\Seen", "\\Flagged"))}})
    assert ops(server).move_by_msgid(ADA, "Trash", "INBOX", MID, unseen=True) == (True, "moved back")
    assert server.boxes["INBOX"][100][1] == {"\\Flagged"}


def test_a_read_mark_that_wont_come_off_leaves_the_move_back_done():
    server = _Server({"INBOX": {}, "Trash": {9: (MID, ("\\Seen",))}}, refuse={"remove_flags"})
    assert ops(server).move_by_msgid(ADA, "Trash", "INBOX", MID, unseen=True) == (True, "moved back")


def test_a_read_mark_that_loses_the_connection_raises():
    server = _Server({"INBOX": {}, "Trash": {9: (MID, ("\\Seen",))}}, lose={"remove_flags"})
    with pytest.raises(imaplib.IMAP4.abort):
        ops(server).move_by_msgid(ADA, "Trash", "INBOX", MID, unseen=True)


@pytest.mark.parametrize("server, said", [
    (_Server({"Archive": {}}), "message not found"),
    (_Server({"Archive": {9: (MID, ())}}, refuse={"by_message_id"}), "message not found"),
    (_Server({"Archive": {9: (MID, ())}}, refuse={"move"}), "the server refused the move"),
], ids=["gone", "search-refused", "move-refused"])
def test_a_move_back_never_guesses(server, said):
    assert ops(server).move_by_msgid(ADA, "Archive", "INBOX", MID) == (False, said)
    assert server.where() in ([], [("Archive", 9)]) and server.said[-1] == ("logout",)


def test_a_move_back_that_loses_the_connection_raises():
    with pytest.raises(imaplib.IMAP4.abort):
        ops(_Server({"Archive": {9: (MID, ())}}, lose={"move"}, logs_out=False)).move_by_msgid(ADA, "Archive", "INBOX", MID)


@pytest.mark.parametrize("server, said", [
    (_Server({"All Mail": {9: (MID, ())}}), (True, "relabeled")),
    (_Server({"All Mail": {}}), (False, "message not found")),
    (_Server({"All Mail": {9: (MID, ())}}, refuse={"add_labels"}), (False, "the server refused the label")),
], ids=["relabeled", "gone", "refused"])
def test_a_gmail_archive_undone_puts_the_label_back(server, said):
    assert ops(server).relabel_by_msgid(ADA, "All Mail", MID) == said
    assert server.said[-1] == ("logout",)


def test_a_relabel_that_loses_the_connection_raises():
    with pytest.raises(imaplib.IMAP4.abort):
        ops(_Server({"All Mail": {9: (MID, ())}}, lose={"add_labels"})).relabel_by_msgid(ADA, "All Mail", MID)


def test_the_folder_playing_a_role_is_the_servers_to_say():
    server = _Server({"INBOX": {}, "Archive": {}, "Trash": {}})
    assert ops(server).find_special(ADA, "junk", "trash") == "Trash"
    assert server.said[-1] == ("logout",)
