"""Which folder plays which part (mailmind.mailbox.domain.roles), and how an imaplib LIST answer
reads (mailmind.mailbox.adapters.imap.imap_list): SPECIAL-USE first, then whole names, never a part of
one; a shallower path before a deeper one; and the rail and the moves through the app."""
import imaplib


from fixtures import world as w
from mailmind.mailbox.adapters.imap import imap_list
from mailmind.mailbox.domain.roles import Folder


def folders(*specs):
    """Folders from (name, attributes...) pairs, with "/" as the delimiter."""
    return [Folder(name, frozenset(a.lower() for a in attrs)) for name, *attrs in specs]


# ── through the fake server and the app ────────────────────────────────────────────────────────

def test_on_the_fake_each_role_finds_its_special_use_folder(world):
    from mailmind.bootstrap import accounts
    from mailmind.bootstrap import mailbox
    from mailmind.mailbox.application.session import find_role
    found = {}
    for acct in (w.ADA, w.BEA, w.CY):
        s = mailbox.open_session(accounts.get(acct), timeout=10)
        try:
            found[acct] = {r: find_role(s, r) for r in ("sent", "drafts", "archive", "all", "junk", "trash")}
        finally:
            s.logout()
    assert found == {
        w.ADA: {"sent": "Sent", "drafts": "Drafts", "archive": "Archive", "all": None, "junk": "Junk", "trash": "Trash"},
        w.BEA: {"sent": "Sent", "drafts": "Drafts", "archive": "Archive", "all": "All Mail", "junk": "Spam",
                "trash": "Trash"},
        w.CY: {"sent": "[Gmail]/Sent Mail", "drafts": "[Gmail]/Drafts", "archive": None, "all": "[Gmail]/All Mail",
               "junk": "[Gmail]/Spam", "trash": "[Gmail]/Trash"}}


def test_the_rail_gives_each_role_to_one_folder(app, world):
    from fakes.imap_server import Mailbox
    a = world.server.accounts[w.BEA]
    with world.server.lock:                          # a user folder named like a system one
        a.mailboxes["Folders/Archive"] = Mailbox("Folders/Archive", uidvalidity=1_700_000_998)
    rail = {f["name"]: f["role"] for f in app.get(f"/api/folders?account={w.BEA}").json["folders"]}
    assert (rail["Archive"], rail["All Mail"], rail["Folders/Archive"], rail["Spam"]) == \
        ("archive", "all", "custom", "junk")


def test_archive_on_gmail_without_an_archive_folder_still_files_by_label(app, world):
    name = "cy-inbox-02-thread-end.eml"
    assert app.post("/api/archive", world.copy(name)).json["ok"]
    assert (w.CY, "INBOX") not in world.where(name)["server"]


def test_imaplib_is_what_the_parser_reads(world):
    """The parser reads what imaplib hands back from a real LIST, not a guess at it."""
    from mailmind.bootstrap import accounts
    M = accounts.connect(accounts.get(w.BEA), timeout=10)
    try:
        typ, data = M.list()
    finally:
        M.logout()
    assert typ == "OK" and isinstance(M, (imaplib.IMAP4, accounts.ReadOnlyIMAP))
    assert {f.name for f in imap_list.parse(data)} >= {"INBOX", "Archive", "All Mail", "Spam", "Folders/Clients"}


def test_an_account_out_of_reach_shows_the_folders_it_has_stored(app, world):
    from mailmind.bootstrap.mailbox import store
    world.server.drop("LOGIN", user=w.BEA, times=None)          # Bridge down, before any list was cached
    r = app.get(f"/api/folders?account={w.BEA}")
    assert r.status == 200
    c = store.conn()
    try:
        stored = {row["folder"]: (row["unseen"], row["total"]) for row in c.execute(
            "SELECT folder, SUM(seen=0) AS unseen, COUNT(*) AS total FROM messages WHERE account=? GROUP BY folder",
            (w.BEA,))}
    finally:
        c.close()
    assert r.json["folders"][0] == {"name": "INBOX", "role": "inbox", "unseen": stored["INBOX"][0],
                                    "total": stored["INBOX"][1], "takes_filing": True}
    assert {f["name"] for f in r.json["folders"]} >= set(stored)
