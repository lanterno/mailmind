"""A small fictional mail world, built by the app's own ingest.

Three accounts live on the fake IMAP server: Ada on a plain IMAP host, Bea behind Proton Bridge
and Cy on Gmail. Their mail is the .eml corpus beside this file, placed by MAIL below. `build`
delivers it, writes a MailMind config that points at the server, and runs ingest.sync_folder on
every folder that holds mail, so the database is what the app itself makes of that mail.

Every address is on example.test and every name is made up.

Delivery is deterministic: the same MAIL into a new FakeIMAPServer gets the same UIDs and
UIDVALIDITY, so conftest builds the database once and copies it for each test.
"""
import json
import os

ROOT = os.path.dirname(os.path.abspath(__file__))
CORPUS = os.path.join(ROOT, "corpus")

ADA, BEA, CY = "ada@example.test", "bea@example.test", "cy@example.test"

# (email, kind, alias, extra folders)
ACCOUNTS = (
    (ADA, "imap", "Ada", ()),
    (BEA, "proton", "Bea", ("Folders/Clients", "Labels/Important")),
    (CY, "gmail", "Cy", ()),
)

SEEN, ANSWERED, DRAFT, FLAGGED = "\\Seen", "\\Answered", "\\Draft", "\\Flagged"

# (corpus file, account, folder, flags, Gmail thread: the file whose X-GM-THRID it shares)
MAIL = (
    ("ada-inbox-01-lunch.eml", ADA, "INBOX", (SEEN, ANSWERED), None),
    ("ada-inbox-02-lunch-confirm.eml", ADA, "INBOX", (), None),
    ("ada-inbox-03-newsletter.eml", ADA, "INBOX", (), None),
    ("ada-inbox-04-ci.eml", ADA, "INBOX", (SEEN,), None),
    ("ada-inbox-05-receipt.eml", ADA, "INBOX", (), None),
    ("ada-inbox-06-outlook-invite.eml", ADA, "INBOX", (), None),
    ("ada-inbox-07-forward-rfc822.eml", ADA, "INBOX", (FLAGGED,), None),
    ("ada-inbox-08-boundaryless.eml", ADA, "INBOX", (), None),
    ("ada-inbox-09-rfc2047-folded.eml", ADA, "INBOX", (), None),
    ("ada-inbox-10-raw-utf8.eml", ADA, "INBOX", (), None),
    ("ada-inbox-11-html-cid.eml", ADA, "INBOX", (), None),
    ("ada-inbox-12-scam.eml", ADA, "INBOX", (), None),
    ("ada-sent-01-lunch-reply.eml", ADA, "Sent", (SEEN,), None),
    ("ada-drafts-01-thunderbird-v1.eml", ADA, "Drafts", (SEEN, DRAFT), None),
    ("ada-drafts-02-thunderbird-v2.eml", ADA, "Drafts", (SEEN, DRAFT), None),
    ("ada-archive-01-old-thread.eml", ADA, "Archive", (SEEN,), None),
    ("ada-trash-01-promo.eml", ADA, "Trash", (SEEN,), None),
    ("bea-inbox-01-client.eml", BEA, "INBOX", (), None),
    ("bea-inbox-02-security.eml", BEA, "INBOX", (SEEN,), None),
    ("bea-clients-01-harbor.eml", BEA, "Folders/Clients", (SEEN,), None),
    ("bea-drafts-01-apple-mail.eml", BEA, "Drafts", (SEEN, DRAFT), None),
    ("cy-inbox-01-thread-start.eml", CY, "INBOX", (SEEN, ANSWERED), None),
    ("cy-sent-01-thread-reply.eml", CY, "[Gmail]/Sent Mail", (SEEN,), "cy-inbox-01-thread-start.eml"),
    ("cy-inbox-02-thread-end.eml", CY, "INBOX", (), "cy-inbox-01-thread-start.eml"),
    ("cy-inbox-03-unrelated.eml", CY, "INBOX", (), None),
)


def raw(name):
    with open(os.path.join(CORPUS, name), "rb") as f:
        return f.read()


def populate(server):
    """Create the accounts on `server` and deliver MAIL. Returns {file: (account, folder, uid)}."""
    for email, kind, _, extra in ACCOUNTS:
        server.add_account(email, kind=kind, folders=extra)
    placed = {}
    for name, acct, folder, flags, thread in MAIL:
        uid = server.deliver(acct, folder, raw(name), flags=flags, thread_of=raw(thread) if thread else None)
        placed[name] = (acct, folder, uid)
    return placed


def config(server):
    """The MailMind config for the world's accounts on `server`."""
    accounts = []
    for email, _, alias, _ in ACCOUNTS:
        accounts.append({**server.config_entry(email), "alias": alias})
    return {"accounts": accounts}


def write_config(server, data_dir):
    with open(os.path.join(data_dir, "mailmind.config.json"), "w") as f:
        json.dump(config(server), f, indent=2)


def synced_folders():
    """(account, folder) for every folder MAIL puts mail in, INBOX first per account."""
    seen = []
    for _, acct, folder, _, _ in MAIL:
        if (acct, folder) not in seen:
            seen.append((acct, folder))
    return sorted(seen, key=lambda af: ([a for a, *_ in ACCOUNTS].index(af[0]), af[1] != "INBOX"))


def ingest_all():
    """Run the app's own first sync of every folder that holds mail."""
    import accounts
    import ingest
    for acct, folder in synced_folders():
        ingest.sync_folder(accounts.get(acct), folder, full=True)


class World:
    """A built world for one test: the running fake server, where each corpus file landed, and
    the data directory holding the database."""

    def __init__(self, server, placed, data_dir):
        self.server, self.placed, self.dir = server, placed, data_dir

    def uid(self, name):
        return self.placed[name][2]

    def pk(self, name):
        """The message's row in the database, by account, folder and UID."""
        import store
        acct, folder, uid = self.placed[name]
        c = store.conn()
        try:
            r = c.execute("SELECT pk FROM messages WHERE account=? AND folder=? AND uid=?",
                          (acct, folder, uid)).fetchone()
        finally:
            c.close()
        return r["pk"] if r else None

    def row(self, name):
        import store
        c = store.conn()
        try:
            return dict(store.get_message(c, self.pk(name)))
        finally:
            c.close()
