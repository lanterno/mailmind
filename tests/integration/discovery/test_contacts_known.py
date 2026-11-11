"""The composer's "new to you" lookup (discovery.known and POST /api/contacts/known): who counts as
known, exact address matching, drafts never vouching, the thin-history flag, input limits, the CSRF
guard, and that it never waits on the IMAP lock. All addresses are fictional."""
import email
import itertools


from mailmind.bootstrap.mailbox import store
from mailmind.content import mime

ACCT = "ada@example.test"
_uid = itertools.count(1000)     # past the UIDs the world holds


def _msg(from_addr, recipients="", folder="INBOX", is_junk=0, draft=0, account=ACCT):
    uid = next(_uid)
    c = store.conn()
    cur = c.execute("INSERT INTO messages (account, kind, folder, uid, message_id, thread_id, ts, from_addr, "
                    "subject, recipients, is_junk, draft) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (account, "imap", folder, uid, f"<m{uid}@example.test>", f"t{uid}",
                     1_800_000_000 + uid, from_addr, "Saturday market", recipients, is_junk, draft))
    store.set_recipients(c, cur.lastrowid, mime.recipient_addrs(email.message_from_string(f"To: {recipients}\n\n")))
    c.commit()
    c.close()


def test_route_answers_near(app):
    _msg(ACCT, "john@example.test", folder="Sent")
    r = app.post("/api/contacts/known", {"addrs": ["jon@example.test"]})
    assert r.status == 200 and r.json["items"]["jon@example.test"]["near"] == "john@example.test", r.body


def test_route_requires_csrf(app):
    _msg("lio@example.test")
    body = {"addrs": ["lio@example.test"]}
    assert app.post("/api/contacts/known", body, headers={"X-CSRF-Token": None}).status == 403
    r = app.post("/api/contacts/known", body)
    assert r.status == 200 and r.json["items"]["lio@example.test"]["known"] is True, r.body
