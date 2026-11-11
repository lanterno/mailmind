"""Expiry against the live server (mailmind.curation.application.expiry.Expiry), on fakes behind Curation's ports: the
background round over every account, one account's turn on its gate, and one account's pass, where the audit comes
before the move and a failure is an answer, never a raise."""
import imaplib
import threading
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from mailmind.curation.application.expiry import Expiry
from mailmind.curation.domain.rulebook import EXPIRE
from mailmind.platform.workers import gates

ADA, BEA = "ada@example.test", "bea@example.test"
OLD = datetime(2020, 1, 1, tzinfo=timezone.utc)
RULES = {"notify-example.test": 72}


def headers(uid, sender, when=OLD):
    raw = f"From: {sender}\r\nSubject: note {uid}\r\nMessage-ID: <n{uid}@example.test>\r\n\r\n".encode()
    return SimpleNamespace(uid=uid, raw=raw, internaldate=when)


MAIL = [headers(1, "bot@notify-example.test"), headers(2, "alerts@notify-example.test"),
        headers(3, "bot@notify-example.test", datetime.now(timezone.utc))]


class _Session:
    def __init__(self, said, mail=MAIL, searches=None, logs_out=True):
        self.said, self.mail, self.searches, self.logs_out = said, mail, searches or {}, logs_out

    def select(self, folder):
        self.said.append(("select", folder))

    def uids(self, *criteria):
        self.said.append(("search", criteria))
        refusal = self.searches.get(criteria[-1])
        if refusal:
            raise refusal
        return [h.uid for h in self.mail]

    def headers(self, uids, fields, *, internaldate=False):
        return [h for h in self.mail if h.uid in uids]

    def logout(self):
        self.said.append(("logout",))
        if not self.logs_out:
            raise OSError("the connection went")


class World:
    """Every port Expiry has, saying in one list what was asked of each, in order."""

    def __init__(self, rules=RULES, emails=(ADA,), **session):
        self.said = []
        self.rules = rules
        self.emails = list(emails)
        self.session = session
        self.opening = {}            # email -> what opening its session raises
        self.folders_failing = set()
        self.stragglers = []

    # RuleBookRepository
    def load(self):
        return {EXPIRE: dict(self.rules)}

    # Mailboxes
    def all(self):
        self.said.append(("all",))
        return [{"email": e} for e in self.emails]

    def open_session(self, acct):
        self.said.append(("open", acct["email"]))
        if acct["email"] in self.opening:
            raise self.opening[acct["email"]]
        return _Session(self.said, **self.session)

    def quote(self, value):
        return f'"{value}"'

    def trash_folder(self, acct):
        return "Trash"

    def move_verified(self, session, uids, dest):
        self.said.append(("move", uids, dest))
        return len(uids) - len(self.stragglers), list(self.stragglers)

    # SweepTrail
    def swept(self, account, rows):
        self.said.append(("audit", account, [(r["uid"], r["reason"]) for r in rows]))

    # SenderMail
    def forget_expired(self, account, targets, stragglers):
        self.said.append(("forget", account, [t["uid"] for t in targets], stragglers))

    # FilingCommands
    def folders_changed(self, account, moved):
        self.said.append(("folders_changed", account, moved))
        if account in self.folders_failing:
            raise RuntimeError(f"{account}: the database is locked")

    def expiry(self):
        return Expiry(self, self, self, self, self)


def asked(world, *kinds):
    return [s for s in world.said if s[0] in kinds]


# -- one account's pass ------------------------------------------------------------------------------------------

def test_mail_past_its_rule_is_audited_then_moved_then_forgotten():
    world = World()
    out = world.expiry().account({"email": ADA})
    assert out == {"account": ADA, "planned": 2, "moved": 2, "stragglers": [], "error": None}
    assert asked(world, "select", "search", "audit", "move", "forget", "logout") == [
        ("select", "INBOX"),
        ("search", ("FROM", '"notify-example.test"')),
        ("audit", ADA, [("1", "expire rule: older than 72h"), ("2", "expire rule: older than 72h")]),
        ("move", ["1", "2"], "Trash"),
        ("logout",),
        ("forget", ADA, ["1", "2"], []),
    ]


def test_what_wont_move_comes_back_as_stragglers_and_isnt_forgotten_as_moved():
    world = World()
    world.stragglers = ["2"]
    out = world.expiry().account({"email": ADA})
    assert (out["moved"], out["stragglers"]) == (1, ["2"])
    assert asked(world, "forget") == [("forget", ADA, ["1", "2"], ["2"])]


def test_a_dry_run_says_what_it_would_take_and_takes_nothing():
    world = World()
    out = world.expiry().account({"email": ADA}, dry=True)
    assert (out["planned"], out["moved"]) == (2, 0)
    assert asked(world, "audit", "move", "forget") == [] and asked(world, "logout") == [("logout",)]


def test_with_no_rules_no_server_is_asked():
    world = World(rules={})
    assert world.expiry().account({"email": ADA})["planned"] == 0
    assert world.said == []


def test_a_search_the_server_refuses_finds_nothing_and_the_other_rules_go_on():
    world = World(rules={"notify-example.test": 72, "news.example.test": 24},
                  searches={'"news.example.test"': imaplib.IMAP4.error("SEARCH refused")})
    assert world.expiry().account({"email": ADA})["moved"] == 2


@pytest.mark.parametrize("raised, error", [
    (imaplib.IMAP4.abort("the connection went"), "abort"),
    (OSError("no route to host"), "OSError"),
])
def test_a_connection_lost_is_an_error_said_by_its_class_and_nothing_moves(raised, error):
    world = World(searches={'"notify-example.test"': raised})
    out = world.expiry().account({"email": ADA})
    assert (out["error"], out["moved"]) == (error, 0)
    assert asked(world, "audit", "move", "forget") == [] and asked(world, "logout") == [("logout",)]


def test_an_account_that_cant_be_reached_is_an_error_said_by_its_class():
    world = World()
    world.opening[ADA] = OSError(f"{ADA}: no route to host")
    out = world.expiry().account({"email": ADA})
    assert (out["error"], out["planned"]) == ("OSError", 0)


def test_a_logout_that_fails_takes_nothing_back():
    world = World(logs_out=False)
    assert world.expiry().account({"email": ADA})["moved"] == 2
    assert asked(world, "forget") == [("forget", ADA, ["1", "2"], [])]


# -- one account's turn on its gate ------------------------------------------------------------------------------

def test_a_turn_that_moved_mail_says_the_folders_changed(capsys):
    world = World()
    world.expiry().auto_one({"email": ADA})
    assert asked(world, "folders_changed") == [("folders_changed", ADA, 2)]
    assert capsys.readouterr().out == "[expire] <withheld>: moved 2 expired message(s) to Trash\n"


def test_a_turn_that_moved_nothing_says_nothing(capsys):
    world = World(mail=[MAIL[2]])
    world.expiry().auto_one({"email": ADA})
    assert asked(world, "folders_changed") == [] and capsys.readouterr().out == ""


def test_a_turn_that_failed_says_only_the_class(capsys):
    world = World()
    world.opening[ADA] = OSError(f"{ADA}: no route to host")
    world.expiry().auto_one({"email": ADA})
    assert asked(world, "folders_changed") == []
    assert capsys.readouterr().out == "[expire] an account failed: OSError\n[expire] <withheld>: OSError\n"


def test_a_turn_gives_way_to_whoever_holds_the_accounts_gate():
    world = World()
    inside, release = threading.Event(), threading.Event()
    holder = threading.Thread(target=lambda: gates.run(ADA, lambda: (inside.set(), release.wait(5)),
                                                       priority=gates.Priority.INTERACTIVE), daemon=True)
    holder.start()
    try:
        assert inside.wait(5)
        world.expiry().auto_one({"email": ADA})
    finally:
        release.set()
        holder.join(5)
    assert world.said == []


# -- the background round ----------------------------------------------------------------------------------------

def test_a_round_with_no_rules_reaches_no_account():
    world = World(rules={}, emails=(ADA, BEA))
    world.expiry().round()
    assert world.said == []


def test_a_round_gives_each_account_its_turn_and_one_failing_stops_none(capsys):
    world = World(emails=(ADA, BEA))
    world.folders_failing = {ADA}
    world.expiry().round()
    assert asked(world, "folders_changed") == [("folders_changed", ADA, 2), ("folders_changed", BEA, 2)]
    assert capsys.readouterr().out == ("[expire] loop error: RuntimeError\n"
                                       "[expire] <withheld>: moved 2 expired message(s) to Trash\n")
