"""A state machine over the offline queue and Undo: under random moves, flag changes, outages,
refusals, crashes, resubmits, syncs, Undo, Retry and Discard, no change applies twice, none lands
once dropped, and no copy of a message is lost, on the server or in the lists.

Every run gets a world of its own. It calls the routes' own functions, not HTTP, and steps the
worker by hand: a crash is an exception at the step it would interrupt, and a restart is what
queue recovery does at boot. MAILMIND_MACHINE_RUNS asks for that many runs, with fresh random seeds,
and MAILMIND_MACHINE_STEPS for longer ones.
"""
import os
import shutil
from unittest import mock

import pytest
from hypothesis import HealthCheck, settings as hsettings, strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, precondition, rule, run_state_machine_as_test

from answered import answered
from fixtures import world as w
from mailmind.bootstrap import mailbox

LUNCH = ("ada-inbox-01-lunch.eml", "ada-inbox-02-lunch-confirm.eml")     # one conversation in the Inbox
NAMES = (*LUNCH, "ada-inbox-03-newsletter.eml", "ada-inbox-05-receipt.eml")
OPS = {"archive": "Archive", "trash": "Trash", "junk": "Junk"}
FOLDERS = ("INBOX", "Archive", "Trash", "Junk")
FRAGILE = ("SELECT", "UID MOVE", "UID COPY", "UID STORE", "UID EXPUNGE")
HALFWAY = ("UID STORE", "UID EXPUNGE")           # what a move by COPY does once the copy has landed


class Crash(BaseException):
    """The process died here: not an Exception, so no handler on the way out takes it for a failure."""


class Machine(RuleBasedStateMachine):
    template = data_dir = None
    has_move = True

    def __init__(self):
        super().__init__()
        from fakes.imap_server import FakeIMAPServer
        path, placed = self.template
        for suffix in ("", "-wal", "-shm"):
            p = self.data_dir / ("mailmind.db" + suffix)
            if p.exists():
                p.unlink()
        shutil.copyfile(path, self.data_dir / "mailmind.db")
        self.server = FakeIMAPServer()
        self.server.__enter__()
        assert w.populate(self.server) == placed
        w.write_config(self.server, str(self.data_dir))
        if not self.has_move:
            self.server.without.add("MOVE")
        self.world = w.World(self.server, placed, self.data_dir)
        self.mutation = 0
        self.down = False
        self.half_moved = False       # a move by COPY may have stopped halfway
        self.was = {n: self.boxes(n) for n in NAMES}
        self.may = {}                # name: where this step may put it, besides where it is

    # ── what's on the server and in the lists ──────────────────────────

    def boxes(self, name):
        return {box for _, box, _, _ in self.world.where(name)["server"]}

    def pk_in_inbox(self, name):
        from mailmind.bootstrap.mailbox import store
        c = store.conn()
        try:
            r = c.execute("SELECT pk FROM messages WHERE account=? AND folder='INBOX' AND message_id=? "
                          "AND pending_removal IS NULL", (w.ADA, w.message_id(name))).fetchone()
            return r and r["pk"]
        finally:
            c.close()

    def rows(self):
        from mailmind.bootstrap.mailbox import store
        c = store.conn()
        try:
            return [dict(r) for r in c.execute("SELECT * FROM mail_commands ORDER BY id")]
        finally:
            c.close()

    def open_rows(self):
        return [r for r in self.rows() if r["state"] in ("queued", "running")]

    def stuck_rows(self):
        return [r for r in self.rows() if r["state"] == "error"]

    def name_of(self, row):
        return next(n for n in NAMES if w.message_id(n) == row["message_id"])

    def allow(self, name, *boxes):
        self.may.setdefault(name, set()).update(boxes)

    def allow_row(self, row):
        if row["kind"] != "flag":
            self.allow(self.name_of(row), row["dest"] or OPS[row["kind"]])

    # ── what a person asks ─────────────────────────────────────────────

    @rule(name=st.sampled_from(NAMES), op=st.sampled_from(sorted(OPS)))
    def file_it(self, name, op):
        pk = self.pk_in_inbox(name)
        if not pk or "INBOX" not in self.boxes(name):
            return
        self.mutation += 1
        self.allow(name, OPS[op])
        body = {"pk": pk, "mutation_id": f"m-{self.mutation}"}
        got = mailbox.filing.do_trash(body) if op == "trash" else mailbox.filing.do_write(f"/api/{op}", body)
        assert got["ok"] in (True, False)

    def members(self, name):
        return LUNCH if name in LUNCH else (name,)

    def file_conversation(self, name, op):
        pk = self.pk_in_inbox(name)
        if not pk:
            return
        self.mutation += 1
        for member in self.members(name):
            self.allow(member, OPS[op])
        got = mailbox.filing.do_thread_file(f"/api/thread/{op}", {"pk": pk, "mutation_id": f"t-{self.mutation}"})
        assert got["ok"] in (True, False)

    @rule(name=st.sampled_from(NAMES), op=st.sampled_from(("archive", "trash")))
    def file_the_conversation(self, name, op):
        self.file_conversation(name, op)

    def crash_during_a_conversation(self, name, op, target, attribute, effect):
        with mock.patch.object(target, attribute, effect(getattr(target, attribute))):
            try:
                self.file_conversation(name, op)
            except Crash:
                pass
        self.restart()

    @rule(name=st.sampled_from(LUNCH), op=st.sampled_from(("archive", "trash")))
    def crash_while_accepting_a_conversation(self, name, op):
        before, pushed = self.rows(), []

        def effect(real):
            def die_at_the_second(c, entry, group=None):
                pushed.append(entry)
                if len(pushed) == 2:
                    raise Crash
                real(c, entry, group)
            return die_at_the_second
        self.crash_during_a_conversation(name, op, mailbox.undo, "push_within", effect)
        if len(pushed) == 2:                          # both were still there to accept: neither was
            assert self.rows() == before

    @rule(name=st.sampled_from(NAMES), op=st.sampled_from(("archive", "trash")))
    def crash_in_the_first_try_at_a_conversation(self, name, op):
        from mailmind.bootstrap.mailbox import server_ops

        def effect(_real):
            def die(_row, _first_try=False):
                raise Crash
            return die
        self.crash_during_a_conversation(name, op, server_ops, "apply", effect)

    @rule(name=st.sampled_from(NAMES), flag=st.sampled_from(("flagged", "seen")), on=st.booleans())
    def flag_it(self, name, flag, on):
        pk = self.pk_in_inbox(name)
        if not pk:
            return
        self.mutation += 1
        mailbox.filing.do_write("/api/flag", {"pk": pk, "flag": flag, "on": on, "mutation_id": f"f-{self.mutation}"})

    @rule()
    def undo(self):
        from mailmind.bootstrap.mailbox import store
        from mailmind.platform import undo as platform_undo
        c = store.conn()
        entries = platform_undo.entries(c)
        c.close()
        if entries:                                   # the newest goes back where it came from
            top = entries[-1]
            for d in top.get("children", [top]):      # a conversation's, all of them
                for name in NAMES:
                    if w.message_id(name) == d.get("message_id"):
                        self.allow(name, d.get("src_folder"))
        mailbox.undo.undo_last()

    @rule(which=st.integers(0, 3))
    @precondition(lambda self: self.open_rows() or self.stuck_rows())
    def discard(self, which):
        rows = self.open_rows() + self.stuck_rows()
        answered(mailbox.filing.do_pending_discard, rows[which % len(rows)]["id"])

    @rule(which=st.integers(0, 3))
    @precondition(lambda self: self.stuck_rows())
    def retry(self, which):
        rows = self.stuck_rows()
        answered(mailbox.filing.do_pending_retry, rows[which % len(rows)]["id"])

    # ── what the server and the process do ─────────────────────────────

    @rule()
    def outage_starts(self):
        if not self.down:
            self.down = True
            self.server.drop("SELECT", user=w.ADA, times=None)

    @rule()
    def outage_ends(self):
        self.down = False
        self.server.clear_faults()

    @rule(command=st.sampled_from(FRAGILE))
    def server_refuses_the_next(self, command):
        self.half_moved = self.half_moved or command in HALFWAY
        self.server.fail(command, user=w.ADA)

    @rule(command=st.sampled_from(FRAGILE))
    def connection_drops_before_the_next(self, command):
        self.half_moved = self.half_moved or command in HALFWAY
        self.server.drop(command, user=w.ADA)

    @rule(command=st.sampled_from(FRAGILE))
    def connection_drops_after_the_next(self, command):
        self.half_moved = self.half_moved or command in HALFWAY or command == "UID COPY"
        self.server.drop_after(command, user=w.ADA)

    @rule(which=st.integers(0, 3))
    @precondition(lambda self: self.open_rows())
    def worker_tries(self, which):
        from mailmind.bootstrap.mailbox import commands
        rows = self.open_rows()
        row = rows[which % len(rows)]
        self.allow_row(row)
        commands.apply_one(row["id"])

    def crash_during(self, which, target, attribute, effect):
        from mailmind.bootstrap.mailbox import commands
        rows = self.open_rows()
        row = rows[which % len(rows)]
        self.allow_row(row)
        with mock.patch.object(target, attribute, effect(getattr(target, attribute))):
            try:
                commands.apply_one(row["id"])
            except Crash:
                pass
        self.restart()

    def restart(self):
        from mailmind.bootstrap.mailbox import commands
        from mailmind.bootstrap.mailbox import store
        c = store.conn()
        commands.QUEUE.reset_stuck(c)
        c.commit()
        c.close()

    @rule(which=st.integers(0, 3))
    @precondition(lambda self: self.open_rows())
    def crash_before_the_server_is_asked(self, which):
        from mailmind.bootstrap.mailbox import server_ops

        def effect(_real):
            def die(_row, _first_try=False):
                raise Crash
            return die
        self.crash_during(which, server_ops, "apply", effect)

    @rule(which=st.integers(0, 3))
    @precondition(lambda self: self.open_rows())
    def crash_after_the_server_answered(self, which):
        from mailmind.bootstrap.mailbox import server_ops

        def effect(real):
            def die(row, first_try=False):
                real(row, first_try)
                raise Crash
            return die
        self.crash_during(which, server_ops, "apply", effect)

    @rule(which=st.integers(0, 3))
    @precondition(lambda self: self.open_rows())
    def crash_before_the_local_copy_is_settled(self, which):
        from mailmind.bootstrap.mailbox import commands

        def effect(_real):
            def die(_c, _row):
                raise Crash
            return die
        self.crash_during(which, commands, "_local_mirror", effect)

    def crash_during_a_click(self, name, op, effect):
        """A click dies mid-try: its command is already accepted, so the next start takes it up."""
        from mailmind.bootstrap.mailbox import server_ops
        pk = self.pk_in_inbox(name)
        if not pk or "INBOX" not in self.boxes(name):
            return
        self.mutation += 1
        self.allow(name, OPS[op])
        body = {"pk": pk, "mutation_id": f"m-{self.mutation}"}
        with mock.patch.object(server_ops, "apply", effect(server_ops.apply)):
            try:
                mailbox.filing.do_trash(body) if op == "trash" else mailbox.filing.do_write(f"/api/{op}", body)
            except Crash:
                pass
        self.restart()

    @rule(name=st.sampled_from(NAMES), op=st.sampled_from(sorted(OPS)))
    def crash_in_a_click_before_the_server_is_asked(self, name, op):
        def effect(_real):
            def die(_row, _first_try=False):
                raise Crash
            return die
        self.crash_during_a_click(name, op, effect)

    @rule(name=st.sampled_from(NAMES), op=st.sampled_from(sorted(OPS)))
    def crash_in_a_click_after_the_server_answered(self, name, op):
        def effect(real):
            def die(row, first_try=False):
                real(row, first_try)
                raise Crash
            return die
        self.crash_during_a_click(name, op, effect)

    @rule(folder=st.sampled_from(FOLDERS))
    def sync(self, folder):
        from mailmind.bootstrap import accounts
        from mailmind.bootstrap.mailbox import ingest
        try:
            ingest.sync_folder(accounts.get(w.ADA), folder)
        except Exception:                             # a sync on a bad day: it tries again later
            pass

    # ── what must hold ─────────────────────────────────────────────────

    @invariant()
    def no_message_went_anywhere_it_wasnt_asked_to(self):
        for name in NAMES:
            now = self.boxes(name)
            assert now, f"{name} is on the server nowhere"
            assert now <= self.was[name] | self.may.get(name, set()), (name, self.was[name], now)
            self.was[name] = now                      # two, for a moment: a COPY landed, its answer didn't
        self.may = {}

    @invariant()
    def a_row_is_hidden_only_by_a_change_on_its_way(self):
        from mailmind.bootstrap.mailbox import store
        c = store.conn()
        try:
            stray = c.execute("SELECT pk, pending_removal FROM messages WHERE pending_removal IS NOT NULL AND "
                              "pending_removal NOT IN (SELECT id FROM mail_commands WHERE state IN "
                              "('queued', 'running'))").fetchall()
        finally:
            c.close()
        assert not [dict(r) for r in stray]

    def teardown(self):
        try:
            self.settle()
        finally:
            self.server.__exit__(None, None, None)

    def settle(self):
        """Everything over: the server back, the queue run dry, every folder caught up. Then the
        lists show each message where the server has it, with the flags the server has."""
        from mailmind.bootstrap import accounts
        from mailmind.bootstrap.mailbox import ingest
        from mailmind.bootstrap.mailbox import reconciler
        from mailmind.bootstrap.mailbox import commands
        self.server.clear_faults()
        self.restart()
        for _ in range(8):
            rows = self.open_rows()
            if not rows:
                break
            for row in rows:
                commands.apply_one(row["id"])
        assert not self.open_rows()
        for folder in FOLDERS:
            ingest.sync_folder(accounts.get(w.ADA), folder)
            reconciler.reconcile_account(accounts.get(w.ADA), force=True, folder=folder)
        for name in NAMES:
            where = self.world.where(name)
            # A copy flagged \\Deleted is one whose EXPUNGE didn't happen: nearly gone, listed or not.
            live = [s for s in where["server"] if "\\Deleted" not in s[3]]
            server = {(box, uid): flags for _, box, uid, flags in where["server"]}
            listed = {(r["folder"], r["uid"]): r for r in where["db"]}
            # One place. More only where a move by COPY stopped halfway and was then undone or dropped:
            # nothing can tell that copy from one put there on purpose, so it stays. A stray, never a loss.
            assert len(live) == 1 or (self.half_moved and live), (name, where)
            assert {(box, uid) for _, box, uid, _ in live} <= set(listed) <= set(server), (name, where)
            for at, row in listed.items():
                flags = server[at]
                assert (row["flagged"], row["seen"]) == ("\\Flagged" in flags, "\\Seen" in flags), (name, where)


RUNS = int(os.environ.get("MAILMIND_MACHINE_RUNS", "0"))
STEPS = int(os.environ.get("MAILMIND_MACHINE_STEPS", "14"))


class WithoutMove(Machine):
    has_move = False


@pytest.mark.parametrize("machine", [Machine, WithoutMove], ids=["move", "copy-and-expunge"])
def test_no_change_applies_twice_and_no_copy_is_lost(world_template, data_dir, machine):
    machine.template, machine.data_dir = world_template, data_dir
    run_state_machine_as_test(machine, settings=hsettings(
        max_examples=RUNS or 12, stateful_step_count=STEPS, deadline=None, database=None,
        derandomize=not RUNS, suppress_health_check=list(HealthCheck), report_multiple_bugs=False))
