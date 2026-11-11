"""Reopening a saved draft (compose's resume helpers): a reply or forward draft already holds its
quote or forwarded section, so it must come back split into your text and that tail, and go out
again with each part once. Every IMAP or SMTP call a save or a send makes is replaced with a
recorder (`calls`), so nothing can leave. All addresses are fictional."""
import itertools
import json
from email import message_from_bytes

import pytest

from answered import answered
from fixtures import world as w
from mailmind.bootstrap import mailbox
from mailmind.bootstrap import compose as composer      # `compose` is the context's api, below
from mailmind.bootstrap.mailbox import store
from mailmind.content import mime
from mailmind.platform import settings

ACCT = "ada@example.test"
CONFIG = {"accounts": [{"email": ACCT, "type": "imap", "host": "imap.example.test"},
                       {"email": "zed@example.test", "type": "imap", "host": "imap.example.test"}]}


class Calls:
    """What the recorders heard, and how the server they stand in for answers: `uidv` is the UIDVALIDITY it names
    (None: it names none), and with `appenduid` False it names no UID for what was appended."""

    def __init__(self):
        self.appended, self.deleted, self.sent = [], [], []
        self.uidv, self.appenduid = 777, True
        self.uids = itertools.count(101)


@pytest.fixture
def home(data_dir):
    """A data directory holding the schema and the config."""
    with open(settings.current().config, "w") as f:
        json.dump(CONFIG, f)
    yield data_dir


@pytest.fixture
def recorders(monkeypatch):
    """Recorders in place of every IMAP call a draft save makes, and of the outbox."""
    calls = Calls()

    def append(acct, raw, prev_uid=None, prev_uidvalidity=None, prev_msgid=None, prev_folder=None, message_id=None, tried=None):
        uid = next(calls.uids)
        calls.appended.append({"account": acct["email"], "raw": raw, "prev_uid": prev_uid,
                               "prev_uidvalidity": prev_uidvalidity, "prev_msgid": prev_msgid,
                               "prev_folder": prev_folder})
        return "Drafts", (uid if calls.appenduid else None), calls.uidv, None

    def delete(acct, folder, uid, uidvalidity=None):
        calls.deleted.append((acct["email"], folder, uid, uidvalidity))

    def enqueue(built, meta):
        calls.sent.append((built, meta))
        return len(calls.sent), True
    monkeypatch.setattr(mailbox.copies, "append_to_drafts", append)
    monkeypatch.setattr(mailbox.copies, "delete_draft_uid", delete)
    monkeypatch.setattr(composer.outbox, "enqueue", enqueue)
    monkeypatch.setattr(composer.drafts, "_live", lambda acct, folder, uid: True)     # the server still holds the copy
    return calls


@pytest.fixture
def calls(home, recorders):
    return recorders


def _save(account=ACCT, draft_id=None, **kw):
    body = {"mode": "new", "account": account, "to": ["bo@example.test"], "subject": "Plans",
            "text": "Saturday works.", "draft_id": draft_id, **kw}
    res, code = answered(composer.drafts.save, body)
    assert code == 200 and res["ok"], (code, res)
    return res


def _synced(raw, uid, account=ACCT, folder="Drafts"):
    """A Drafts message as sync would store it once fetched: headers read from the bytes the way
    ingest reads them (compat32, ingest.dec), body kept, the folder's UIDVALIDITY known. Returns
    its pk."""
    m = message_from_bytes(raw)
    c = store.conn()
    store.set_uidvalidity(c, account, folder, 777)
    key = store.mint_key(c, account, (m["Message-ID"] or "").strip(), account, None, mime.dec(m["Subject"]))
    c.execute("INSERT INTO messages (account, kind, folder, uid, message_id, thread_id, ts, from_addr, subject, "
              "in_reply_to, refs, message_key) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
              (account, "imap", folder, uid, (m["Message-ID"] or "").strip(), f"td{uid}", 1_800_000_100, account,
               mime.dec(m["Subject"]), m["In-Reply-To"] or None, m["References"] or None, key))
    pk = c.execute("SELECT pk FROM messages WHERE folder=? AND uid=?", (folder, uid)).fetchone()["pk"]
    store.store_body(c, pk, raw, "2026-10-31T10:00:00")
    c.commit()
    c.close()
    return pk


def _saved_and_synced(calls, **kw):
    """Save a draft through do_draft, then sync back exactly the bytes appended. (pk, save result)"""
    res = _save(**kw)
    return _synced(calls.appended[-1]["raw"], res["uid"] if res["uid"] is not None else 555), res


def test_resume_route_requires_csrf(app, recorders):
    pk, _ = _saved_and_synced(recorders, text="Mine.")
    assert app.post("/api/draft/resume", w.copy_of(pk), headers={"X-CSRF-Token": None}).status == 403
    r = app.post("/api/draft/resume", w.copy_of(pk))
    assert r.status == 200 and r.json["text"] == "Mine.", r.body
