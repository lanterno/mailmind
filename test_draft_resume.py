"""Reopening a saved draft (compose's resume helpers): a reply or forward draft already holds its
quote or forwarded section, so it must come back split into your text and that tail, and go out
again with each part once. Hermetic — temp database and config, and every IMAP or SMTP call is
replaced with a recorder, so nothing can leave. All addresses are fictional.
Run: python3 test_draft_resume.py"""
import base64
import contextlib
import hashlib
import http.client
import json
import os
import tempfile
import threading
from email import message_from_bytes, policy
from email.message import EmailMessage

import store
import config
_TMP = tempfile.mkdtemp()
store.DB = os.path.join(_TMP, "mailmind.db")        # before anything opens a connection
config.CONFIG = os.path.join(_TMP, "mailmind.config.json")
with open(config.CONFIG, "w") as f:
    json.dump({"accounts": [{"email": "ada@example.test", "type": "imap", "host": "imap.example.test"},
                            {"email": "zed@example.test", "type": "imap", "host": "imap.example.test"}]}, f)
import compose
import mailparse
import pool
import server

ACCT = "ada@example.test"
ORIG = {"from_name": "Bo Park", "from_addr": "bo@example.test", "date": "Mon, 2 Feb 2026 09:00:00 +0000",
        "subject": "Plans", "to": ACCT, "cc": "cy@example.test",
        "text": "Shall we meet Saturday?\nThe map is attached.", "html": "<p>Shall we meet <b>Saturday</b>?</p>"}
OWN = "Saturday works.\n\n-- \nAda"
_uid = [100]
CALLS = {}


def _fresh(uidv=777, appenduid=True):
    """A new temp database, and recorders in place of every IMAP call a draft save makes.
    appenduid=False is a server that names no UID for what was appended."""
    store.DB = os.path.join(tempfile.mkdtemp(), "mailmind.db")
    store.init()
    CALLS.clear()
    CALLS.update(append=[], delete=[], sent=[])

    def append(acct, raw, prev_uid=None, prev_uidvalidity=None, prev_msgid=None):
        _uid[0] += 1
        CALLS["append"].append({"account": acct["email"], "raw": raw, "prev_uid": prev_uid,
                                "prev_uidvalidity": prev_uidvalidity, "prev_msgid": prev_msgid})
        return "Drafts", (_uid[0] if appenduid else None), uidv

    def delete(acct, folder, uid, uidvalidity=None):
        CALLS["delete"].append((acct["email"], folder, uid, uidvalidity))
    def enqueue(built, meta):
        CALLS["sent"].append((built, meta))
        return len(CALLS["sent"]), True
    server.smtpsend.append_to_drafts = append
    server.smtpsend.delete_draft_uid = delete
    server.outbox.enqueue = enqueue
    server.ingest.fetch_body = _real_fetch_body
    server._draft_live = lambda acct, folder, uid: True           # the server still holds the copy


_real_fetch_body = server.ingest.fetch_body
_real_draft_live = server._draft_live


def _save(account=ACCT, draft_id=None, **kw):
    body = {"mode": "new", "account": account, "to": ["bo@example.test"], "subject": "Plans",
            "text": "Saturday works.", "draft_id": draft_id, **kw}
    res, code = server.do_draft(body)
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
    c.execute("INSERT INTO messages (account, kind, folder, uid, message_id, thread_id, ts, from_addr, from_name, "
              "subject, refs, recipients) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
              (ACCT, "imap", "INBOX", uid, "<orig1@example.test>", "t-orig", 1_800_000_000, "bo@example.test",
               "Bo Park", "Plans", "<root@example.test>", "ada@example.test, cy@example.test"))
    pk = c.execute("SELECT pk FROM messages WHERE uid=? AND folder='INBOX'", (uid,)).fetchone()["pk"]
    store.store_body(c, pk, m.as_bytes(), "2026-10-31T09:00:00")
    c.commit()
    c.close()
    return pk


def _synced(raw, uid, account=ACCT, folder="Drafts"):
    """A Drafts message as sync would store it once fetched: headers from the bytes, body kept,
    the folder's UIDVALIDITY known. Returns its pk."""
    m = message_from_bytes(raw, policy=policy.default)
    c = store.conn()
    store.set_uidvalidity(c, account, folder, 777)
    c.execute("INSERT INTO messages (account, kind, folder, uid, message_id, thread_id, ts, from_addr, subject, "
              "in_reply_to, refs) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
              (account, "imap", folder, uid, str(m["Message-ID"] or ""), f"td{uid}", 1_800_000_100, account,
               str(m["Subject"] or ""), str(m["In-Reply-To"] or "") or None, str(m["References"] or "") or None))
    pk = c.execute("SELECT pk FROM messages WHERE folder=? AND uid=?", (folder, uid)).fetchone()["pk"]
    store.store_body(c, pk, raw, "2026-10-31T10:00:00")
    c.commit()
    c.close()
    return pk


def _saved_and_synced(**kw):
    """Save a draft through do_draft, then sync back exactly the bytes appended. (pk, save result)"""
    res = _save(**kw)
    return _synced(CALLS["append"][-1]["raw"], res["uid"] if res["uid"] is not None else 555), res


def _resume(pk):
    return server.draft_resume({"pk": pk})


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


def _sent(i=-1):
    """(parsed message, meta, built) of a send the outbox recorder caught."""
    built, meta = CALLS["sent"][i]
    return message_from_bytes(built["raw"], policy=policy.default), meta, built


def _parts(m):
    text = m.get_body(preferencelist=("plain",))
    html = m.get_body(preferencelist=("html",))
    return (compose.lf(text.get_content()) if text else None), (html.get_content() if html else None)


def _reply_resumed(**kw):
    opk = _original()
    pk, _ = _saved_and_synced(mode="reply", in_reply_to_pk=opk, subject="Re: Plans", text=OWN,
                              to=["Bo Park <bo@example.test>"], **kw)
    r, code = _resume(pk)
    assert code == 200, r
    return opk, r


def _row(did):
    c = store.conn()
    r = store.draft_get(c, did)
    c.close()
    return r


def _round_trip(text, html=None, attachments=()):
    """What a draft's text/plain reads as once it has been saved and fetched back."""
    raw = compose.build_message({"account": ACCT, "to": ["bo@example.test"], "subject": "Re: Plans",
                                 "text": text, "html": html, "attachments": list(attachments)}, draft=True)["raw"]
    return mailparse.parse(raw)


def test_own_html_is_where_reply_and_forward_html_start():
    for fn in (compose.reply_bodies, compose.forward_bodies):
        _, html = fn('a & <b> "q"\nline', ORIG)
        assert html.startswith(compose.own_html('a & <b> "q"\nline')), (fn.__name__, html[:80])
    assert compose.own_html("a\nb") == "<div>a<br>b</div><br>"


def test_quote_tail_of_fresh_reply_and_forward():
    text, html = compose.reply_bodies(OWN, ORIG)
    tail, tail_h = compose.quote_tail(OWN, text, html)
    assert text == OWN + tail and tail.startswith("\n\nOn Mon, 2 Feb 2026"), tail
    assert html == compose.own_html(OWN) + tail_h and "<blockquote" in tail_h, tail_h
    text, html = compose.forward_bodies(OWN, ORIG)
    tail, tail_h = compose.quote_tail(OWN, text, html)
    assert tail.startswith("\n\n---------- Forwarded message ----------"), tail
    assert tail_h.startswith("<div>---------- Forwarded message"), tail_h
    assert compose.quote_tail(OWN, OWN, None) == (None, None)
    assert compose.quote_tail("other", "text", "<p>x</p>") == (None, None)


def test_recorded_tail_survives_a_real_round_trip():
    bad = []
    origs = [ORIG, {**ORIG, "text": "no newline at the end", "html": None},
             {**ORIG, "text": None, "html": "<p>only <a href='https://x.example.test'>html</a></p>"},
             {**ORIG, "text": "", "html": None}]
    for o in origs:
        for own in ("", "\n\n\n", OWN, 'a & <b> "q"'):
            for fn in (compose.reply_bodies, compose.forward_bodies):
                text, html = fn(own, o)
                tail, _ = compose.quote_tail(own, text, html)
                p = _round_trip(text, html)
                got = compose.resume_split(p["text"], {"own_text": own, "tail_text": tail}, False)
                if got != (own, tail, "tail"):
                    bad.append((fn.__name__, own, o.get("text"), got))
    assert not bad, bad


def test_resave_three_times_is_stable():
    text, html = compose.reply_bodies(OWN, ORIG)
    tail, _ = compose.quote_tail(OWN, text, html)
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
    tail, _ = compose.quote_tail(OWN, text, html)
    edited = text.replace("> Shall we", "> Shall we not")
    own, t, how = compose.resume_split(_round_trip(edited)["text"], {"own_text": OWN, "tail_text": tail}, False)
    assert (t, how) == ("", "whole") and own == edited.rstrip("\n"), (own, how)


def test_pasted_quote_cuts_only_the_last_copy():
    text, html = compose.reply_bodies(OWN, ORIG)
    tail, _ = compose.quote_tail(OWN, text, html)
    mine = "Pasted on purpose:" + tail.rstrip("\n") + "\nand more"
    own, t, how = compose.resume_split(mine + tail, {"own_text": "", "tail_text": tail}, False)
    assert (own, t, how) == (mine, tail, "tail"), (own, how)


def test_whole_drops_one_trailing_newline():
    assert compose.resume_split("Hello\n\n", None, False) == ("Hello\n", "", "whole")
    assert compose.resume_split("Hello", None, False) == ("Hello", "", "whole")
    assert compose.resume_split("a\r\nb\r\n", {"own_text": None, "tail_text": None}, False) == ("a\nb", "", "whole")


def test_no_text_and_not_exact_is_none():
    assert compose.resume_split(None, {"own_text": "Mine", "tail_text": None}, False) is None
    assert compose.resume_split(None, None, False) is None


def test_addr_chip_round_trips():
    addr = "bo@example.test"
    assert compose.addr_chip("", addr) == addr and compose.addr_chip("BO@example.test", addr) == addr
    for name in ("Bo Park", "Park, Bo", 'Bo "B" Park', "Dr. Bo", "bo@work", "(Bo)", "back\\slash",
                 "semi;colon", "Zoë Ünal", "x<y>"):
        chip = compose.addr_chip(name, addr)
        assert compose.parse_addr_list([chip]) == [(name, addr)], (name, chip, compose.parse_addr_list([chip]))
    assert compose.addr_chip("Bo Park", addr) == "Bo Park <bo@example.test>"
    assert compose.addr_chip("Park, Bo", addr) == '"Park, Bo" <bo@example.test>'


def test_thread_ids_validate_dedupe_and_cap():
    irt, refs = compose.thread_ids("junk <bad> <a@example.test> <b@example.test>",
                                   "<r@example.test> <r@example.test> <nope> "
                                   + " ".join(f"<m{i}@example.test>" for i in range(60)))
    assert irt == "<a@example.test>", irt
    assert len(refs) == 50 and refs[0] == "<r@example.test>" and refs[1] == "<m11@example.test>", refs[:2]
    assert refs[-1] == "<m59@example.test>" and len(set(refs)) == 50
    assert compose.thread_ids(None, None) == (None, [])
    assert compose.thread_ids("<x@example.test>\r\nBcc: evil@example.test", "")[0] == "<x@example.test>"


def test_from_change_never_passes_the_old_uid():
    _fresh()
    did = _save()["draft_id"]
    old_uid = _row(did)["uid"]
    _save(account="zed@example.test", draft_id=did)
    last = CALLS["append"][-1]
    assert last["account"] == "zed@example.test" and last["prev_uid"] is None, last
    assert last["prev_uidvalidity"] is None and last["prev_msgid"] is None, last
    assert old_uid != _row(did)["uid"]


def test_from_change_deletes_the_old_copy_in_its_own_account():
    _fresh()
    did = _save()["draft_id"]
    old_uid = _row(did)["uid"]
    _save(account="zed@example.test", draft_id=did)
    assert CALLS["delete"] == [(ACCT, "Drafts", old_uid, 777)], CALLS["delete"]


def test_from_change_then_two_saves_keeps_the_row_in_step():
    _fresh()
    did = _save()["draft_id"]
    _save(account="zed@example.test", draft_id=did)
    row = _row(did)
    assert row["account"] == "zed@example.test", dict(row)
    _save(account="zed@example.test", draft_id=did, text="Saturday at ten.")
    last = CALLS["append"][-1]
    assert last["account"] == "zed@example.test" and last["prev_uid"] == row["uid"], last
    assert len(CALLS["delete"]) == 1, CALLS["delete"]          # only the From change removed anything


def test_unvouched_old_copy_is_left_alone():
    _fresh(uidv=None)                                           # the server named no UIDVALIDITY
    did = _save()["draft_id"]
    _save(account="zed@example.test", draft_id=did)
    assert CALLS["delete"] == [], CALLS["delete"]
    assert CALLS["append"][-1]["prev_uid"] is None


def test_other_apps_draft_registers_with_folder_uidvalidity():
    _fresh()
    c = store.conn()
    store.set_uidvalidity(c, ACCT, "Drafts", 4242)
    c.execute("INSERT INTO messages (account, kind, folder, uid, message_id, thread_id, ts, from_addr, subject) "
              "VALUES (?,?,?,?,?,?,?,?,?)", (ACCT, "imap", "Drafts", 9, "<other@example.test>", "t9",
                                              1_800_000_000, ACCT, "From my phone"))
    c.commit()
    row = c.execute("SELECT * FROM messages WHERE uid=9").fetchone()
    c.close()
    info = server._draft_info(row)
    d = _row(info["draft_id"])
    assert (d["uid"], d["uidvalidity"], d["mode"]) == (9, 4242, "new"), dict(d)


def test_vanished_draft_row_is_tracked_again():
    _fresh()
    did = _save()["draft_id"]
    c = store.conn()
    store.draft_delete(c, did)                                  # sent or discarded in another tab
    c.commit()
    c.close()
    res = _save(draft_id=did, text="Saturday at ten.")
    assert res["draft_id"] != did and _row(res["draft_id"]) is not None, res
    assert CALLS["append"][-1]["prev_uid"] is None
    again = _save(draft_id=res["draft_id"], text="Saturday at eleven.")
    assert again["draft_id"] == res["draft_id"] and CALLS["append"][-1]["prev_uid"] == res["uid"], again


def _cols():
    c = store.conn()
    cols = {r[1] for r in c.execute("PRAGMA table_info(drafts)")}
    v = c.execute("PRAGMA user_version").fetchone()[0]
    c.close()
    return cols, v


SIDECAR = {"own_text", "tail_text", "tail_html", "bcc", "raw_sha"}


def test_migration_20_is_idempotent():
    _fresh()
    store.init()
    cols, v = _cols()
    assert v >= 20 and SIDECAR <= cols, (v, cols)


def test_v19_database_gains_the_columns():
    _fresh()
    c = store.conn()
    for col in SIDECAR:
        c.execute(f"ALTER TABLE drafts DROP COLUMN {col}")
    c.execute("INSERT INTO drafts (account, folder, uid, mode, subject) VALUES (?,?,?,?,?)",
              (ACCT, "Drafts", 3, "reply", "Re: Plans"))
    c.execute("PRAGMA user_version=19")
    c.commit()
    c.close()
    store.init()
    cols, v = _cols()
    assert v == 20 and SIDECAR <= cols, (v, cols)
    row = _row(1)
    assert (row["uid"], row["mode"], row["own_text"], row["bcc"]) == (3, "reply", None, None), dict(row)


def test_new_draft_records_its_text():
    _fresh()
    res = _save(text="Saturday works.\r\nSee you.")
    row = _row(res["draft_id"])
    assert row["own_text"] == "Saturday works.\nSee you." and row["tail_text"] is None, dict(row)
    assert row["tail_html"] is None and row["bcc"] is None, dict(row)
    assert row["raw_sha"] == hashlib.sha256(CALLS["append"][-1]["raw"]).hexdigest()


def test_reply_draft_records_text_quote_and_html():
    _fresh()
    opk = _original()
    res = _save(mode="reply", in_reply_to_pk=opk, subject="Re: Plans", text=OWN)
    row = _row(res["draft_id"])
    raw = CALLS["append"][-1]["raw"]
    assert row["own_text"] == OWN and row["tail_text"].startswith("\n\nOn Mon, 02 Feb 2026"), dict(row)
    assert row["tail_text"].count("wrote:") == 1 and "> Shall we meet Saturday?" in row["tail_text"]
    assert row["tail_html"].startswith("<div>On Mon") and "<blockquote" in row["tail_html"], row["tail_html"]
    assert compose.lf(mailparse.parse(raw)["text"]) == OWN + row["tail_text"]


def test_forward_draft_records_the_banner():
    _fresh()
    opk = _original()
    res = _save(mode="forward", in_reply_to_pk=opk, subject="Fwd: Plans", text="FYI")
    row = _row(res["draft_id"])
    assert row["own_text"] == "FYI" and row["tail_text"].startswith("\n\n---------- Forwarded message"), dict(row)
    assert "From: Bo Park <bo@example.test>" in row["tail_text"], row["tail_text"]
    files = [a["filename"] for a in mailparse.parse(CALLS["append"][-1]["raw"])["attachments"]]
    assert files == ["map.pdf"], files


def test_bcc_is_kept_beside_the_draft_not_in_it():
    _fresh()
    res = _save(bcc=['"Park, Cy" <cy@example.test>', "dee@example.test"])
    raw = CALLS["append"][-1]["raw"]
    head = raw.split(b"\r\n\r\n", 1)[0]
    assert b"\nBcc:" not in b"\n" + head and b"cy@example.test" not in raw, head
    row = _row(res["draft_id"])
    assert json.loads(row["bcc"]) == ['"Park, Cy" <cy@example.test>', "dee@example.test"], row["bcc"]
    assert row["raw_sha"] == hashlib.sha256(raw).hexdigest()


def test_reply_draft_reopens_with_quote_files_bcc_and_thread():
    _fresh()
    opk = _original()
    pk, res = _saved_and_synced(mode="reply", in_reply_to_pk=opk, subject="Re: Plans", text=OWN,
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
    assert r["orig_pk"] == opk and r["participants"] == ["bo@example.test", ACCT, "cy@example.test"], r
    assert [(f["filename"], base64.b64decode(f["b64"])) for f in r["files"]] == [("notes.txt", b"bring water")], r["files"]
    assert r["notes"] == {"inline": 0, "bcc_dropped": 0, "plain": False}, r["notes"]


def test_forward_draft_reopens_with_its_files_as_chips():
    _fresh()
    opk = _original()
    pk, _ = _saved_and_synced(mode="forward", in_reply_to_pk=opk, subject="Fwd: Plans", text="FYI")
    r, code = _resume(pk)
    assert code == 200 and (r["mode"], r["text"], r["split"]) == ("forward", "FYI", "exact"), r
    assert r["tail_text"].startswith("\n\n---------- Forwarded message ----------"), r["tail_text"]
    assert [(f["filename"], f["content_type"], f["size"]) for f in r["files"]] == [("map.pdf", "application/pdf", 12)], r
    assert (r["orig_pk"], r["in_reply_to"]) == (None, ""), r


def test_original_deleted_still_reopens():
    _fresh()
    opk = _original()
    pk, _ = _saved_and_synced(mode="reply", in_reply_to_pk=opk, subject="Re: Plans", text=OWN)
    c = store.conn()
    store.delete_message(c, opk)                                # archived: the local row is gone
    c.commit()
    c.close()
    r, code = _resume(pk)
    assert code == 200 and (r["mode"], r["orig_pk"], r["participants"]) == ("reply", None, []), r
    assert r["in_reply_to"] == "<orig1@example.test>" and r["text"] == OWN, r


def test_original_renumbered_found_by_message_id():
    _fresh()
    opk = _original(uid=7)
    pk, _ = _saved_and_synced(mode="reply", in_reply_to_pk=opk, subject="Re: Plans", text=OWN)
    c = store.conn()
    store.delete_message(c, opk)
    c.commit()
    c.close()
    npk = _original(uid=8)                                      # reconcile gave it a new row
    assert npk != opk
    assert _resume(pk)[0]["orig_pk"] == npk


def test_raw_sha_proves_the_version_without_a_uid():
    _fresh(appenduid=False)
    pk, res = _saved_and_synced(text="Mine.", bcc=["dee@example.test"])
    assert res["uid"] is None
    r, _ = _resume(pk)
    assert (r["split"], r["text"], r["bcc"]) == ("exact", "Mine.", ["dee@example.test"]), r


def test_changed_copy_uses_the_recorded_tail_and_drops_bcc():
    _fresh(appenduid=False)
    opk = _original()
    res = _save(mode="reply", in_reply_to_pk=opk, subject="Re: Plans", text=OWN, bcc=["dee@example.test"])
    raw = b"X-Rewritten-By: bridge\r\n" + CALLS["append"][-1]["raw"]   # the server's copy isn't byte-for-byte ours
    r, code = _resume(_synced(raw, 601))
    assert code == 200 and (r["split"], r["text"], r["draft_id"]) == ("tail", OWN, res["draft_id"]), r
    assert r["tail_text"].count("wrote:") == 1, r["tail_text"]
    assert r["bcc"] == [] and r["notes"]["bcc_dropped"] == 1, r


def test_other_apps_draft_opens_whole_as_plain_text():
    _fresh()
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
    assert r["bcc"] == ["Cy Lee <cy@example.test>"] and r["orig_pk"] == opk, r
    names = sorted(f["filename"] for f in r["files"])
    assert len(names) == 2 and "plan.pdf" in names and any(n.startswith("inline-") and n.endswith(".png")
                                                            for n in names), names
    assert r["notes"] == {"inline": 1, "bcc_dropped": 0, "plain": True}, r["notes"]


def test_html_only_draft_is_refused():
    _fresh()
    raw = _foreign([lambda m: m.set_content("<p>Only <a href='https://x.example.test'>formatted</a></p>",
                                            subtype="html")])
    r, code = _resume(_synced(raw, 10))
    assert code == 422 and "only has formatted text" in r["error"], (code, r)


def test_attached_email_is_refused():
    _fresh()
    inner = EmailMessage()
    inner["Subject"] = "Old thread"
    inner.set_content("hello")

    def body(m):
        m.set_content("See the thread below.")
        m.add_attachment(inner)
    r, code = _resume(_synced(_foreign([body]), 11))
    assert code == 422 and "email attached as a file" in r["error"], (code, r)


def test_files_over_the_cap_are_refused():
    _fresh()
    pk, _ = _saved_and_synced(text="Big.", attachments=[{"filename": "a.bin", "content_type": "application/octet-stream",
                                                        "b64": base64.b64encode(b"x" * 64).decode()}])
    cap, server.MAX_ATTACH_BYTES = server.MAX_ATTACH_BYTES, 32
    try:
        r, code = _resume(pk)
    finally:
        server.MAX_ATTACH_BYTES = cap
    assert code == 422 and "more than 25 MB" in r["error"], (code, r)


def test_bad_pk_unknown_and_not_a_draft():
    _fresh()
    opk = _original()
    assert server.draft_resume({"pk": "x"}) == ({"ok": False, "error": "Missing draft."}, 400)
    assert server.draft_resume({})[1] == 400
    assert server.draft_resume({"pk": 99999})[1] == 404
    r, code = server.draft_resume({"pk": opk})
    assert code == 400 and r["error"] == "That message isn’t a draft.", r


def test_resume_route_requires_csrf():
    _fresh()
    pk, _ = _saved_and_synced(text="Mine.")
    srv = server._Server(("127.0.0.1", 0), server.H)
    port = srv.server_address[1]
    old_port, server.PORT = server.PORT, port
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        def post(headers):
            h = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            h.request("POST", "/api/draft/resume", json.dumps({"pk": pk}),
                      {"Content-Type": "application/json", **headers})
            resp = h.getresponse()
            out = resp.status, json.loads(resp.read() or b"{}")
            h.close()
            return out
        origin = {"Origin": f"http://127.0.0.1:{port}"}
        assert post(origin)[0] == 403
        code, body = post({**origin, "X-CSRF-Token": server.CSRF})
        assert code == 200 and body["text"] == "Mine.", (code, body)
    finally:
        srv.shutdown()
        srv.server_close()
        server.PORT = old_port


def test_resumed_reply_sends_one_quote():
    _fresh()
    opk, r = _reply_resumed(bcc=["dee@example.test"])
    res, code = server.do_send(_payload(r))
    assert code == 200 and res["ok"], res
    m, meta, built = _sent()
    text, html = _parts(m)
    assert text.count("wrote:") == 1 and text.startswith(OWN + "\n\nOn Mon, 02 Feb 2026"), text
    assert html.count("<blockquote") == 1 and html.startswith(compose.own_html(OWN)), html
    assert str(m["In-Reply-To"]) == "<orig1@example.test>", m["In-Reply-To"]
    assert str(m["References"]) == "<root@example.test> <orig1@example.test>", m["References"]
    assert m["Bcc"] is None and "dee@example.test" in built["envelope"], built["envelope"]
    assert meta["in_reply_to_pk"] == opk and meta["draft_id"] == r["draft_id"], meta


def test_resumed_forward_sends_each_file_once():
    _fresh()
    opk = _original()
    pk, _ = _saved_and_synced(mode="forward", in_reply_to_pk=opk, subject="Fwd: Plans", text="FYI")
    r, _ = _resume(pk)
    res, code = server.do_send(_payload(r, to=["dee@example.test"]))
    assert code == 200, res
    m, meta, _ = _sent()
    files = [p.get_filename() for p in m.iter_attachments()]
    text, _ = _parts(m)
    assert files == ["map.pdf"], files
    assert text.count("---------- Forwarded message ----------") == 1, text
    assert m["In-Reply-To"] is None and meta["in_reply_to_pk"] is None, meta


def test_send_never_fetches_the_original():
    _fresh()
    _, r = _reply_resumed()

    def refuse(pk):
        raise AssertionError(f"fetched pk {pk}")
    server.ingest.fetch_body = refuse
    res, code = server.do_send(_payload(r))
    assert code == 200 and res["ok"], res


def test_renumbered_original_still_gets_answered():
    _fresh()
    opk, r = _reply_resumed()
    c = store.conn()
    store.delete_message(c, opk)
    c.commit()
    c.close()
    npk = _original(uid=8)
    server.do_send(_payload(r))
    assert _sent()[1]["in_reply_to_pk"] == npk


def test_deleted_original_sends_threaded_without_answered():
    _fresh()
    opk, r = _reply_resumed()
    c = store.conn()
    store.delete_message(c, opk)
    c.commit()
    c.close()
    res, code = server.do_send(_payload(r))
    assert code == 200, res
    m, meta, _ = _sent()
    assert str(m["In-Reply-To"]) == "<orig1@example.test>" and meta["in_reply_to_pk"] is None, meta


def test_changed_tail_sends_plain_text():
    _fresh()
    _, r = _reply_resumed()
    p = _payload(r)
    p["resume"]["tail_text"] = r["tail_text"].replace("Saturday", "Sunday")
    server.do_send(p)
    text, html = _parts(_sent()[0])
    assert html is None and text == OWN + p["resume"]["tail_text"], (text, html)


def test_whole_resume_sends_as_typed():
    _fresh()
    raw = _foreign([lambda m: m.set_content("On my way.\n\n> Shall we meet?\n")],
                   [("In-Reply-To", "<orig1@example.test>")])
    r, _ = _resume(_synced(raw, 12))
    assert r["split"] == "whole", r
    server.do_send(_payload(r, text=r["text"] + "\nSee you."))
    m, _, _ = _sent()
    text, html = _parts(m)
    assert html is None and text == "On my way.\n\n> Shall we meet?\nSee you.\n", repr(text)
    assert str(m["In-Reply-To"]) == "<orig1@example.test>"


def test_resave_three_times_keeps_one_quote_and_the_bcc():
    _fresh()
    _, r = _reply_resumed(bcc=["dee@example.test"])
    for i in range(3):
        res, code = server.do_draft(_payload(r, text=r["text"] + f"\nEdit {i}."))
        assert code == 200 and res["ok"], res
        r, code = _resume(_synced(CALLS["append"][-1]["raw"], res["uid"]))
        assert code == 200 and r["split"] == "exact", r
    assert r["text"] == OWN + "\nEdit 0.\nEdit 1.\nEdit 2." and r["bcc"] == ["dee@example.test"], r
    text, html = _parts(message_from_bytes(CALLS["append"][-1]["raw"], policy=policy.default))
    assert text.count("wrote:") == 1 and html.count("<blockquote") == 1, (text, html)


def test_bad_resume_shape_is_400():
    _fresh()
    _, r = _reply_resumed()
    res, code = server.do_send(_payload(r, resume="x"))
    assert (code, res["error"]) == (400, "bad draft data"), res
    assert CALLS["sent"] == []


def test_unflagged_reply_still_needs_its_original():
    _fresh()
    res, code = server.do_send({"mode": "reply", "account": ACCT, "to": ["bo@example.test"], "subject": "Re: x",
                                "text": "hi", "in_reply_to_pk": "<orig1@example.test>"})
    assert (code, res["error"]) == (400, "missing original message"), res


def test_replaced_copy_is_refused_and_drafts_resynced():
    _fresh()
    pk, _ = _saved_and_synced(text="Old words.")
    synced = []
    real = server.ingest.sync_folder
    server.ingest.sync_folder = lambda acct, folder, full=False, recent=None: synced.append((acct["email"], folder, full))
    server._draft_live = lambda acct, folder, uid: False
    try:
        r, code = _resume(pk)
    finally:
        server.ingest.sync_folder = real
    assert code == 409 and "changed since the list loaded" in r["error"], (code, r)
    c = store.conn()
    gone = store.get_message(c, pk) is None
    c.close()
    assert gone and synced == [(ACCT, "Drafts", True)], synced


def test_unreachable_server_opens_the_local_copy():
    _fresh()
    pk, _ = _saved_and_synced(text="Mine.")
    server._draft_live = lambda acct, folder, uid: None
    r, code = _resume(pk)
    assert code == 200 and r["text"] == "Mine.", r


class _FakeIMAP:
    def __init__(self, uids, uidv=777, select="OK", boom=False):
        self.uids, self.untagged_responses, self._select, self.boom = uids, {"UIDVALIDITY": [str(uidv).encode()]}, select, boom
        self.calls = []

    def select(self, folder, readonly=False):
        self.calls.append(("select", folder, readonly))
        if self.boom:
            raise OSError("connection reset")
        return self._select, [b"3"]

    def uid(self, *args):
        self.calls.append(("uid",) + args)
        want = args[-1]
        return "OK", [b" ".join(u.encode() for u in self.uids if u == want)]


def test_draft_live_reads_uidvalidity_and_uid():
    _fresh()
    c = store.conn()
    store.set_uidvalidity(c, ACCT, "Drafts", 777)
    c.commit()
    c.close()
    acct = {"email": ACCT}
    real = pool.borrow
    try:
        def check(fake, uid=42):
            pool.borrow = contextlib.contextmanager(lambda a: (yield fake))
            return _real_draft_live(acct, "Drafts", uid)
        fake = _FakeIMAP(["42"])
        assert check(fake) is True
        assert fake.calls[0] == ("select", '"Drafts"', True) and fake.calls[1] == ("uid", "SEARCH", None, "UID", "42")
        assert check(_FakeIMAP(["41"])) is False
        assert check(_FakeIMAP(["42"], uidv=888)) is False                      # UIDs rolled
        assert check(_FakeIMAP(["42"], select="NO")) is None
        assert check(_FakeIMAP(["42"], boom=True)) is None
        assert check(_FakeIMAP(["42"]), uid=None) is None
    finally:
        pool.borrow = real


if __name__ == "__main__":
    import sys
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print("PASS", fn.__name__)
        except AssertionError as e:
            failed += 1
            print("FAIL", fn.__name__, repr(e))
        except Exception as e:
            failed += 1
            print("ER:U", fn.__name__, repr(e))
    print("\n%d/%d passed" % (len(fns) - failed, len(fns)))
    sys.exit(1 if failed else 0)
