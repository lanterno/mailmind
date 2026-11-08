"""Reopening a saved draft (compose's resume helpers): a reply or forward draft already holds its
quote or forwarded section, so it must come back split into your text and that tail, and go out
again with each part once. Every IMAP or SMTP call a save or a send makes is replaced with a
recorder (`calls`), so nothing can leave. All addresses are fictional."""
import base64
import hashlib
import imaplib
import itertools
import json
import os
from email import message_from_bytes, policy
from email.message import EmailMessage

import pytest

import app_client
import problems
from answered import answered
from fixtures import world as w
from mailmind.bootstrap import accounts, mailbox
from mailmind.bootstrap.compose import book, drafts, outbox, sending
from mailmind.bootstrap.mailbox import store
from mailmind.compose import api as compose
from mailmind.compose.domain import limits
from mailmind.content import mime
from mailmind.content.headers import addr_chip
from mailmind.content.quote import own_html, quote_tail
from mailmind.kernel import addresses
from mailmind.platform import settings
from mailmind.platform.db import migrate, migrations
from mailmind.platform.workers import gates

ACCT = "ada@example.test"
CONFIG = {"accounts": [{"email": ACCT, "type": "imap", "host": "imap.example.test"},
                       {"email": "zed@example.test", "type": "imap", "host": "imap.example.test"}]}
ORIG = {"from_name": "Bo Park", "from_addr": "bo@example.test", "date": "Mon, 2 Feb 2026 09:00:00 +0000",
        "subject": "Plans", "to": ACCT, "cc": "cy@example.test",
        "text": "Shall we meet Saturday?\nThe map is attached.", "html": "<p>Shall we meet <b>Saturday</b>?</p>"}
OWN = "Saturday works.\n\n-- \nAda"


class Calls:
    """What the recorders heard, and how the server they stand in for answers: `uidv` is the UIDVALIDITY it names
    (None: it names none), and with `appenduid` False it names no UID for what was appended."""

    def __init__(self):
        self.appended, self.deleted, self.sent = [], [], []
        self.uidv, self.appenduid = 777, True
        self.uids = itertools.count(101)


@pytest.fixture
def home(data_dir):
    """A data directory holding the schema and the config, with nothing kept in memory from another test."""
    with open(settings.current().config, "w") as f:
        json.dump(CONFIG, f)
    app_client.reset_state()
    yield data_dir
    app_client.reset_state()


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
    monkeypatch.setattr(outbox, "enqueue", enqueue)
    monkeypatch.setattr(drafts, "_live", lambda acct, folder, uid: True)     # the server still holds the copy
    return calls


@pytest.fixture
def calls(home, recorders):
    return recorders


def _save(account=ACCT, draft_id=None, **kw):
    body = {"mode": "new", "account": account, "to": ["bo@example.test"], "subject": "Plans",
            "text": "Saturday works.", "draft_id": draft_id, **kw}
    res, code = answered(drafts.save, body)
    assert code == 200 and res["ok"], (code, res)
    return res


def _original(uid=7):
    """An INBOX message from Bo, its body (text, HTML and a PDF) stored as if already opened, so
    replying and forwarding never reach for IMAP. Returns its pk."""
    m = EmailMessage()
    m["From"], m["To"], m["Cc"] = "Bo Park <bo@example.test>", ACCT, "cy@example.test"
    m["Subject"], m["Date"] = "Plans", "Mon, 2 Feb 2026 09:00:00 +0000"
    m["Message-ID"], m["References"] = "<orig1@example.test>", "<root@example.test>"
    m.set_content("Shall we meet Saturday?\nThe map is attached.\n")
    m.add_alternative("<p>Shall we meet <b>Saturday</b>?</p>", subtype="html")
    m.add_attachment(b"%PDF-1.4 map", maintype="application", subtype="pdf", filename="map.pdf")
    c = store.conn()
    key = store.mint_key(c, ACCT, "<orig1@example.test>", "bo@example.test", None, "Plans")
    c.execute("INSERT INTO messages (account, kind, folder, uid, message_id, thread_id, ts, from_addr, from_name, "
              "subject, refs, recipients, message_key) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
              (ACCT, "imap", "INBOX", uid, "<orig1@example.test>", "t-orig", 1_800_000_000, "bo@example.test",
               "Bo Park", "Plans", "<root@example.test>", "ada@example.test, cy@example.test", key))
    pk = c.execute("SELECT pk FROM messages WHERE uid=? AND folder='INBOX'", (uid,)).fetchone()["pk"]
    store.store_body(c, pk, m.as_bytes(), "2026-10-31T09:00:00")
    c.commit()
    c.close()
    return pk


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


def _resume(pk):
    return answered(drafts.resume, {"pk": pk})


def _key(pk):
    """The key of the message row `pk` holds: what a reopened draft names its original by."""
    key = w.copy_of(pk)["key"]
    assert key is not None
    return key


def _foreign(parts, headers=()):
    """A draft another app made: a multipart message from the given parts."""
    m = EmailMessage()
    m["From"], m["To"], m["Subject"] = f"Ada <{ACCT}>", "Bo Park <bo@example.test>", "Re: Plans"
    m["Message-ID"] = "<phone1@example.test>"
    for k, v in headers:
        m[k] = v
    for p in parts:
        p(m)
    return m.as_bytes()


def _payload(r, **over):
    """What the composer sends for a draft it reopened from r (draft_resume's answer)."""
    return {"mode": r["mode"], "account": r["account"], "to": r["to"], "cc": r["cc"], "bcc": r["bcc"],
            "subject": r["subject"], "text": r["text"], "draft_id": r["draft_id"], "in_reply_to_pk": 99999,
            "attachments": [{"filename": f["filename"], "content_type": f["content_type"], "b64": f["b64"]}
                            for f in r["files"]],
            "resume": {"in_reply_to": r["in_reply_to"], "references": r["references"], "tail_text": r["tail_text"]},
            **over}


def _sent(calls, i=-1):
    """(parsed message, meta, built) of a send the outbox recorder caught."""
    built, meta = calls.sent[i]
    return message_from_bytes(built["raw"], policy=policy.default), meta, built


def _parts(m):
    text = m.get_body(preferencelist=("plain",))
    html = m.get_body(preferencelist=("html",))
    return (compose.lf(text.get_content()) if text else None), (html.get_content() if html else None)


def _reply_resumed(calls, **kw):
    opk = _original()
    pk, _ = _saved_and_synced(calls, mode="reply", in_reply_to_pk=opk, subject="Re: Plans", text=OWN,
                              to=["Bo Park <bo@example.test>"], **kw)
    r, code = _resume(pk)
    assert code == 200, r
    return opk, r


def _row(did):
    c = store.conn()
    r = book.draft_get(c, did)
    c.close()
    return r


def _round_trip(text, html=None, attachments=()):
    """What a draft's text/plain reads as once it has been saved and fetched back."""
    raw = compose.build_message({"account": ACCT, "to": ["bo@example.test"], "subject": "Re: Plans",
                                 "text": text, "html": html, "attachments": list(attachments)}, draft=True)["raw"]
    return mime.parse(raw)


@pytest.mark.parametrize("fn", [compose.reply_bodies, compose.forward_bodies], ids=["reply", "forward"])
def test_own_html_is_where_reply_and_forward_html_start(fn):
    _, html = fn('a & <b> "q"\nline', ORIG)
    assert html.startswith(own_html('a & <b> "q"\nline')), html[:80]


def test_own_html_keeps_line_breaks():
    assert own_html("a\nb") == "<div>a<br>b</div><br>"


def test_quote_tail_of_fresh_reply_and_forward():
    text, html = compose.reply_bodies(OWN, ORIG)
    tail, tail_h = quote_tail(OWN, text, html)
    assert text == OWN + tail and tail.startswith("\n\nOn Mon, 2 Feb 2026"), tail
    assert html == own_html(OWN) + tail_h and "<blockquote" in tail_h, tail_h
    text, html = compose.forward_bodies(OWN, ORIG)
    tail, tail_h = quote_tail(OWN, text, html)
    assert tail.startswith("\n\n---------- Forwarded message ----------"), tail
    assert tail_h.startswith("<div>---------- Forwarded message"), tail_h
    assert quote_tail(OWN, OWN, None) == (None, None)
    assert quote_tail("other", "text", "<p>x</p>") == (None, None)


@pytest.mark.parametrize("fn", [compose.reply_bodies, compose.forward_bodies], ids=["reply", "forward"])
@pytest.mark.parametrize("own", ["", "\n\n\n", OWN, 'a & <b> "q"'], ids=["empty", "blank_lines", "with_a_signature", "markup"])
@pytest.mark.parametrize("o", [
    pytest.param(ORIG, id="text_and_html"),
    pytest.param({**ORIG, "text": "no newline at the end", "html": None}, id="no_final_newline"),
    pytest.param({**ORIG, "text": None, "html": "<p>only <a href='https://x.example.test'>html</a></p>"}, id="html_only"),
    pytest.param({**ORIG, "text": "", "html": None}, id="empty_text"),
])
def test_recorded_tail_survives_a_real_round_trip(o, own, fn):
    text, html = fn(own, o)
    tail, _ = quote_tail(own, text, html)
    p = _round_trip(text, html)
    assert compose.resume_split(p["text"], {"own_text": own, "tail_text": tail}, False) == (own, tail, "tail")


def test_resave_three_times_is_stable():
    text, html = compose.reply_bodies(OWN, ORIG)
    tail, _ = quote_tail(OWN, text, html)
    own = OWN
    for _ in range(3):
        p = _round_trip(own + tail)
        own2, tail2, how = compose.resume_split(p["text"], {"own_text": own, "tail_text": tail}, False)
        assert (own2, tail2, how) == (own, tail, "tail"), (own2, how)
        own = own2
    assert p["text"].count("wrote:") == 1, p["text"]


def test_exact_uses_the_recorded_text_even_without_a_text_part():
    rec = {"own_text": "Mine\r\nstill", "tail_text": "\r\n\r\nOn Mon, Bo wrote:\r\n> hi\r\n"}
    assert compose.resume_split(None, rec, True) == ("Mine\nstill", "\n\nOn Mon, Bo wrote:\n> hi\n", "exact")
    assert compose.resume_split("rewritten by the server", rec, True)[2] == "exact"
    assert compose.resume_split(None, {"own_text": None, "tail_text": None}, True) is None   # nothing recorded


def test_changed_quote_falls_back_to_whole():
    text, html = compose.reply_bodies(OWN, ORIG)
    tail, _ = quote_tail(OWN, text, html)
    edited = text.replace("> Shall we", "> Shall we not")
    own, t, how = compose.resume_split(_round_trip(edited)["text"], {"own_text": OWN, "tail_text": tail}, False)
    assert (t, how) == ("", "whole") and own == edited.rstrip("\n"), (own, how)


def test_pasted_quote_cuts_only_the_last_copy():
    text, html = compose.reply_bodies(OWN, ORIG)
    tail, _ = quote_tail(OWN, text, html)
    mine = "Pasted on purpose:" + tail.rstrip("\n") + "\nand more"
    own, t, how = compose.resume_split(mine + tail, {"own_text": "", "tail_text": tail}, False)
    assert (own, t, how) == (mine, tail, "tail"), (own, how)


@pytest.mark.parametrize("text, record, want", [
    ("Hello\n\n", None, ("Hello\n", "", "whole")),
    ("Hello", None, ("Hello", "", "whole")),
    ("a\r\nb\r\n", {"own_text": None, "tail_text": None}, ("a\nb", "", "whole")),
])
def test_whole_drops_one_trailing_newline(text, record, want):
    assert compose.resume_split(text, record, False) == want


@pytest.mark.parametrize("record", [{"own_text": "Mine", "tail_text": None}, None])
def test_no_text_and_not_exact_is_none(record):
    assert compose.resume_split(None, record, False) is None


@pytest.mark.parametrize("name", ["Bo Park", "Park, Bo", 'Bo "B" Park', "Dr. Bo", "bo@work", "(Bo)", "back\\slash",
                                  "semi;colon", "Zoë Ünal", "x<y>"])
def test_addr_chip_round_trips(name):
    addr = "bo@example.test"
    chip = addr_chip(name, addr)
    assert addresses.parse_addr_list([chip]) == [(name, addr)], chip


@pytest.mark.parametrize("name, chip", [("", "bo@example.test"), ("BO@example.test", "bo@example.test"),
                                        ("Bo Park", "Bo Park <bo@example.test>"),
                                        ("Park, Bo", '"Park, Bo" <bo@example.test>')])
def test_addr_chip_forms(name, chip):
    assert addr_chip(name, "bo@example.test") == chip


def test_thread_ids_validate_dedupe_and_cap():
    irt, refs = compose.thread_ids("junk <bad> <a@example.test> <b@example.test>",
                                   "<r@example.test> <r@example.test> <nope> "
                                   + " ".join(f"<m{i}@example.test>" for i in range(60)))
    assert irt == "<a@example.test>", irt
    assert len(refs) == 50 and refs[0] == "<r@example.test>" and refs[1] == "<m11@example.test>", refs[:2]
    assert refs[-1] == "<m59@example.test>" and len(set(refs)) == 50
    assert compose.thread_ids(None, None) == (None, [])
    assert compose.thread_ids("<x@example.test>\r\nBcc: evil@example.test", "")[0] == "<x@example.test>"


def test_from_change_never_passes_the_old_uid(calls):
    did = _save()["draft_id"]
    old_uid = _row(did)["uid"]
    _save(account="zed@example.test", draft_id=did)
    last = calls.appended[-1]
    assert last["account"] == "zed@example.test" and last["prev_uid"] is None, last
    assert last["prev_uidvalidity"] is None and last["prev_msgid"] is None, last
    assert old_uid != _row(did)["uid"]


def test_from_change_deletes_the_old_copy_in_its_own_account(calls):
    did = _save()["draft_id"]
    old_uid = _row(did)["uid"]
    _save(account="zed@example.test", draft_id=did)
    assert calls.deleted == [(ACCT, "Drafts", old_uid, 777)], calls.deleted


def test_from_change_then_two_saves_keeps_the_row_in_step(calls):
    did = _save()["draft_id"]
    _save(account="zed@example.test", draft_id=did)
    row = _row(did)
    assert row["account"] == "zed@example.test", dict(row)
    _save(account="zed@example.test", draft_id=did, text="Saturday at ten.")
    last = calls.appended[-1]
    assert last["account"] == "zed@example.test" and last["prev_uid"] == row["uid"], last
    assert len(calls.deleted) == 1, calls.deleted          # only the From change removed anything


def test_from_change_removes_the_old_copy_only_after_the_new_one_is_saved(calls, monkeypatch):
    did = _save()["draft_id"]
    seen, record = [], mailbox.copies.delete_draft_uid
    monkeypatch.setattr(mailbox.copies, "delete_draft_uid", lambda *a, **k: (seen.append(len(calls.appended)), record(*a, **k)))
    _save(account="zed@example.test", draft_id=did)
    assert seen == [2], seen                                    # both appends had run


def test_failed_save_under_a_new_from_keeps_the_old_copy(calls, monkeypatch):
    did = _save()["draft_id"]
    old = dict(_row(did))

    def unreachable(acct, raw, **kw):
        raise OSError("connection refused")
    monkeypatch.setattr(mailbox.copies, "append_to_drafts", unreachable)
    res, code = answered(drafts.save, {"mode": "new", "account": "zed@example.test", "to": ["bo@example.test"],
                             "subject": "Plans", "text": "Saturday works.", "draft_id": did})
    assert code == 200 and res["ok"] and res["pushed"] is False, (code, res)   # the journal holds it
    assert calls.deleted == [], calls.deleted
    now = dict(_row(did))
    assert {k: now[k] for k in ("account", "folder", "uid", "message_id", "raw_sha")} == {
        k: old[k] for k in ("account", "folder", "uid", "message_id", "raw_sha")}


def _other_folder_draft(uid=5):
    """A draft another app keeps in a second drafts folder, registered the way the reader does."""
    c = store.conn()
    store.set_uidvalidity(c, ACCT, "Old drafts", 777)              # the same number as Drafts
    c.execute("INSERT INTO messages (account, kind, folder, uid, message_id, thread_id, ts, from_addr, subject, draft) "
              "VALUES (?,?,?,?,?,?,?,?,?,1)", (ACCT, "imap", "Old drafts", uid, "<old@example.test>", "to5",
                                                1_800_000_000, ACCT, "From last year"))
    c.commit()
    row = c.execute("SELECT * FROM messages WHERE folder='Old drafts' AND uid=?", (uid,)).fetchone()
    c.close()
    return drafts.info(row)["draft_id"]


def test_copy_in_another_drafts_folder_is_removed_there(calls):
    did = _other_folder_draft()
    _save(draft_id=did, text="Rewritten.")
    last = calls.appended[-1]
    assert (last["prev_uid"], last["prev_folder"]) == (5, "Old drafts"), last
    assert calls.deleted == [(ACCT, "Old drafts", 5, 777)], calls.deleted
    _save(draft_id=did, text="Rewritten again.")                # now tracked in Drafts: replaced in place
    assert calls.appended[-1]["prev_folder"] == "Drafts" and len(calls.deleted) == 1, calls.deleted


def test_received_mail_a_row_points_at_is_never_removed(calls):
    """Until 2026-11 Edit registered a row for anything in a folder whose name held "draft", so old
    rows may name received mail."""
    c = store.conn()
    store.set_uidvalidity(c, ACCT, "Bank drafts", 912)
    c.execute("INSERT INTO messages (account, kind, folder, uid, message_id, thread_id, ts, from_addr, subject, draft) "
              "VALUES (?,?,?,?,?,?,?,?,?,0)", (ACCT, "imap", "Bank drafts", 3, "<stmt@bank.example.test>", "tb3",
                                                1_800_000_000, "loans@bank.example.test", "Your statement"))
    c.commit()
    row = c.execute("SELECT * FROM messages WHERE folder='Bank drafts'").fetchone()
    c.close()
    assert drafts.info(row) == {}                         # no Edit for it today
    c = store.conn()
    did = book.draft_upsert(c, {"id": None, "account": ACCT, "folder": "Bank drafts", "uid": 3,
                                 "message_id": "<stmt@bank.example.test>", "uidvalidity": 912,
                                 "mode": "new", "subject": "Your statement",
                                 "created_at": "2026-10-01T09:00:00", "updated_at": "2026-10-01T09:00:00"})
    c.commit()
    c.close()
    _save(draft_id=did, text="Forwarding this to Bo.")
    assert calls.deleted == [], calls.deleted                # the statement stays where it is
    saved = _row(did)
    _save(account="zed@example.test", draft_id=did, text="From the other account.")
    assert calls.deleted == [(ACCT, "Drafts", saved["uid"], 777)], calls.deleted   # only MailMind's own copy


def test_a_copy_mailmind_saved_is_removed_even_unflagged(calls):
    did = _save()["draft_id"]                                   # no messages row: never synced
    _save(account="zed@example.test", draft_id=did)
    assert len(calls.deleted) == 1 and calls.deleted[0][0] == ACCT, calls.deleted


class _FakeDraftsIMAP:
    def __init__(self):
        self.untagged_responses, self.calls = {"UIDVALIDITY": [b"777"]}, []

    def list(self):
        return "OK", [b'(\\HasNoChildren \\Drafts) "/" "Drafts"']

    def select(self, folder, readonly=False):
        self.calls.append(("select", folder))
        return "OK", [b"1"]

    def response(self, code):
        return code, self.untagged_responses.get(code, [None])

    def uid(self, *args):
        self.calls.append(("uid",) + args)
        return "OK", [b"5"]

    def expunge(self):
        self.calls.append(("expunge",))

    def append(self, folder, flags, when, raw):
        self.calls.append(("append", folder))
        return "OK", [b"[APPENDUID 777 9] done"]

    def logout(self):
        pass


@pytest.mark.parametrize("prev_folder, touched", [("Old drafts", False), ("Drafts", True), (None, True)])
def test_append_to_drafts_leaves_drafts_alone_for_a_copy_elsewhere(prev_folder, touched, home, monkeypatch):
    fake = _FakeDraftsIMAP()
    monkeypatch.setattr(accounts, "connect", lambda acct, **kw: fake)
    got = mailbox.copies.append_to_drafts({"email": ACCT}, b"Subject: x\r\n\r\nhi\r\n", prev_uid=5, prev_uidvalidity=777,
                                          prev_msgid="<old@example.test>", prev_folder=prev_folder)
    assert got[:3] == ("Drafts", 9, 777), got
    assert bool([c for c in fake.calls if c[0] == "uid"]) is touched, fake.calls
    if prev_folder == "Old drafts":                     # not even searched for by Message-ID
        assert fake.calls == [("select", '"Drafts"'), ("append", '"Drafts"')], fake.calls


def test_unvouched_old_copy_is_left_alone(calls):
    calls.uidv = None                                           # the server named no UIDVALIDITY
    did = _save()["draft_id"]
    _save(account="zed@example.test", draft_id=did)
    assert calls.deleted == [], calls.deleted
    assert calls.appended[-1]["prev_uid"] is None


def test_other_apps_draft_registers_with_folder_uidvalidity(calls):
    c = store.conn()
    store.set_uidvalidity(c, ACCT, "Drafts", 4242)
    c.execute("INSERT INTO messages (account, kind, folder, uid, message_id, thread_id, ts, from_addr, subject) "
              "VALUES (?,?,?,?,?,?,?,?,?)", (ACCT, "imap", "Drafts", 9, "<other@example.test>", "t9",
                                              1_800_000_000, ACCT, "From my phone"))
    c.commit()
    row = c.execute("SELECT * FROM messages WHERE uid=9").fetchone()
    c.close()
    info = drafts.info(row)
    d = _row(info["draft_id"])
    assert (d["uid"], d["uidvalidity"], d["mode"]) == (9, 4242, "new"), dict(d)


def test_vanished_draft_row_is_tracked_again(calls):
    did = _save()["draft_id"]
    c = store.conn()
    book.draft_delete(c, did)                                  # sent or discarded in another tab
    c.commit()
    c.close()
    res = _save(draft_id=did, text="Saturday at ten.")
    assert res["draft_id"] != did and _row(res["draft_id"]) is not None, res
    assert calls.appended[-1]["prev_uid"] is None
    again = _save(draft_id=res["draft_id"], text="Saturday at eleven.")
    assert again["draft_id"] == res["draft_id"] and calls.appended[-1]["prev_uid"] == res["uid"], again


def _cols():
    c = store.conn()
    cols = {r[1] for r in c.execute("PRAGMA table_info(drafts)")}
    v = c.execute("PRAGMA user_version").fetchone()[0]
    c.close()
    return cols, v


SIDECAR = {"own_text", "tail_text", "tail_html", "bcc", "raw_sha"}


def test_migration_20_is_idempotent(home):
    store.init()
    cols, v = _cols()
    assert v >= 20 and SIDECAR <= cols, (v, cols)


def test_v19_database_gains_the_columns(home):
    db = settings.current().db
    os.remove(db)
    migrate.init(db, migrations.MIGRATIONS[:19])
    c = store.conn()
    assert not SIDECAR & {r[1] for r in c.execute("PRAGMA table_info(drafts)")}
    c.execute("INSERT INTO drafts (account, folder, uid, mode, subject) VALUES (?,?,?,?,?)",
              (ACCT, "Drafts", 3, "reply", "Re: Plans"))
    c.commit()
    c.close()
    store.init()
    cols, v = _cols()
    assert v == migrations.MIGRATIONS[-1][0] and SIDECAR <= cols, (v, cols)   # every later one ran too
    row = _row(1)
    assert (row["uid"], row["mode"], row["own_text"], row["bcc"]) == (3, "reply", None, None), dict(row)


def test_new_draft_records_its_text(calls):
    res = _save(text="Saturday works.\r\nSee you.")
    row = _row(res["draft_id"])
    assert row["own_text"] == "Saturday works.\nSee you." and row["tail_text"] is None, dict(row)
    assert row["tail_html"] is None and row["bcc"] is None, dict(row)
    assert row["raw_sha"] == hashlib.sha256(calls.appended[-1]["raw"]).hexdigest()


def test_reply_draft_records_text_quote_and_html(calls):
    opk = _original()
    res = _save(mode="reply", in_reply_to_pk=opk, subject="Re: Plans", text=OWN)
    row = _row(res["draft_id"])
    raw = calls.appended[-1]["raw"]
    assert row["own_text"] == OWN and row["tail_text"].startswith("\n\nOn Mon, 02 Feb 2026"), dict(row)
    assert row["tail_text"].count("wrote:") == 1 and "> Shall we meet Saturday?" in row["tail_text"]
    assert row["tail_html"].startswith("<div>On Mon") and "<blockquote" in row["tail_html"], row["tail_html"]
    assert compose.lf(mime.parse(raw)["text"]) == OWN + row["tail_text"]


def test_forward_draft_records_the_banner(calls):
    opk = _original()
    res = _save(mode="forward", in_reply_to_pk=opk, subject="Fwd: Plans", text="FYI")
    row = _row(res["draft_id"])
    assert row["own_text"] == "FYI" and row["tail_text"].startswith("\n\n---------- Forwarded message"), dict(row)
    assert "From: Bo Park <bo@example.test>" in row["tail_text"], row["tail_text"]
    files = [a["filename"] for a in mime.parse(calls.appended[-1]["raw"])["attachments"]]
    assert files == ["map.pdf"], files


def test_bcc_is_kept_beside_the_draft_not_in_it(calls):
    res = _save(bcc=['"Park, Cy" <cy@example.test>', "dee@example.test"])
    raw = calls.appended[-1]["raw"]
    head = raw.split(b"\r\n\r\n", 1)[0]
    assert b"\nBcc:" not in b"\n" + head and b"cy@example.test" not in raw, head
    row = _row(res["draft_id"])
    assert json.loads(row["bcc"]) == ['"Park, Cy" <cy@example.test>', "dee@example.test"], row["bcc"]
    assert row["raw_sha"] == hashlib.sha256(raw).hexdigest()


def test_reply_draft_reopens_with_quote_files_bcc_and_thread(calls):
    opk = _original()
    pk, res = _saved_and_synced(calls, mode="reply", in_reply_to_pk=opk, subject="Re: Plans", text=OWN,
                                to=["Bo Park <bo@example.test>"], cc=["cy@example.test"], bcc=["Dee <dee@example.test>"],
                                attachments=[{"filename": "notes.txt", "content_type": "text/plain",
                                              "b64": base64.b64encode(b"bring water").decode()}])
    r, code = _resume(pk)
    assert code == 200 and r["split"] == "exact", (code, r)
    assert (r["mode"], r["draft_id"], r["text"]) == ("reply", res["draft_id"], OWN), r
    assert r["tail_text"].startswith("\n\nOn Mon, 02 Feb 2026") and r["tail_text"].count("wrote:") == 1, r["tail_text"]
    assert (r["to"], r["cc"], r["bcc"]) == (["Bo Park <bo@example.test>"], ["cy@example.test"],
                                            ["Dee <dee@example.test>"]), r
    assert (r["in_reply_to"], r["references"]) == ("<orig1@example.test>", "<root@example.test> <orig1@example.test>"), r
    assert r["original_key"] == _key(opk) and r["participants"] == ["bo@example.test", ACCT, "cy@example.test"], r
    assert [(f["filename"], base64.b64decode(f["b64"])) for f in r["files"]] == [("notes.txt", b"bring water")], r["files"]
    assert r["notes"] == {"inline": 0, "bcc_dropped": 0, "plain": False}, r["notes"]


def test_forward_draft_reopens_with_its_files_as_chips(calls):
    opk = _original()
    pk, _ = _saved_and_synced(calls, mode="forward", in_reply_to_pk=opk, subject="Fwd: Plans", text="FYI")
    r, code = _resume(pk)
    assert code == 200 and (r["mode"], r["text"], r["split"]) == ("forward", "FYI", "exact"), r
    assert r["tail_text"].startswith("\n\n---------- Forwarded message ----------"), r["tail_text"]
    assert [(f["filename"], f["content_type"], f["size"]) for f in r["files"]] == [("map.pdf", "application/pdf", 12)], r
    assert (r["original_key"], r["in_reply_to"]) == (None, ""), r


def test_original_deleted_still_reopens(calls):
    opk = _original()
    pk, _ = _saved_and_synced(calls, mode="reply", in_reply_to_pk=opk, subject="Re: Plans", text=OWN)
    c = store.conn()
    store.delete_message(c, opk)                                # archived: the local row is gone
    c.commit()
    c.close()
    r, code = _resume(pk)
    assert code == 200 and (r["mode"], r["original_key"], r["participants"]) == ("reply", None, []), r
    assert r["in_reply_to"] == "<orig1@example.test>" and r["text"] == OWN, r


def test_original_renumbered_found_by_message_id(calls):
    opk = _original(uid=7)
    pk, _ = _saved_and_synced(calls, mode="reply", in_reply_to_pk=opk, subject="Re: Plans", text=OWN)
    c = store.conn()
    store.delete_message(c, opk)
    c.commit()
    c.close()
    npk = _original(uid=8)                                      # reconcile gave it a new row
    assert npk != opk
    assert _resume(pk)[0]["original_key"] == _key(npk)


def test_raw_sha_proves_the_version_without_a_uid(calls):
    calls.appenduid = False
    pk, res = _saved_and_synced(calls, text="Mine.", bcc=["dee@example.test"])
    assert res["uid"] is None
    r, _ = _resume(pk)
    assert (r["split"], r["text"], r["bcc"]) == ("exact", "Mine.", ["dee@example.test"]), r


def test_changed_copy_uses_the_recorded_tail_and_drops_bcc(calls):
    calls.appenduid = False
    opk = _original()
    res = _save(mode="reply", in_reply_to_pk=opk, subject="Re: Plans", text=OWN, bcc=["dee@example.test"])
    raw = b"X-Rewritten-By: bridge\r\n" + calls.appended[-1]["raw"]   # the server's copy isn't byte-for-byte ours
    r, code = _resume(_synced(raw, 601))
    assert code == 200 and (r["split"], r["text"], r["draft_id"]) == ("tail", OWN, res["draft_id"]), r
    assert r["tail_text"].count("wrote:") == 1, r["tail_text"]
    assert r["bcc"] == [] and r["notes"]["bcc_dropped"] == 1, r


def _resaved_elsewhere(calls, old_text, new_text, uid=555):
    """Another app re-saved MailMind's last copy under the same Message-ID, at a new UID."""
    raw = calls.appended[-1]["raw"].replace(old_text.encode(), new_text.encode())
    return _synced(raw, uid)


def test_copy_resaved_elsewhere_is_the_one_the_next_save_replaces(calls):
    _, res = _saved_and_synced(calls, text="Mine.", bcc=["dee@example.test"])
    pk = _resaved_elsewhere(calls, "Mine.", "Mine, from my phone.")
    r, code = _resume(pk)
    assert code == 200 and (r["draft_id"], r["split"], r["text"]) == (res["draft_id"], "whole", "Mine, from my phone."), r
    assert r["notes"]["bcc_dropped"] == 1, r
    row = _row(res["draft_id"])
    assert (row["folder"], row["uid"], row["uidvalidity"]) == ("Drafts", 555, 777), dict(row)   # what a send deletes
    _save(draft_id=res["draft_id"], text="Mine, from my phone. Edited.")
    assert calls.appended[-1]["prev_uid"] == 555, calls.appended[-1]


def test_record_of_a_replaced_copy_is_never_taken_for_the_new_one(calls):
    _, res = _saved_and_synced(calls, text="Mine.", bcc=["dee@example.test"])
    pk = _resaved_elsewhere(calls, "Mine.", "Mine, from my phone.")
    _resume(pk)
    row = _row(res["draft_id"])
    assert (row["own_text"], row["bcc"], row["raw_sha"]) == (None, None, None), dict(row)
    r, _ = _resume(pk)                                          # same UID now, but not MailMind's bytes
    assert (r["split"], r["text"], r["bcc"]) == ("whole", "Mine, from my phone.", []), r


def test_reply_resaved_elsewhere_keeps_its_recorded_quote(calls):
    opk = _original()
    res = _save(mode="reply", in_reply_to_pk=opk, subject="Re: Plans", text=OWN, to=["Bo Park <bo@example.test>"])
    pk = _resaved_elsewhere(calls, "Saturday works.", "Sunday works.")
    r, _ = _resume(pk)
    assert (r["split"], r["text"]) == ("tail", OWN.replace("Saturday", "Sunday")), r
    assert _row(res["draft_id"])["tail_html"], "the quote's HTML is still vouched for"
    r2, _ = _resume(pk)
    assert (r2["split"], r2["text"], r2["tail_text"]) == ("tail", r["text"], r["tail_text"]), r2


def test_a_save_that_moved_the_row_meanwhile_is_not_re_pointed_over(calls):
    _, res = _saved_and_synced(calls, text="Mine.", bcc=["dee@example.test"])
    did = res["draft_id"]
    seen = _row(did)
    _save(draft_id=did, text="Newer, from another tab.", bcc=["dee@example.test"])   # lands after the read
    newest = dict(_row(did))
    c = store.conn()
    moved = book.draft_repoint(c, seen, "Drafts", 555, 777, forget=True)
    c.commit()
    c.close()
    assert moved is False and dict(_row(did)) == newest, dict(_row(did))
    c = store.conn()
    assert book.draft_repoint(c, _row(did), "Drafts", 556, 777, forget=True) is True
    c.commit()
    c.close()
    assert (_row(did)["uid"], _row(did)["own_text"]) == (556, None)


def test_same_bytes_without_a_uid_are_tracked_at_the_uid_they_got(calls):
    calls.appenduid = False
    pk, res = _saved_and_synced(calls, text="Mine.", bcc=["dee@example.test"])
    r, _ = _resume(pk)
    assert (r["split"], r["bcc"]) == ("exact", ["dee@example.test"]), r
    row = _row(res["draft_id"])
    assert (row["uid"], row["own_text"], row["bcc"]) == (555, "Mine.", '["dee@example.test"]'), dict(row)
    _save(draft_id=res["draft_id"], text="Mine, edited.")
    assert calls.appended[-1]["prev_uid"] == 555, calls.appended[-1]


def test_other_apps_draft_opens_whole_as_plain_text(calls):
    opk = _original()

    def body(m):
        m.set_content("On my way.\n\nOn Mon, Bo wrote:\n> Shall we meet Saturday?\n")
        m.add_alternative("<p>On my way. <img src='cid:logo1@example.test'></p>", subtype="html")
        m.get_payload()[1].add_related(b"\x89PNG fake", maintype="image", subtype="png", cid="<logo1@example.test>")
        m.add_attachment(b"%PDF-1.4 plan", maintype="application", subtype="pdf", filename="plan.pdf")
    raw = _foreign([body], [("Bcc", "Cy Lee <cy@example.test>"), ("In-Reply-To", "<orig1@example.test>")])
    r, code = _resume(_synced(raw, 9))
    assert code == 200 and (r["mode"], r["split"]) == ("reply", "whole"), r
    assert r["text"] == "On my way.\n\nOn Mon, Bo wrote:\n> Shall we meet Saturday?" and r["tail_text"] == "", r
    assert r["bcc"] == ["Cy Lee <cy@example.test>"] and r["original_key"] == _key(opk), r
    names = sorted(f["filename"] for f in r["files"])
    assert len(names) == 2 and "plan.pdf" in names and any(n.startswith("inline-") and n.endswith(".png")
                                                            for n in names), names
    assert r["notes"] == {"inline": 1, "bcc_dropped": 0, "plain": True}, r["notes"]


def test_many_files_come_back_in_order_from_one_parse(calls, monkeypatch):
    files = [{"filename": f"f{i}.bin", "content_type": "application/octet-stream",
              "b64": base64.b64encode(bytes([i]) * 3000).decode()} for i in range(40)]
    pk, _ = _saved_and_synced(calls, text="Many.", attachments=files)

    def per_part(raw, part_id):
        raise AssertionError("re-parsed the whole draft for one file")
    monkeypatch.setattr(mime, "extract_part", per_part)
    r, code = _resume(pk)
    assert code == 200 and [f["filename"] for f in r["files"]] == [f"f{i}.bin" for i in range(40)], r
    assert all(base64.b64decode(f["b64"]) == bytes([i]) * 3000 for i, f in enumerate(r["files"]))


def test_html_only_draft_is_refused(calls):
    raw = _foreign([lambda m: m.set_content("<p>Only <a href='https://x.example.test'>formatted</a></p>",
                                            subtype="html")])
    r, code = _resume(_synced(raw, 10))
    assert code == 422 and "only has formatted text" in r["detail"], (code, r)


def _old_thread():
    """An email of its own, with a file of its own, to attach to a draft."""
    inner = EmailMessage()
    inner["From"], inner["Subject"] = "lin@example.test", "Old thread"
    inner.set_content("the inner note")
    inner.add_attachment(b"%PDF-1.4 swatch", maintype="application", subtype="pdf", filename="swatch.pdf")
    return inner


def test_an_attached_email_comes_back_as_one_file(calls):
    def body(m):
        m.set_content("See the thread below.")
        m.add_attachment(b"%PDF-1.4 map", maintype="application", subtype="pdf", filename="map.pdf")
        m.add_attachment(_old_thread(), filename="Old thread.eml")
    r, code = _resume(_synced(_foreign([body]), 11))
    assert code == 200 and r["text"] == "See the thread below.", (code, r)
    assert [(f["filename"], f["content_type"]) for f in r["files"]] == [
        ("map.pdf", "application/pdf"), ("Old thread.eml", "message/rfc822")], r["files"]
    assert base64.b64decode(r["files"][0]["b64"]) == b"%PDF-1.4 map"
    data = base64.b64decode(r["files"][1]["b64"])
    assert b"Subject: Old thread" in data and r["files"][1]["size"] == len(data), r["files"]


def test_an_attached_email_with_no_file_name_still_comes_back(calls):
    def body(m):
        m.set_content("See below.")
        m.add_attachment(_old_thread())
    r, code = _resume(_synced(_foreign([body]), 12))
    assert code == 200 and [f["filename"] for f in r["files"]] == ["message.eml"], (code, r)


def test_an_attached_email_goes_out_whole_when_the_draft_is_sent(calls):
    def body(m):
        m.set_content("See the thread below.")
        m.add_attachment(_old_thread(), filename="Old thread.eml")
    r, code = _resume(_synced(_foreign([body]), 13))
    assert code == 200, (code, r)
    answered(sending.send, _payload(r))
    m, _, _ = _sent(calls)
    [att] = list(m.iter_attachments())
    assert att.get_content_type() == "message/rfc822" and att["Content-Transfer-Encoding"] != "base64", att.as_string()
    inner = att.get_content()
    assert inner["Subject"] == "Old thread"
    assert [a.get_filename() for a in inner.iter_attachments()] == ["swatch.pdf"]


def test_a_draft_saved_with_an_attached_email_reopens_and_saves_again(calls):
    r1, code = _resume(_synced(_foreign([lambda m: (m.set_content("See below."), m.add_attachment(_old_thread()))]), 14))
    assert code == 200, (code, r1)
    res = _save(account=r1["account"], draft_id=r1["draft_id"], text=r1["text"], attachments=[
        {k: f[k] for k in ("filename", "content_type", "b64")} for f in r1["files"]])
    assert res["ok"]
    raw = calls.appended[-1]["raw"]
    assert [a["content_type"] for a in mime.parse(raw)["attachments"]] == ["message/rfc822"]
    r2, code = _resume(_synced(raw, 15))
    assert code == 200 and [f["content_type"] for f in r2["files"]] == ["message/rfc822"], (code, r2)
    assert b"Subject: Old thread" in base64.b64decode(r2["files"][0]["b64"])


def test_a_part_inside_an_attached_email_is_not_the_drafts_own(calls):
    """Its text is the attached email's, whatever it says, so it is neither the draft's text nor a part left unread."""
    inner = _old_thread()

    def body(m):
        m.add_attachment(inner, filename="Old thread.eml")
        m.attach(_part("See below."))
    r, code = _resume(_synced(_foreign([body]), 16))
    assert code == 200 and r["text"] == "See below." and len(r["files"]) == 1, (code, r)


def test_a_delivery_report_is_not_one_file(calls):
    report = EmailMessage()
    report.add_header("Reporting-MTA", "dns; mail.example.test")

    def body(m):
        m.set_content("Bounced.")
        m.add_attachment(report)
        m.get_payload()[-1].set_type("message/delivery-status")
    r, code = _resume(_synced(_foreign([body]), 17))
    assert code == 422 and "can’t reopen without losing it" in r["detail"], (code, r)


def _part(content, **kw):
    p = EmailMessage()
    p.set_content(content, **kw)
    return p


def _mixed(*parts):
    """Another app's draft: text, then the given parts in order."""
    def body(m):
        m.set_content("Here is the first photo:")
        m.make_mixed()
        for p in parts:
            m.attach(p)
    return _foreign([body])


def test_text_after_a_file_is_refused_not_dropped(calls):
    raw = _mixed(_part(b"\xff\xd8jpeg", maintype="image", subtype="jpeg", filename="a.jpg", disposition="inline"),
                 _part("And the important part: meet at 5pm at the north gate."))
    r, code = _resume(_synced(raw, 12))
    assert code == 422 and "can’t reopen without losing it" in r["detail"], (code, r)


def test_nameless_file_is_refused_unless_marked_an_attachment(calls):
    r, code = _resume(_synced(_mixed(_part(b"%PDF-1.4 scan", maintype="application", subtype="pdf")), 13))
    assert code == 422 and "can’t reopen without losing it" in r["detail"], (code, r)
    raw = _mixed(_part(b"%PDF-1.4 scan", maintype="application", subtype="pdf", disposition="attachment"))
    r, code = _resume(_synced(raw, 14))
    assert code == 200 and [f["filename"] for f in r["files"]] == ["part-2"], (code, r)


def test_empty_extra_text_is_no_loss(calls):
    r, code = _resume(_synced(_mixed(_part(b"%PDF-1.4", maintype="application", subtype="pdf", filename="a.pdf"),
                                     _part("\n")), 15))
    assert code == 200 and r["text"] == "Here is the first photo:", (code, r)


def test_empty_first_text_cannot_hide_a_second(calls):
    def body(m):
        m.set_content("")
        m.make_mixed()
        m.attach(_part("The words are here."))
    r, code = _resume(_synced(_foreign([body]), 16))
    assert code == 422, (code, r)


def _apple_rich(tail_html):
    """Apple Mail's Rich Text draft with a file: the plain part is the alternative to all of it."""
    return (b"From: Ada <ada@example.test>\r\nTo: Bo Park <bo@example.test>\r\nSubject: Plan\r\n"
            b"Message-ID: <apple1@example.test>\r\nMIME-Version: 1.0\r\n"
            b"Content-Type: multipart/alternative; boundary=\"A\"\r\n\r\n"
            b"--A\r\nContent-Type: text/plain; charset=utf-8\r\n\r\nHere is the plan:\r\n\r\nSee you at 5pm\r\n"
            b"--A\r\nContent-Type: multipart/mixed; boundary=\"B\"\r\n\r\n"
            b"--B\r\nContent-Type: text/html; charset=utf-8\r\n\r\n<div>Here is the plan:</div>\r\n"
            b"--B\r\nContent-Type: application/pdf; name=plan.pdf\r\nContent-Disposition: inline; filename=plan.pdf\r\n"
            b"Content-Transfer-Encoding: base64\r\n\r\nJVBERi0xLjQ=\r\n"
            b"--B\r\nContent-Type: text/html; charset=utf-8\r\n\r\n" + tail_html + b"\r\n--B--\r\n--A--\r\n")


@pytest.mark.parametrize("uid, tail", [(30, b'<div class="">See you at 5pm</div>'), (31, b"<html><body></body></html>")],
                         ids=["words_in_the_last_part", "nothing_in_the_last_part"])
def test_apple_rich_text_draft_with_a_file_reopens_as_plain_text(uid, tail, calls):
    r, code = _resume(_synced(_apple_rich(tail), uid))
    assert code == 200, (code, r)
    assert (r["text"], [f["filename"] for f in r["files"]]) == ("Here is the plan:\n\nSee you at 5pm", ["plan.pdf"]), r
    assert r["notes"]["plain"] is True, r["notes"]


def test_html_beside_the_text_with_words_of_its_own_is_refused(calls):
    def body(m):
        m.set_content("Here is the plan:")
        m.add_attachment(b"%PDF-1.4", maintype="application", subtype="pdf", filename="plan.pdf")
        m.attach(_part("<div>And bring the badge.</div>", subtype="html"))
    r, code = _resume(_synced(_foreign([body]), 32))
    assert code == 422 and "can’t reopen without losing it" in r["detail"], (code, r)


def test_outlook_style_invite_is_refused_a_named_one_is_a_file(calls):
    ics = "BEGIN:VCALENDAR\r\nMETHOD:REQUEST\r\nEND:VCALENDAR\r\n"

    def inline(m):
        m.set_content("Let’s sync.")
        m.add_alternative(ics, subtype="calendar", params={"method": "REQUEST"})
    r, code = _resume(_synced(_foreign([inline]), 17))
    assert code == 422 and "calendar invite" in r["detail"], (code, r)

    def named(m):
        m.set_content("Let’s sync.")
        m.add_attachment(ics, subtype="calendar", filename="invite.ics")
    r, code = _resume(_synced(_foreign([named]), 18))
    assert code == 200 and [f["filename"] for f in r["files"]] == ["invite.ics"], (code, r)


def test_a_file_typed_multipart_is_saved_and_reopened_as_a_file(calls):
    page = {"filename": "page.mhtml", "content_type": "multipart/related", "b64": base64.b64encode(b"MIME page").decode()}
    pk, _ = _saved_and_synced(calls, text="The page.", attachments=[page])
    r, code = _resume(pk)
    assert code == 200 and [(f["filename"], f["content_type"]) for f in r["files"]] == [
        ("page.mhtml", "application/octet-stream")], (code, r)
    assert base64.b64decode(r["files"][0]["b64"]) == b"MIME page"


def test_data_in_a_boundaryless_multipart_part_is_refused_not_dropped(calls):
    raw = (b"From: Ada <ada@example.test>\r\nTo: bo@example.test\r\nSubject: Page\r\nMessage-ID: <mh1@example.test>\r\n"
           b"MIME-Version: 1.0\r\nContent-Type: multipart/mixed; boundary=\"M\"\r\n\r\n"
           b"--M\r\nContent-Type: text/plain\r\n\r\nThe page.\r\n"
           b"--M\r\nContent-Type: multipart/related\r\nContent-Disposition: attachment; filename=\"page.mhtml\"\r\n"
           b"Content-Transfer-Encoding: base64\r\n\r\nTUlNRSBwYWdl\r\n--M--\r\n")
    r, code = _resume(_synced(raw, 33))
    assert code == 422 and "can’t reopen without losing it" in r["detail"], (code, r)


def test_files_over_the_cap_are_refused(calls, monkeypatch):
    pk, _ = _saved_and_synced(calls, text="Big.", attachments=[{"filename": "a.bin", "content_type": "application/octet-stream",
                                                                "b64": base64.b64encode(b"x" * 64).decode()}])
    monkeypatch.setattr(limits, "MAX_ATTACH_BYTES", 32)
    r, code = _resume(pk)
    assert code == 422 and "more than 25 MB" in r["detail"], (code, r)


def test_draft_whose_saves_would_be_too_large_is_refused(calls, monkeypatch):
    opk = _original()
    pk, _ = _saved_and_synced(calls, mode="forward", in_reply_to_pk=opk, subject="Fwd: Plans", text="FYI")
    r, code = _resume(pk)
    assert code == 200, r
    need = (sum(len(f["b64"]) for f in r["files"]) + len(json.dumps(r["text"])) + len(json.dumps(r["tail_text"]))
            + limits.RESUME_HEADROOM)
    monkeypatch.setattr(limits, "MAX_SEND_POST", need)
    assert _resume(pk)[1] == 200
    monkeypatch.setattr(limits, "MAX_SEND_POST", need - len(r["tail_text"]) // 2)      # the files alone would still fit
    r, code = _resume(pk)
    assert code == 422 and "too large for MailMind to save or send" in r["detail"], (code, r)


def test_bad_pk_unknown_and_not_a_draft(calls):
    opk = _original()
    assert answered(drafts.resume, {"pk": "x"}) == (problems.refused("Missing draft."), 400)
    assert answered(drafts.resume, {})[1] == 400
    assert answered(drafts.resume, {"pk": 99999})[1] == 404
    r, code = answered(drafts.resume, {"pk": opk})
    assert code == 400 and r["detail"] == "That message isn’t a draft.", r


def test_resume_route_requires_csrf(app, recorders):
    pk, _ = _saved_and_synced(recorders, text="Mine.")
    assert app.post("/api/draft/resume", w.copy_of(pk), headers={"X-CSRF-Token": None}).status == 403
    r = app.post("/api/draft/resume", w.copy_of(pk))
    assert r.status == 200 and r.json["text"] == "Mine.", r.body


def test_resumed_reply_sends_one_quote(calls):
    opk, r = _reply_resumed(calls, bcc=["dee@example.test"])
    res, code = answered(sending.send, _payload(r))
    assert code == 200 and res["ok"], res
    m, meta, built = _sent(calls)
    text, html = _parts(m)
    assert text.count("wrote:") == 1 and text.startswith(OWN + "\n\nOn Mon, 02 Feb 2026"), text
    assert html.count("<blockquote") == 1 and html.startswith(own_html(OWN)), html
    assert str(m["In-Reply-To"]) == "<orig1@example.test>", m["In-Reply-To"]
    assert str(m["References"]) == "<root@example.test> <orig1@example.test>", m["References"]
    assert m["Bcc"] is None and "dee@example.test" in built["envelope"], built["envelope"]
    assert meta["in_reply_to_pk"] == opk and meta["draft_id"] == r["draft_id"], meta


def test_resumed_forward_sends_each_file_once(calls):
    opk = _original()
    pk, _ = _saved_and_synced(calls, mode="forward", in_reply_to_pk=opk, subject="Fwd: Plans", text="FYI")
    r, _ = _resume(pk)
    res, code = answered(sending.send, _payload(r, to=["dee@example.test"]))
    assert code == 200, res
    m, meta, _ = _sent(calls)
    files = [p.get_filename() for p in m.iter_attachments()]
    text, _ = _parts(m)
    assert files == ["map.pdf"], files
    assert text.count("---------- Forwarded message ----------") == 1, text
    assert m["In-Reply-To"] is None and meta["in_reply_to_pk"] is None, meta


def test_send_never_fetches_the_original(calls, monkeypatch):
    _, r = _reply_resumed(calls)

    def refuse(pk):
        raise AssertionError(f"fetched pk {pk}")
    monkeypatch.setattr(mailbox.ingest, "fetch_body", refuse)
    res, code = answered(sending.send, _payload(r))
    assert code == 200 and res["ok"], res


def test_renumbered_original_still_gets_answered(calls):
    opk, r = _reply_resumed(calls)
    c = store.conn()
    store.delete_message(c, opk)
    c.commit()
    c.close()
    npk = _original(uid=8)
    sending.send(_payload(r))
    assert _sent(calls)[1]["in_reply_to_pk"] == npk


def test_deleted_original_sends_threaded_without_answered(calls):
    opk, r = _reply_resumed(calls)
    c = store.conn()
    store.delete_message(c, opk)
    c.commit()
    c.close()
    res, code = answered(sending.send, _payload(r))
    assert code == 200, res
    m, meta, _ = _sent(calls)
    assert str(m["In-Reply-To"]) == "<orig1@example.test>" and meta["in_reply_to_pk"] is None, meta


def test_changed_tail_sends_plain_text(calls):
    _, r = _reply_resumed(calls)
    p = _payload(r)
    p["resume"]["tail_text"] = r["tail_text"].replace("Saturday", "Sunday")
    sending.send(p)
    text, html = _parts(_sent(calls)[0])
    assert html is None and text == OWN + p["resume"]["tail_text"], (text, html)


def test_whole_resume_sends_as_typed(calls):
    raw = _foreign([lambda m: m.set_content("On my way.\n\n> Shall we meet?\n")],
                   [("In-Reply-To", "<orig1@example.test>")])
    r, _ = _resume(_synced(raw, 12))
    assert r["split"] == "whole", r
    sending.send(_payload(r, text=r["text"] + "\nSee you."))
    m, _, _ = _sent(calls)
    text, html = _parts(m)
    assert html is None and text == "On my way.\n\n> Shall we meet?\nSee you.\n", repr(text)
    assert str(m["In-Reply-To"]) == "<orig1@example.test>"


def test_resave_three_times_keeps_one_quote_and_the_bcc(calls):
    _, r = _reply_resumed(calls, bcc=["dee@example.test"])
    for i in range(3):
        res, code = answered(drafts.save, _payload(r, text=r["text"] + f"\nEdit {i}."))
        assert code == 200 and res["ok"], res
        r, code = _resume(_synced(calls.appended[-1]["raw"], res["uid"]))
        assert code == 200 and r["split"] == "exact", r
    assert r["text"] == OWN + "\nEdit 0.\nEdit 1.\nEdit 2." and r["bcc"] == ["dee@example.test"], r
    text, html = _parts(message_from_bytes(calls.appended[-1]["raw"], policy=policy.default))
    assert text.count("wrote:") == 1 and html.count("<blockquote") == 1, (text, html)


def test_bad_resume_shape_is_400(calls):
    _, r = _reply_resumed(calls)
    res, code = answered(sending.send, _payload(r, resume="x"))
    assert (code, res["detail"]) == (400, "bad draft data"), res
    assert calls.sent == []


def test_unflagged_reply_still_needs_its_original(calls):
    res, code = answered(sending.send, {"mode": "reply", "account": ACCT, "to": ["bo@example.test"], "subject": "Re: x",
                                "text": "hi", "in_reply_to_pk": "<orig1@example.test>"})
    assert (code, res["detail"]) == (400, "missing original message"), res


def test_replaced_copy_is_refused_and_drafts_resynced(calls, monkeypatch):
    pk, _ = _saved_and_synced(calls, text="Old words.")
    synced = []
    monkeypatch.setattr(mailbox.ingest, "sync_folder", lambda acct, folder, full=False, recent=None: synced.append((acct["email"], folder, full)))
    monkeypatch.setattr(drafts, "_live", lambda acct, folder, uid: False)
    r, code = _resume(pk)
    assert code == 409 and "changed since the list loaded" in r["detail"], (code, r)
    c = store.conn()
    gone = store.get_message(c, pk) is None
    c.close()
    assert gone and synced == [(ACCT, "Drafts", True)], synced


def test_a_draft_waiting_in_the_outbox_is_not_reopened(calls):
    pk, res = _saved_and_synced(calls, text="Send this tomorrow.")
    c = store.conn()
    oid, _ = book.outbox_insert(c, {"account": ACCT, "idempotency_key": "k-later", "message_id": "<later@example.test>",
                                     "envelope": "[]", "recipients": "{}", "subject": "Plans",
                                     "raw": b"x", "next_attempt_at": 4_000_000_000, "draft_id": res["draft_id"],
                                     "created_at": "2026-11-01T10:00:00"})
    c.commit()
    c.close()
    r, code = _resume(pk)
    assert code == 423 and "in the outbox" in r["detail"], (code, r)
    c = store.conn()
    outbox.QUEUE.cancel_queued(c, oid)
    c.commit()
    c.close()
    assert _resume(pk)[1] == 200                                # cancelled: free to edit


@pytest.mark.parametrize("change", ["sent", "saved"])
def test_a_copy_replaced_or_sent_while_edit_waited_is_refused(change, calls, monkeypatch):
    pk, res = _saved_and_synced(calls, text="Mine.")

    def meanwhile(acct, folder, uid):
        c = store.conn()                                    # between the probe and the lock
        if change == "sent":
            book.draft_delete(c, res["draft_id"])
        else:
            c.execute("UPDATE drafts SET uid=uid+1000 WHERE id=?", (res["draft_id"],))
        c.commit()
        c.close()
        return True
    monkeypatch.setattr(drafts, "_live", meanwhile)
    synced = []
    monkeypatch.setattr(mailbox.ingest, "sync_folder", lambda acct, folder, full=False, recent=None: synced.append(full))
    r, code = _resume(pk)
    assert code == 409 and "changed since the list loaded" in r["detail"] and synced == [True], (code, r)


def test_save_and_send_cleanup_move_the_row_under_the_lock(calls, monkeypatch):
    held, real_settle, real_delete = [], book.draft_settle_push, book.draft_delete
    monkeypatch.setattr(book, "draft_settle_push", lambda c, *a: (held.append(("settle", gates.held(ACCT))), real_settle(c, *a))[1])
    monkeypatch.setattr(book, "draft_delete", lambda c, did: (held.append(("delete", gates.held(ACCT))), real_delete(c, did))[1])
    did = _save()["draft_id"]
    outbox._side_effects(1, {"sent_appended": 1, "original_key": None, "orig_message_id": None, "answered_set": 1,
                             "followup_recorded": 1, "draft_id": did, "draft_deleted": 0},
                         {"email": ACCT})
    assert held == [("settle", True), ("delete", True)], held
    assert _row(did) is None and len(calls.deleted) == 1, calls.deleted


def test_unreachable_server_opens_the_local_copy(calls, monkeypatch):
    pk, _ = _saved_and_synced(calls, text="Mine.")
    monkeypatch.setattr(drafts, "_live", lambda acct, folder, uid: None)
    r, code = _resume(pk)
    assert code == 200 and r["text"] == "Mine.", r


def test_headers_that_wont_parse_fall_back_to_their_raw_values(calls):
    opk = _original()
    raw = (b"From: Ada <ada@example.test>\r\nTo: Bo Park <bo@example.test>\r\nSubject: Re: Plans\r\n"
           b"Bcc: =?utf-8?q?Cy=0D=0AX?= <cy@example.test>, dee@example.test\r\n"
           b"In-Reply-To: " + b"(" * 1200 + b"<orig1@example.test>\r\n"
           b"References:\r\n <root@example.test>\r\n <orig1@example.test>\r\n"
           b"Message-ID: <hostile1@example.test>\r\n\r\nOn it.\r\n")
    r, code = _resume(_synced(raw, 21))
    assert code == 200, (code, r)
    assert r["bcc"] == ["cy@example.test", "dee@example.test"], r["bcc"]       # bare addresses, nothing lost
    assert (r["in_reply_to"], r["references"]) == ("<orig1@example.test>", "<root@example.test> <orig1@example.test>"), r
    assert (r["mode"], r["original_key"], r["to"]) == ("reply", _key(opk), ["Bo Park <bo@example.test>"]), r


def test_long_subject_reopens_as_drafted_however_often_it_is_saved(calls):
    subj = "Re: Notes from the quarterly planning meeting about the north gate renovation and the budget review"
    pk, res = _saved_and_synced(calls, subject=subj, text="Notes.")
    for uid in (701, 702, 703):
        r, code = _resume(pk)
        assert code == 200 and r["subject"] == subj, (code, r["subject"])
        _save(draft_id=res["draft_id"], subject=r["subject"], text=r["text"])
        pk = _synced(calls.appended[-1]["raw"], uid)
    res, code = answered(sending.send, _payload(_resume(pk)[0], in_reply_to_pk=None))
    assert code == 200 and str(_sent(calls)[0]["Subject"]) == subj, str(_sent(calls)[0]["Subject"])


def test_comments_nested_past_any_parser_still_give_bare_addresses(calls):
    raw = (b"From: Ada <ada@example.test>\r\nTo: Bo <bo@example.test>\r\nSubject: x\r\n"
           b"Bcc: " + b"(" * 3000 + b"cy@example.test, dee@example.test\r\nMessage-ID: <deep1@example.test>\r\n\r\nhi\r\n")
    r, code = _resume(_synced(raw, 40))
    assert code == 200 and r["bcc"] == ["cy@example.test", "dee@example.test"], (code, r)


def test_a_part_header_that_wont_parse_is_unreadable_not_a_500(calls):
    raw = (b"From: Ada <ada@example.test>\r\nTo: bo@example.test\r\nSubject: y\r\nMessage-ID: <deep2@example.test>\r\n"
           b"MIME-Version: 1.0\r\nContent-Type: multipart/mixed; boundary=\"M\"\r\n\r\n"
           b"--M\r\nContent-Type: text/plain\r\n\r\nHi\r\n--M\r\nContent-Type: text/plain\r\n"
           b"Content-Transfer-Encoding: " + b"(" * 3000 + b"base64\r\n\r\nSGk=\r\n--M--\r\n")
    r, code = _resume(_synced(raw, 41))
    assert code == 422 and "can’t read this draft" in r["detail"], (code, r)


def test_raw_utf8_headers_come_back_as_text_and_send_cleanly(calls):
    raw = ("From: Ada <ada@example.test>\r\nTo: José Ñúñez <jose@bücher.example>, Bo <bo@example.test>\r\n"
           "Cc: =?utf-8?q?Zo=C3=AB?= <zoe@example.test>\r\nSubject: Re: Plans\r\n"
           "Message-ID: <utf1@example.test>\r\n\r\nOn it.\r\n").encode()
    r, code = _resume(_synced(raw, 22))
    assert code == 200 and r["to"] == ["José Ñúñez <jose@bücher.example>", "Bo <bo@example.test>"], r
    assert r["cc"] == ["Zoë <zoe@example.test>"], r["cc"]
    res, code = answered(sending.send, _payload(r, in_reply_to_pk=None))
    assert code == 200, res
    m, _, built = _sent(calls)
    assert b"unknown-8bit" not in built["raw"] and b"xn--bcher-kva.example" in built["raw"], built["raw"][:400]
    assert m["To"].addresses[0].display_name == "José Ñúñez", str(m["To"])


@pytest.mark.parametrize("exc, want", [
    pytest.param(OSError("no route to host"), 502, id="no_route"),
    pytest.param(imaplib.IMAP4.error("FETCH BAD"), 502, id="server_refused"),
    pytest.param(ValueError("address parts cannot contain CR or LF"), 422, id="bad_address"),
    pytest.param(RecursionError(), 422, id="too_deep"),
])
def test_unreadable_draft_is_422_and_only_a_dead_server_502(exc, want, calls, monkeypatch):
    pk, _ = _saved_and_synced(calls, text="Mine.")

    def fetch(pk):
        raise exc
    monkeypatch.setattr(mailbox.ingest, "fetch_body", fetch)
    r, code = _resume(pk)
    assert code == want, (code, r)
    assert ("Try again" in r["detail"]) is (want == 502) and ("can’t read" in r["detail"]) is (want == 422), r


def test_a_body_that_wont_parse_is_unreadable(calls, monkeypatch):
    pk, _ = _saved_and_synced(calls, text="Mine.")

    def parse(raw):
        raise ValueError("address parts cannot contain CR or LF")
    monkeypatch.setattr(mime, "parse", parse)              # a body cached by an older Python
    r, code = _resume(pk)
    assert code == 422 and "can’t read this draft" in r["detail"], (code, r)


class _FakeIMAP:
    def __init__(self, uids, uidv=777, select="OK", boom=False, search="OK"):
        self.uids, self.untagged_responses, self._select, self.boom = uids, {"UIDVALIDITY": [str(uidv).encode()]}, select, boom
        self.search, self.calls = search, []

    def select(self, folder, readonly=False):
        self.calls.append(("select", folder, readonly))
        if self.boom:
            raise OSError("connection reset")
        return self._select, [b"3"]

    def response(self, code):
        return code, self.untagged_responses.get(code, [None])

    def uid(self, *args):
        self.calls.append(("uid",) + args)
        if self.search != "OK":                         # a busy server: its words, not UIDs
            return self.search, [b"[UNAVAILABLE] Mailbox is being synchronized 42"]
        want = args[-1]
        return "OK", [b" ".join(u.encode() for u in self.uids if u == want)]

    def logout(self):
        self.calls.append(("logout",))


@pytest.fixture
def draft_live(home, monkeypatch):
    """draft_live(fake, uid): what the probe says of `uid` in Drafts, on the connection `fake`."""
    c = store.conn()
    store.set_uidvalidity(c, ACCT, "Drafts", 777)
    c.commit()
    c.close()

    def ask(fake, uid=42):
        monkeypatch.setattr(accounts, "connect", lambda a, timeout=60: fake)
        return drafts._live({"email": ACCT}, "Drafts", uid)
    return ask


def test_draft_live_reads_uidvalidity_and_uid(draft_live):
    fake = _FakeIMAP(["42"])
    assert draft_live(fake) is True
    assert fake.calls[0] == ("select", '"Drafts"', True) and fake.calls[1] == ("uid", "SEARCH", None, "UID", "42")


@pytest.mark.parametrize("fake, uid, want", [
    pytest.param(dict(uids=["41"]), 42, False, id="another_uid"),
    pytest.param(dict(uids=["42"], uidv=888), 42, False, id="uids_rolled"),
    pytest.param(dict(uids=["42"], select="NO"), 42, None, id="select_refused"),
    pytest.param(dict(uids=["42"], boom=True), 42, None, id="connection_reset"),
    pytest.param(dict(uids=["42"], search="NO"), 7, None, id="search_refused_is_not_gone"),
    pytest.param(dict(uids=["42"]), None, None, id="no_uid"),
])
def test_draft_live_answers(fake, uid, want, draft_live):
    assert draft_live(_FakeIMAP(**fake), uid) is want


def test_draft_live_is_a_short_stateless_probe(home, monkeypatch):
    timeouts = []
    fake = _FakeIMAP(["42"])
    monkeypatch.setattr(accounts, "connect", lambda a, timeout=60: (timeouts.append(timeout), fake)[1])
    assert drafts._live({"email": ACCT}, "Drafts", 42) is True
    assert timeouts and timeouts[0] <= 15 and fake.calls[-1] == ("logout",), (timeouts, fake.calls)


def test_draft_live_logs_out_after_a_reset(home, monkeypatch):
    fake = _FakeIMAP(["42"], boom=True)
    monkeypatch.setattr(accounts, "connect", lambda a, timeout=60: fake)
    assert drafts._live({"email": ACCT}, "Drafts", 42) is None and fake.calls[-1] == ("logout",)


def test_draft_live_with_no_connection_is_unknown(home, monkeypatch):
    def refused(a, timeout=60):
        raise OSError("no route to host")
    monkeypatch.setattr(accounts, "connect", refused)
    assert drafts._live({"email": ACCT}, "Drafts", 42) is None


def test_edit_probes_the_server_without_holding_the_ops_lock(calls, monkeypatch):
    pk, _ = _saved_and_synced(calls, text="Mine.")
    held = []
    monkeypatch.setattr(drafts, "_live", lambda acct, folder, uid: (held.append(gates.held(acct["email"])), True)[1])
    r, code = _resume(pk)
    assert code == 200 and held == [False], (code, held)
