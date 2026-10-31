"""Reopening a saved draft (compose's resume helpers): a reply or forward draft already holds its
quote or forwarded section, so it must come back split into your text and that tail, and go out
again with each part once. Hermetic — temp database and config, and every IMAP or SMTP call is
replaced with a recorder, so nothing can leave. All addresses are fictional.
Run: python3 test_draft_resume.py"""
import json
import os
import tempfile

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
import server

ACCT = "ada@example.test"
ORIG = {"from_name": "Bo Park", "from_addr": "bo@example.test", "date": "Mon, 2 Feb 2026 09:00:00 +0000",
        "subject": "Plans", "to": ACCT, "cc": "cy@example.test",
        "text": "Shall we meet Saturday?\nThe map is attached.", "html": "<p>Shall we meet <b>Saturday</b>?</p>"}
OWN = "Saturday works.\n\n-- \nAda"
_uid = [100]
CALLS = {}


def _fresh(uidv=777):
    """A new temp database, and recorders in place of every IMAP call a draft save makes."""
    store.DB = os.path.join(tempfile.mkdtemp(), "mailmind.db")
    store.init()
    CALLS.clear()
    CALLS.update(append=[], delete=[])

    def append(acct, raw, prev_uid=None, prev_uidvalidity=None, prev_msgid=None):
        _uid[0] += 1
        CALLS["append"].append({"account": acct["email"], "raw": raw, "prev_uid": prev_uid,
                                "prev_uidvalidity": prev_uidvalidity, "prev_msgid": prev_msgid})
        return "Drafts", _uid[0], uidv

    def delete(acct, folder, uid, uidvalidity=None):
        CALLS["delete"].append((acct["email"], folder, uid, uidvalidity))
    server.smtpsend.append_to_drafts = append
    server.smtpsend.delete_draft_uid = delete


def _save(account=ACCT, draft_id=None, **kw):
    body = {"mode": "new", "account": account, "to": ["bo@example.test"], "subject": "Plans",
            "text": "Saturday works.", "draft_id": draft_id, **kw}
    res, code = server.do_draft(body)
    assert code == 200 and res["ok"], (code, res)
    return res


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
