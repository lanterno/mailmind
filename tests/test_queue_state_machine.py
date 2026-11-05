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

from fixtures import world as w

NAMES = ("ada-inbox-01-lunch.eml", "ada-inbox-03-newsletter.eml", "ada-inbox-05-receipt.eml")
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
        import store
        c = store.conn()
        try:
            r = c.execute("SELECT pk FROM messages WHERE account=? AND folder='INBOX' AND message_id=? "
                          "AND pending_removal IS NULL", (w.ADA, w.message_id(name))).fetchone()
            return r and r["pk"]
        finally:
            c.close()

    def rows(self):
        import store
        c = store.conn()
        try:
            return [dict(r) for r in c.execute("SELECT * FROM read_mutations ORDER BY id")]
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
        if row["op"] != "flag":
            self.allow(self.name_of(row), row["dest"] or OPS[row["op"]])

    # ── what a person asks ─────────────────────────────────────────────

    @rule(name=st.sampled_from(NAMES), op=st.sampled_from(sorted(OPS)))
    def file_it(self, name, op):
        import server
        pk = self.pk_in_inbox(name)
        if not pk or "INBOX" not in self.boxes(name):
            return
        self.mutation += 1
        self.allow(name, OPS[op])
        body = {"pk": pk, "mutation_id": f"m-{self.mutation}"}
        got = server.do_trash(body) if op == "trash" else server.do_write(f"/api/{op}", body)
        assert got["ok"] in (True, False)

    @rule(name=st.sampled_from(NAMES), flag=st.sampled_from(("flagged", "seen")), on=st.booleans())
    def flag_it(self, name, flag, on):
        import server
        pk = self.pk_in_inbox(name)
        if not pk:
            return
        self.mutation += 1
        server.do_write("/api/flag", {"pk": pk, "flag": flag, "on": on, "mutation_id": f"f-{self.mutation}"})

    @rule()
    def undo(self):
        import server
        import store
        c = store.conn()
        entries = store.undo_entries(c)
        c.close()
        if entries:                                   # the newest goes back where it came from
            top = entries[-1]
            for name in NAMES:
                if w.message_id(name) == top.get("message_id"):
                    self.allow(name, top.get("src_folder"))
        server.do_undo()

    @rule(which=st.integers(0, 3))
    @precondition(lambda self: self.open_rows() or self.stuck_rows())
    def discard(self, which):
        import server
        rows = self.open_rows() + self.stuck_rows()
        server.do_pending_discard(rows[which % len(rows)]["id"])

    @rule(which=st.integers(0, 3))
    @precondition(lambda self: self.stuck_rows())
    def retry(self, which):
        import server
        rows = self.stuck_rows()
        server.do_pending_retry(rows[which % len(rows)]["id"])

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
        import readmutq
        rows = self.open_rows()
        row = rows[which % len(rows)]
        self.allow_row(row)
        readmutq.apply_one(row["id"])

    def crash_during(self, which, target, attribute, effect):
        import readmutq
        rows = self.open_rows()
        row = rows[which % len(rows)]
        self.allow_row(row)
        with mock.patch.object(target, attribute, effect(getattr(target, attribute))):
            try:
                readmutq.apply_one(row["id"])
            except Crash:
                pass
        self.restart()

    def restart(self):
        import readmutq
        import store
        c = store.conn()
        readmutq.QUEUE.reset_stuck(c)
        c.commit()
        c.close()

    @rule(which=st.integers(0, 3))
    @precondition(lambda self: self.open_rows())
    def crash_before_the_server_is_asked(self, which):
        import mailops

        def effect(_real):
            def die(_row):
                raise Crash
            return die
        self.crash_during(which, mailops, "apply_queued_mutation", effect)

    @rule(which=st.integers(0, 3))
    @precondition(lambda self: self.open_rows())
    def crash_after_the_server_answered(self, which):
        import mailops

        def effect(real):
            def die(row):
                real(row)
                raise Crash
            return die
        self.crash_during(which, mailops, "apply_queued_mutation", effect)

    @rule(which=st.integers(0, 3))
    @precondition(lambda self: self.open_rows())
    def crash_before_the_local_copy_is_settled(self, which):
        import readmutq

        def effect(_real):
            def die(_row):
                raise Crash
            return die
        self.crash_during(which, readmutq, "_local_mirror", effect)

    @rule(folder=st.sampled_from(FOLDERS))
    def sync(self, folder):
        import accounts
        import ingest
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
        import store
        c = store.conn()
        try:
            stray = c.execute("SELECT pk, pending_removal FROM messages WHERE pending_removal IS NOT NULL AND "
                              "pending_removal NOT IN (SELECT id FROM read_mutations WHERE state IN "
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
        import accounts
        import ingest
        import readmutq
        self.server.clear_faults()
        self.restart()
        for _ in range(8):
            rows = self.open_rows()
            if not rows:
                break
            for row in rows:
                readmutq.apply_one(row["id"])
        assert not self.open_rows()
        for folder in FOLDERS:
            ingest.sync_folder(accounts.get(w.ADA), folder)
            ingest.reconcile_account(accounts.get(w.ADA), force=True, folder=folder)
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
