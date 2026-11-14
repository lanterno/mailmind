"""Warming more than the INBOX (mailmind.mailbox.application.warm): which folders an account's Drafts, Archive and Sent
are, found over one connection and kept, and which of them each tick pulls. Whether it runs at all is
test_pool_and_warming_by_account's."""
import imaplib
from types import SimpleNamespace

import pytest

from mailmind.mailbox.application import warm
from mailmind.mailbox.application.warm import WARM_RECENT, Warming
from mailmind.mailbox.domain.roles import Folder

ADA, BEA = "ada@example.test", "bea@example.test"
FOLDERS = [Folder("INBOX"), Folder("Drafts", frozenset({"\\drafts"})), Folder("Archive", frozenset({"\\archive"})),
           Folder("All Mail", frozenset({"\\all"})), Folder("Sent Items", frozenset({"\\sent"}))]


class _Session:
    def __init__(self, folders=FOLDERS, lists=True, logs_out=True, lost=False):
        self._folders, self._lists, self._logs_out, self._lost = folders, lists, logs_out, lost
        self.logged_out = 0

    def folders(self):
        if self._lost:
            raise imaplib.IMAP4.abort("the connection went")
        if not self._lists:
            raise imaplib.IMAP4.error("LIST refused")
        return self._folders

    def logout(self):
        self.logged_out += 1
        if not self._logs_out:
            raise OSError("the connection went")


class _Server:
    def __init__(self, sessions):
        self.sessions = sessions
        self.opened = []

    def __call__(self, acct):
        self.opened.append(acct["email"])
        got = self.sessions[acct["email"]]
        if isinstance(got, Exception):
            raise got
        return got


@pytest.fixture(autouse=True)
def no_pause(monkeypatch):
    monkeypatch.delenv("MAILMIND_WARM_FOLDERS", raising=False)
    monkeypatch.setattr(warm, "time", SimpleNamespace(sleep=lambda seconds: None))


def warming(sessions, *, gmail=(), failing=()):
    """A Warming over accounts that each ask to be warmed, and what each tick synced: (account, folder)."""
    synced = []

    def sync_folder(acct, folder, *, full, recent):
        assert (full, recent) == (False, WARM_RECENT)
        synced.append((acct["email"], folder))
        if folder in failing:
            raise imaplib.IMAP4.abort("the connection went")
        return (1, 0, 99)
    accounts = SimpleNamespace(accounts=lambda: [{"email": e, "warm_folders": True} for e in sessions],
                               profile=lambda a: SimpleNamespace(archive_is_label_removal=a["email"] in gmail))
    server = _Server(sessions)
    w = Warming(accounts, server, sync_folder)
    w.synced, w.server = synced, server
    return w


def ticks(w, n, emails=None):
    out = []
    for _ in range(n):
        del w.synced[:]
        w.warm_tick(emails)
        out.append(list(w.synced))
    return out


def test_the_roles_are_found_over_one_connection_and_kept():
    session = _Session()
    w = warming({ADA: session})
    ticks(w, 3)
    assert (w.server.opened, session.logged_out) == ([ADA], 1)


def test_all_mail_plays_the_archive_ahead_of_a_folder_named_archive():
    w = warming({ADA: _Session()})
    assert ticks(w, 2)[1] == [(ADA, "Drafts"), (ADA, "All Mail")]


def test_drafts_go_every_tick_the_archive_every_second_and_sent_every_third():
    w = warming({ADA: _Session()})
    assert ticks(w, 6) == [
        [(ADA, "Drafts")],
        [(ADA, "Drafts"), (ADA, "All Mail")],
        [(ADA, "Drafts"), (ADA, "Sent Items")],
        [(ADA, "Drafts"), (ADA, "All Mail")],
        [(ADA, "Drafts")],
        [(ADA, "Drafts"), (ADA, "All Mail"), (ADA, "Sent Items")],
    ]


def test_gmails_all_mail_holds_the_inbox_so_it_isnt_warmed():
    w = warming({ADA: _Session()}, gmail={ADA})
    assert ticks(w, 6)[-1] == [(ADA, "Drafts"), (ADA, "Sent Items")]


def test_a_role_no_folder_plays_is_passed_over():
    w = warming({ADA: _Session([Folder("INBOX"), Folder("Sent", frozenset({"\\sent"}))])})
    assert ticks(w, 6) == [[], [], [(ADA, "Sent")], [], [], [(ADA, "Sent")]]


def test_a_role_the_inbox_plays_is_passed_over():
    w = warming({ADA: _Session([Folder("INBOX", frozenset({"\\drafts"}))])})
    assert ticks(w, 6) == [[]] * 6


def test_a_list_the_server_refuses_warms_nothing():
    w = warming({ADA: _Session(lists=False)})
    assert ticks(w, 6) == [[]] * 6


@pytest.mark.xfail(strict=True, reason="a connection lost while listing keeps no folder for any part, until a restart")
def test_a_connection_lost_while_the_folders_are_listed_is_tried_again_next_tick():
    w = warming({ADA: _Session(lost=True)})
    assert ticks(w, 1) == [[]]
    w.server.sessions[ADA] = _Session()
    assert ticks(w, 1) == [[(ADA, "Drafts"), (ADA, "All Mail")]]


def test_an_account_that_cant_be_reached_is_tried_again_next_tick_and_the_others_go_on(capsys):
    w = warming({ADA: OSError("no route to host"), BEA: _Session()})
    assert ticks(w, 2) == [[(BEA, "Drafts")], [(BEA, "Drafts"), (BEA, "All Mail")]]
    assert w.server.opened == [ADA, BEA, ADA]
    assert capsys.readouterr().out.count("[warm] resolve <withheld>: OSError\n") == 2


def test_a_logout_that_fails_still_keeps_the_roles():
    session = _Session(logs_out=False)
    w = warming({ADA: session})
    assert ticks(w, 2)[1] == [(ADA, "Drafts"), (ADA, "All Mail")]
    assert w.server.opened == [ADA]


def test_a_folder_that_fails_leaves_the_rest_of_the_tick_to_go():
    w = warming({ADA: _Session(), BEA: _Session()}, failing={"Drafts"})
    assert ticks(w, 2)[1] == [(ADA, "Drafts"), (ADA, "All Mail"), (BEA, "Drafts"), (BEA, "All Mail")]


def test_a_tick_for_some_accounts_warms_only_those():
    w = warming({ADA: _Session(), BEA: _Session()})
    assert ticks(w, 1, emails={BEA}) == [[(BEA, "Drafts")]]
    assert w.server.opened == [BEA]
