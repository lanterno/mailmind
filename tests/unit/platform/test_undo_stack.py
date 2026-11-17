"""The undo stack, which lives in the database (undo_entries, migration 25) so Undo outlives the
process: what a push adds, how an undo group gathers its members, and what the stack keeps.
tests/e2e/test_front_door.py undoes across a restart; the routes' undo is in test_http_mail.py and
characterization/mailbox/test_undo.py."""
from datetime import datetime, timedelta, timezone

import pytest
import time_machine

from mailmind.bootstrap import mailbox
from mailmind.bootstrap.mailbox import store
from mailmind.kernel.errors import Conflict, NotFound
from mailmind.platform.profile import ReadOnlyError


def move(n):
    return {"kind": "move", "account": "ada@example.test", "src_folder": "INBOX", "dest_folder": "Archive",
            "message_id": f"<m{n}@example.test>", "label": "Archived"}


def ids(stack):
    return [[c["message_id"] for c in e["children"]] if e["kind"] == "bulk" else e["message_id"] for e in stack]


def test_a_group_gathers_its_members_only_while_it_is_on_top(data_dir):
    mailbox.undo.push(move(1), "g")
    mailbox.undo.push(move(2), "g")
    mailbox.undo.push(move(3))
    mailbox.undo.push(move(4), "g")                                # g is no longer on top: a new entry
    assert ids(mailbox.undo.entries()) == [["<m1@example.test>", "<m2@example.test>"], "<m3@example.test>",
                                          ["<m4@example.test>"]]
    assert mailbox.undo.entries()[0]["label"] == "Archived"


def test_many_go_under_one_group_at_one_write_as_each_in_turn_would(data_dir):
    mailbox.undo.push(move(0), "cleanup")
    mailbox.undo.push_many([move(1), move(2), move(3)], "sweep")
    mailbox.undo.push_many([move(4)], "sweep")                     # still on top: it joins the group
    mailbox.undo.push_many([], "later")                            # nothing went: nothing on the stack
    assert ids(mailbox.undo.entries()) == [["<m0@example.test>"], ["<m1@example.test>", "<m2@example.test>",
                                                                   "<m3@example.test>", "<m4@example.test>"]]
    assert [e["group"] for e in mailbox.undo.entries()] == ["cleanup", "sweep"]


def test_the_stack_keeps_its_newest_entries(data_dir, monkeypatch):
    monkeypatch.setattr(mailbox.undo, "UNDO_MAX", 3)
    for n in range(5):
        mailbox.undo.push(move(n))
    assert ids(mailbox.undo.entries()) == ["<m2@example.test>", "<m3@example.test>", "<m4@example.test>"]


def test_nothing_to_reverse_pushes_nothing(data_dir):
    mailbox.undo.push(None)
    mailbox.undo.push({}, "g")
    assert mailbox.undo.entries() == []


def test_an_undo_that_fails_puts_its_entry_back_on_top(data_dir, monkeypatch):
    mailbox.undo.push(move(1))
    mailbox.undo.push(move(2))
    monkeypatch.setattr(mailbox.undo, "_reverse_one", lambda d: (False, d["account"], None))
    with pytest.raises(Conflict):
        mailbox.undo.undo_last()
    assert ids(mailbox.undo.entries()) == ["<m1@example.test>", "<m2@example.test>"]


def test_it_holds_fifty_groups_each_counting_once(data_dir):
    for n in range(50):
        mailbox.undo.push(move(n))
    mailbox.undo.push(move(50), "g")
    mailbox.undo.push(move(51), "g")
    held = ids(mailbox.undo.entries())
    assert len(held) == 50 and held[0] == "<m1@example.test>" and held[-1] == ["<m50@example.test>", "<m51@example.test>"]


def test_an_entry_a_day_old_can_no_longer_be_undone(data_dir, monkeypatch):
    """Whichever comes first, of 50 groups and 24 hours: Undo finds nothing, and asks no server."""
    monkeypatch.setattr(mailbox.undo, "_reverse_one", lambda d: pytest.fail(f"asked to reverse {d['message_id']}"))
    with time_machine.travel(datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc), tick=False) as clock:
        mailbox.undo.push(move(1))
        clock.shift(timedelta(hours=23, minutes=59))
        assert ids(mailbox.undo.entries()) == ["<m1@example.test>"]
        clock.shift(timedelta(minutes=2))
        assert mailbox.undo.entries() == []
        with pytest.raises(NotFound) as gone:
            mailbox.undo.undo_last()
    assert gone.value.more == {"empty": True}


def test_a_new_entry_ends_what_has_grown_too_old(data_dir):
    with time_machine.travel(datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc), tick=False) as clock:
        mailbox.undo.push(move(1), "g")
        clock.shift(timedelta(hours=25))
        mailbox.undo.push(move(2), "g")                            # a new group: the old one has gone
        assert ids(mailbox.undo.entries()) == [["<m2@example.test>"]]
        c = store.conn()
        assert c.execute("SELECT COUNT(*) FROM undo_entries").fetchone()[0] == 1
        c.close()


class Swept:
    """Reverses `swept` entries together and `move` ones alone, noting each call. One whose Message-ID is `missing`
    doesn't go back; a run holding a `lost` one raises whole, and one `refused` is a read-only process's refusal."""

    def __init__(self):
        self.asked = []

    def one(self, d):
        self.asked.append(f"one {d['message_id']}")
        if d["message_id"] == "refused":
            raise ReadOnlyError("a move undone")
        return d["message_id"] != "missing", d["account"], "INBOX"

    def together(self, ds):
        mids = [d["message_id"] for d in ds]
        self.asked.append("together " + " ".join(mids))
        if "lost" in mids:
            raise ConnectionError("scripted")
        if "refused" in mids:
            raise ReadOnlyError("a sweep undone")
        return [(d["message_id"] != "missing", d["account"], "INBOX") for d in ds]


def swept(kind, mid, account="ada@example.test"):
    return {"kind": kind, "account": account, "message_id": mid, "label": "Swept junk"}


def undo_over(reverser, children):
    from mailmind.platform.undo import Undo
    undo = Undo(mailbox.undo._uow, {"swept": lambda d: reverser.together([d])[0], "move": reverser.one}, lambda: None,
                lambda e: type(e).__name__, together={"swept": reverser.together})
    undo.push_many(children, "ug-1")
    return undo


def test_a_run_of_one_kind_and_one_account_goes_back_together_newest_first_and_the_rest_one_at_a_time(data_dir):
    """As the Rust server's Undo does (core/crates/mm-store/tests/undo_groups.rs)."""
    reverser = Swept()
    undo = undo_over(reverser, [swept("swept", "m1"), swept("swept", "missing"), swept("move", "m3"),
                                swept("swept", "m4"), swept("swept", "m5"), swept("swept", "m6", "bea@example.test")])
    done = undo.undo_group("ug-1")
    assert (done["reversed"], done["failed"]) == (5, 1)
    assert reverser.asked == ["together m6", "together m5 m4", "one m3", "together missing m1"]
    assert ids(undo.entries()) == [["missing"]]


def test_a_run_that_fails_whole_is_on_the_stack_again_and_stops_no_other(data_dir):
    reverser = Swept()
    undo = undo_over(reverser, [swept("swept", "m1"), swept("swept", "lost"), swept("move", "m3")])
    done = undo.undo_group("ug-1")
    assert (done["reversed"], done["failed"]) == (1, 2)
    assert reverser.asked == ["one m3", "together lost m1"]
    assert ids(undo.entries()) == [["m1", "lost"]]
    with pytest.raises(Conflict):                                   # nothing of it going back is a refusal
        undo.undo_group("ug-1")
    assert ids(undo.entries()) == [["m1", "lost"]]


def test_a_group_a_read_only_process_refuses_whole_says_so_and_waits_on_the_stack(data_dir):
    """As the Rust server's Undo does (core/crates/mm-store/tests/undo_groups.rs)."""
    undo = undo_over(Swept(), [swept("swept", "refused"), swept("move", "refused")])
    with pytest.raises(ReadOnlyError):
        undo.undo_group("ug-1")
    assert ids(undo.entries()) == [["refused", "refused"]]


def test_what_went_back_of_a_group_a_read_only_process_refused_in_part_is_said(data_dir):
    undo = undo_over(Swept(), [swept("move", "refused"), swept("move", "m2")])
    done = undo.undo_group("ug-1")
    assert (done["reversed"], done["failed"]) == (1, 1)
    assert ids(undo.entries()) == [["refused"]]


def test_one_a_read_only_process_refuses_goes_on_up_and_back_on_top(data_dir, monkeypatch):
    mailbox.undo.push(move(1))

    def refused(d):
        raise ReadOnlyError("a move undone")

    monkeypatch.setattr(mailbox.undo, "_reverse_one", refused)
    with pytest.raises(ReadOnlyError):
        mailbox.undo.undo_last()
    assert ids(mailbox.undo.entries()) == ["<m1@example.test>"]
