"""The fictional world as the AI's characterization tests read it: the messages they anchor on, a triage result stored by hand, a
row's pk by its Message-ID, and an INBOX renumbered under them."""
from datetime import datetime, timezone

from mailmind.bootstrap.assistant import answers
from stored_rows import q

LUNCH = "ada-inbox-02-lunch-confirm.eml"        # Lin's last word: the anchor of Ada's lunch thread
LUNCH_MID = "<lunch-3@lin.example.test>"
QUILT = "ada-inbox-11-html-cid.eml"             # Lin's quilt photo: a thread of one, inside the tick's 72h
CLIENT = "bea-inbox-01-client.eml"              # Ivo's contract mail, in Bea's account


def execute(sql, *args):
    from mailmind.bootstrap.mailbox import store
    c = store.conn()
    try:
        c.execute(sql, args)
        c.commit()
    finally:
        c.close()


def summarize_as(pk, importance, category="info", action=""):
    """Store the triage result a model would have given this message."""
    from mailmind.bootstrap.mailbox import store
    c = store.conn()
    try:
        answers.save_summary(c, pk, "Stub triage.", importance, category, action, "claude-opus-5-5",
                           datetime.now(timezone.utc).isoformat())
        c.commit()
    finally:
        c.close()


def pk_of(message_id, folder="INBOX"):
    [r] = q("SELECT pk FROM messages WHERE message_id=? AND folder=?", message_id, folder)
    return r["pk"]


def renumber_inbox(world, account):
    """The server renumbers the folder (a UIDVALIDITY change) and the app re-syncs it: every
    INBOX row comes back under a new pk."""
    from mailmind.bootstrap import accounts
    from mailmind.bootstrap.mailbox import ingest
    world.server.renumber(account, "INBOX")
    ingest.sync_new(accounts.get(account))
