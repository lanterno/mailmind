"""A state machine over the undo stack, against a plain model of it: under random pushes, groups, undos that work or fail,
forgetting, restarts and the clock moving on, it stays last-in-first-out, holds at most UNDO_MAX groups and nothing older
than KEEP_FOR, and a group comes off whole. test_undo_stack.py has its worked cases. test_queue_state_machine.py runs
Undo with the real reversers, against a server; here a reverser is a fake that does as it's told.

MAILMIND_MACHINE_RUNS asks for that many runs, with fresh random seeds, and MAILMIND_MACHINE_STEPS for longer ones.
"""
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import time_machine
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, rule, run_state_machine_as_test

from mailmind.bootstrap.mailbox import store
from mailmind.platform import undo
from property_runs import runs

START = datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc)
ACCOUNTS = ("ada@example.test", "bea@example.test")
HELD = 4                       # the machine's UNDO_MAX, low enough for a short run to reach it
STEPS = int(os.environ.get("MAILMIND_MACHINE_STEPS", "30"))


@dataclass
class Held:
    """What the stack should hold in one place: a move alone, or a group of them."""
    born: datetime
    group: str | None
    moves: list = field(default_factory=list)


class Machine(RuleBasedStateMachine):
    def __init__(self):
        super().__init__()
        self.travel = time_machine.travel(START, tick=False)
        self.clock = self.travel.start()
        c = store.conn()
        c.execute("DELETE FROM undo_entries")
        c.commit()
        c.close()
        self.undo = self.fresh()
        self.model = []          # oldest first
        self.account_of = {}
        self.tried = []
        self.refuses = lambda n: False
        self.raises = False

    def fresh(self):
        made = undo.Undo(store.conn, {"move": self.reverse}, lambda: None, lambda e: type(e).__name__)
        made.UNDO_MAX = HELD
        return made

    def reverse(self, d):
        self.tried.append(d["n"])
        if self.raises:
            raise RuntimeError("the connection went")
        return not self.refuses(d["n"]), d["account"], "INBOX"

    def teardown(self):
        self.travel.stop()

    def now(self):
        return datetime.now(timezone.utc)

    def expire(self):
        self.model = [h for h in self.model if h.born >= self.now() - undo.KEEP_FOR]

    def a_move(self, account):
        n = len(self.account_of) + 1
        self.account_of[n] = account
        return {"kind": "move", "account": account, "n": n, "label": f"Move {n}"}

    def keep_newest(self):
        del self.model[:-HELD]

    # ── what someone can do ───────────────────────────────────────────

    @rule(account=st.sampled_from(ACCOUNTS))
    def push_alone(self, account):
        move = self.a_move(account)
        self.undo.push(move)
        self.expire()
        self.model.append(Held(self.now(), None, [move["n"]]))
        self.keep_newest()

    @rule(group=st.sampled_from(("g", "h")), account=st.sampled_from(ACCOUNTS))
    def push_in_a_group(self, group, account):
        move = self.a_move(account)
        self.undo.push(move, group)
        self.expire()
        if self.model and self.model[-1].group == group:
            self.model[-1].moves.append(move["n"])
        else:
            self.model.append(Held(self.now(), group, [move["n"]]))
        self.keep_newest()

    @rule(size=st.integers(2, 4), account=st.sampled_from(ACCOUNTS))
    def push_a_whole_group(self, size, account):
        """As a bulk action does, under a group id of its own."""
        group = f"bulk-{len(self.account_of)}"
        moves = [self.a_move(account) for _ in range(size)]
        for move in moves:
            self.undo.push(move, group)
        self.expire()
        self.model.append(Held(self.now(), group, [move["n"] for move in moves]))
        self.keep_newest()

    @rule()
    def push_nothing(self):
        self.undo.push(None)
        self.undo.push({}, "g")

    @rule(outcome=st.sampled_from(("works", "refused", "raises", "odd ones refused")))
    def undo_the_last(self, outcome):
        self.expire()
        self.tried = []
        self.raises = outcome == "raises"
        self.refuses = {"works": lambda n: False, "refused": lambda n: True, "raises": lambda n: True,
                        "odd ones refused": lambda n: n % 2 == 1}[outcome]
        answer, code = self.undo.undo_last()
        assert code == 200
        if not self.model:
            assert answer == {"ok": False, "empty": True} and self.tried == []
            return
        top = self.model.pop()
        assert self.tried == top.moves[::-1], "a group is undone newest first, and every member is tried"
        left = [n for n in top.moves if self.refuses(n)]
        if left:                                         # back on top, as young as it now is
            self.model.append(Held(self.now(), top.group, left))
        assert answer["ok"] is (len(left) < len(top.moves))
        if top.group and answer["ok"]:
            assert (answer["reversed"], answer["failed"]) == (len(top.moves) - len(left), len(left))
        if answer["ok"]:
            assert answer["remaining"] == len(self.model)

    @rule(account=st.sampled_from(ACCOUNTS))
    def forget(self, account):
        """The newest move on this account has nothing left to reverse."""
        self.undo._undo_db(lambda c: undo.forget(c, lambda e: e["account"] == account))
        self.expire()
        for held in reversed(self.model):
            found = next((n for n in reversed(held.moves) if self.account_of[n] == account), None)
            if found is not None:
                held.moves.remove(found)
                if not held.moves:
                    self.model.remove(held)
                return

    @rule(by=st.sampled_from((timedelta(seconds=1), timedelta(hours=6), timedelta(hours=23, minutes=59, seconds=59),
                              timedelta(hours=24), timedelta(hours=24, seconds=1), timedelta(days=3))))
    def time_passes(self, by):
        self.clock.shift(by)

    @rule()
    def restart(self):
        self.undo = self.fresh()

    # ── what must hold ────────────────────────────────────────────────

    @invariant()
    def the_stack_is_the_model(self):
        self.expire()
        held = self.undo.entries()
        assert len(held) <= HELD
        got = [([c["n"] for c in e["children"]], e["group"]) if e["kind"] == "bulk" else ([e["n"]], None) for e in held]
        assert got == [(h.moves, h.group) for h in self.model]


def test_the_undo_stack_is_what_a_plain_model_of_it_says(data_dir):
    run_state_machine_as_test(Machine, settings=runs(40, stateful_step_count=STEPS))
