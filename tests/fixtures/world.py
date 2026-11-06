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
import re

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

# IMAP commands that change mail on the server, as the fake records them
WRITES = {"APPEND", "CREATE", "DELETE", "RENAME", "STORE", "COPY", "MOVE", "EXPUNGE",
          "UID STORE", "UID COPY", "UID MOVE", "UID EXPUNGE"}

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


def message_id(name):
    import email.parser
    return (email.parser.BytesHeaderParser().parsebytes(raw(name)).get("Message-ID") or "").strip()


def variant(name, **headers):
    """Corpus file `name` with some headers replaced, for a test that needs one more message like
    it: variant("ada-inbox-03-newsletter.eml", Message_ID="<issue-219@loom.example.test>").
    Underscores in a keyword become hyphens."""
    data = raw(name)
    head, sep, body = data.partition(b"\r\n\r\n") if b"\r\n\r\n" in data else data.partition(b"\n\n")
    for k, v in headers.items():
        field = k.replace("_", "-").encode()
        head, n = re.subn(rb"(?im)^" + re.escape(field) + rb":[^\r\n]*", field + b": " + v.encode(), head)
        assert n == 1, f"{name} has no single {field.decode()} header"
    return head + sep + body


def populate(server):
    """Create the accounts on `server` and deliver MAIL. Returns {file: (account, folder, uid)}."""
    for email, kind, _, extra in ACCOUNTS:
        server.add_account(email, kind=kind, folders=extra)
    placed = {}
    for name, acct, folder, flags, thread in MAIL:
        uid = server.deliver(acct, folder, raw(name), flags=flags, thread_of=raw(thread) if thread else None)
        placed[name] = (acct, folder, uid)
    return placed


def config(server, smtp=None):
    """The MailMind config for the world's accounts on `server`, sending through `smtp` if given.
    Cy's Gmail asks for IMAPClient, as an account soaking it would: the new door runs it through
    IMAPClient, the old door through imaplib, against the same golden files."""
    accounts = []
    for email, _, alias, _ in ACCOUNTS:
        entry = {**server.config_entry(email), "alias": alias}
        if email == CY:
            entry["imap_adapter"] = "imapclient"
        if smtp is not None:
            entry.update(smtp_host="127.0.0.1", smtp_port=smtp.port, smtp_starttls=True)
        accounts.append(entry)
    return {"accounts": accounts}


def write_config(server, data_dir, smtp=None):
    with open(os.path.join(data_dir, "mailmind.config.json"), "w") as f:
        json.dump(config(server, smtp), f, indent=2)


def connect_smtp(server, smtp):
    """Give each world account a login on `smtp`, and file what Gmail accounts send in their
    [Gmail]/Sent Mail, threaded with the mail it answers, as Gmail itself does."""
    import email.parser
    kinds = {email_: kind for email_, kind, _, _ in ACCOUNTS}
    for user in kinds:
        smtp.add_user(user, server.accounts[user].password)

    def file_sent(delivery):
        if kinds.get(delivery.user) == "gmail":
            head = email.parser.BytesHeaderParser().parsebytes(delivery.data)
            server.deliver(delivery.user, "[Gmail]/Sent Mail", delivery.data, flags=(SEEN,),
                           thread_of=(head.get("In-Reply-To") or "").strip() or None)
    smtp.on_message = file_sent


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
    """A built world for one test: the running fake IMAP and SMTP servers, where each corpus file
    landed, and the data directory holding the database."""

    def __init__(self, server, placed, data_dir, smtp=None):
        self.server, self.placed, self.dir, self.smtp = server, placed, data_dir, smtp

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

    def deliver(self, acct, folder, data, flags=()):
        """Put one more message on the server and run the app's own incremental sync of that
        folder, as the background sync would. Returns its UID."""
        import accounts
        import ingest
        uid = self.server.deliver(acct, folder, data, flags=flags)
        ingest.sync_folder(accounts.get(acct), folder)
        return uid

    def mark(self):
        """A point in the server's command log, for writes()."""
        return len(self.server.commands)

    def writes(self, since=0):
        """The commands since mark() that change mail on the server, as "<account> [<selected
        mailbox>] <command>", without imaplib's random tag."""
        return [f"{c['user']} [{c['mailbox']}] {c['line'].split(' ', 1)[1]}"
                for c in self.server.commands[since:] if c["command"] in WRITES]

    def where(self, name):
        """Where the corpus message `name` is now, found by its Message-ID: on the server (account,
        mailbox, UID, flags) and in the database (account, folder, UID and the flag columns). A row
        a queued move is taking away (pending_removal) is listed nowhere, so it isn't here either."""
        import store
        mid = message_id(name)
        server = []
        with self.server.lock:
            for user, acct in self.server.accounts.items():
                for box_name, box in acct.mailboxes.items():
                    for uid, m in box.messages.items():
                        if (m.headers().get("Message-ID") or "").strip() == mid:
                            server.append([user, box_name, uid, sorted(m.flags)])
        c = store.conn()
        try:
            db = [dict(r) for r in c.execute(
                "SELECT account, folder, uid, seen, flagged, answered, draft, is_junk FROM messages "
                "WHERE message_id=? AND pending_removal IS NULL ORDER BY account, folder, uid", (mid,))]
        finally:
            c.close()
        return {"server": sorted(server), "db": db}

    def where_id(self, message_id):
        """Where on the server the message with this Message-ID is, as where()'s "server" list."""
        with self.server.lock:
            return sorted([user, box_name, uid, sorted(m.flags)]
                          for user, acct in self.server.accounts.items()
                          for box_name, box in acct.mailboxes.items()
                          for uid, m in box.messages.items()
                          if (m.headers().get("Message-ID") or "").strip() == message_id)

    def row(self, name):
        import store
        c = store.conn()
        try:
            return dict(store.get_message(c, self.pk(name)))
        finally:
            c.close()
