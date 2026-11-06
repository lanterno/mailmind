"""The composer's "new to you" lookup (discovery.known and POST /api/contacts/known): who
counts as known, exact address matching, drafts never vouching, the thin-history flag, input
limits, the CSRF guard, and that it never waits on the IMAP lock. Hermetic — the database,
config and rules are temp files. All addresses are fictional.
Run: python3 test_contacts_known.py"""
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, ROOT)
import dataclasses
import email
import http.client
import json
import tempfile
import threading

from mailmind.bootstrap.mailbox import store
from mailmind.platform import settings
from mailmind.platform.workers import gates

_CONFIG = {"accounts": [{"email": "ada@example.test", "type": "imap", "host": "imap.example.test"}]}


def _home():
    """A new temp data directory holding only the config, made the app's for every module."""
    settings.configure(settings.Settings(home=tempfile.mkdtemp()))
    with open(settings.current().config, "w") as f:
        json.dump(_CONFIG, f)


_home()                                               # before anything opens a file
from mailmind.content import mime
from mailmind.bootstrap.compose import outbox
from mailmind.bootstrap import discovery
from mailmind.bootstrap.compose import book
from mailmind.bootstrap import web
from mailmind.bootstrap import old_door

ACCT = "ada@example.test"
_uid = [0]


def _fresh():
    _home()
    store.init()


def _msg(from_addr, recipients="", folder="INBOX", is_junk=0, draft=0, account=ACCT):
    _uid[0] += 1
    c = store.conn()
    cur = c.execute("INSERT INTO messages (account, kind, folder, uid, message_id, thread_id, ts, from_addr, "
                    "subject, recipients, is_junk, draft) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (account, "imap", folder, _uid[0], f"<m{_uid[0]}@example.test>", f"t{_uid[0]}",
                     1_800_000_000 + _uid[0], from_addr, "Saturday market", recipients, is_junk, draft))
    store.set_recipients(c, cur.lastrowid, mime.recipient_addrs(email.message_from_string(f"To: {recipients}\n\n")))
    c.commit()
    c.close()


def _sql(q, args=()):
    c = store.conn()
    c.execute(q, args)
    c.commit()
    c.close()


def _outbox(envelope, delivered):
    _uid[0] += 1
    c = store.conn()
    oid, _ = book.outbox_insert(c, {
        "account": ACCT, "idempotency_key": f"k{_uid[0]}", "message_id": f"<o{_uid[0]}@example.test>",
        "in_reply_to_pk": None, "envelope": json.dumps(envelope),
        "recipients": json.dumps({"to": envelope, "cc": [], "bcc": []}), "subject": "Plans",
        "raw": b"x", "next_attempt_at": 0, "draft_id": None, "created_at": "2026-10-01T09:00:00"})
    if delivered:
        outbox.QUEUE.mark_gate(c, oid)
    c.commit()
    c.close()


def _known(addrs, **kw):
    res, code = discovery.known.ask(addrs, **kw)
    assert code == 200, (code, res)
    return {k: v["known"] for k, v in res["items"].items()}


def test_received_non_junk_only():
    _fresh()
    _msg("lio@example.test")
    _msg("spoof@example.test", is_junk=1)
    k = _known(["lio@example.test", "spoof@example.test"])
    assert k == {"lio@example.test": True, "spoof@example.test": False}, k


def test_provider_spam_folders_never_vouch():
    _fresh()
    _msg("jordan@lookalike.example", folder="Spam")              # classifier said personal
    _msg("bulk@lookalike.example", folder="INBOX.Bulk Mail")
    _msg(ACCT, "victim@other.example", folder="[Gmail]/Spam")     # spam forging your From:
    _msg(ACCT, "mark@other.example", folder="Sent", is_junk=1)
    _msg("ok@example.test", folder="Spammers club")              # a folder merely named like it
    k = _known(["jordan@lookalike.example", "bulk@lookalike.example", "victim@other.example",
                "mark@other.example", "ok@example.test"])
    assert k == {"jordan@lookalike.example": False, "bulk@lookalike.example": False,
                 "victim@other.example": False, "mark@other.example": False,
                 "ok@example.test": True}, k


def test_own_sent_rows_count_every_recipient():
    _fresh()
    _msg(ACCT, 'Lio Park <lio@example.test>, "Smith, Cy" <cy@example.test>, kim@example.test, '
               'mo@example.test', folder="Sent")
    k = _known(["lio@example.test", "cy@example.test", "kim@example.test", "mo@example.test"])
    assert all(k.values()), k


def test_exact_tokens_not_substrings():
    _fresh()
    _msg(ACCT, "joann@example.test, ann@example.test.au", folder="Sent")
    _msg(ACCT, '"ann@example.test via List" <list@example.test>', folder="Sent")
    k = _known(["ann@example.test", "list@example.test"])
    assert k == {"ann@example.test": False, "list@example.test": True}, k


def test_drafts_never_vouch():
    _fresh()
    _msg(ACCT, "d1@example.test", folder="Sent", draft=1)
    _msg(ACCT, "d2@example.test", folder="Drafts")
    _msg(ACCT, "d3@example.test", folder="Work in progress")
    _sql("INSERT INTO drafts (account, folder) VALUES (?, ?)", (ACCT, "Work in progress"))
    k = _known(["d1@example.test", "d2@example.test", "d3@example.test"])
    assert not any(k.values()), k


def test_send_as_identity_in_a_synced_sent_folder():
    _fresh()
    _sql("INSERT INTO sync_state (account, folder) VALUES (?, ?)", (ACCT, "INBOX.Sent"))
    _sql("INSERT INTO sync_state (account, folder) VALUES (?, ?)", (ACCT, "Consent"))
    _msg("alias@example.test", "pat@example.test", folder="INBOX.Sent")
    _msg("stranger@example.test", "quin@example.test", folder="Consent")
    k = _known(["pat@example.test", "quin@example.test"])
    assert k == {"pat@example.test": True, "quin@example.test": False}, k


def test_outbox_counts_only_delivered():
    _fresh()
    _outbox(["to@example.test", "hidden@example.test"], delivered=True)
    _outbox(["typo@example.test"], delivered=False)
    k = _known(["hidden@example.test", "typo@example.test"])
    assert k == {"hidden@example.test": True, "typo@example.test": False}, k


def test_idna_forms_match_both_ways():
    _fresh()
    _outbox(["ann@xn--bcher-kva.example"], delivered=True)
    _msg("bo@bücher.example")
    k = _known(["ann@bücher.example", "bo@xn--bcher-kva.example"])
    assert k == {"ann@bücher.example": True, "bo@xn--bcher-kva.example": True}, k


def test_keep_senders_count_keep_domains_do_not():
    _fresh()
    with open(settings.current().rules, "w") as f:
        json.dump({"keep_senders": ["vip@example.test"], "keep_domains": ["friends.example"]}, f)
    k = _known(["vip@example.test", "someone@friends.example"])
    assert k == {"vip@example.test": True, "someone@friends.example": False}, k


def test_own_addresses_are_self():
    _fresh()
    res, _ = discovery.known.ask(["ADA@example.test", "lio@example.test"])
    assert res["items"]["ada@example.test"] == {"known": True, "self": True}, res
    assert res["items"]["lio@example.test"] == {"known": False, "self": False}, res
    res, _ = discovery.known.ask(["lio@example.test", "ada@example.test"], own=["Lio@example.test"])
    assert res["items"]["lio@example.test"]["self"] is True, res
    assert res["items"]["ada@example.test"]["self"] is False, res


def test_input_shape_and_limits():
    _fresh()
    assert discovery.known.ask("lio@example.test")[1] == 400
    assert discovery.known.ask(None)[1] == 400
    over = [f"p{i}@example.test" for i in range(101)]
    res, code = discovery.known.ask(over)
    assert code == 400 and res["error"] == "too many addresses", res
    _msg("lio@example.test")
    res, code = discovery.known.ask(["nobody", "a@b@c", "Lio <LIO@Example.test>",
                                       "LIO@example.test", "lio@example.test"])
    assert code == 200 and res["bad"] == ["nobody", "a@b@c"], res
    assert res["items"]["lio <lio@example.test>"]["known"] is True, res
    assert list(res["items"]) == ["lio <lio@example.test>", "lio@example.test"], res


def test_thin_history_flag():
    _fresh()
    for i in range(2):
        _msg(f"p{i}@example.test")
    _msg("junk@example.test", is_junk=1)
    res, _ = discovery.known.ask(["p0@example.test"], min_history=3)
    assert res["thin"] is True, res
    _msg("p2@example.test")
    res, _ = discovery.known.ask(["p0@example.test"], min_history=3)
    assert res["thin"] is False, res


def test_the_delivered_address_is_judged_not_the_display_name():
    _fresh()
    _msg("bob@company.example")
    _msg("ann@example.test")
    res, _ = discovery.known.ask(['"bob@company.example" <bob@evil.example>',
                                    "Bob (bob@company.example) <bob@evil.example>",
                                    "'ann@example.test", ".ann@example.test"])
    items = {k: v["known"] for k, v in res["items"].items()}
    assert items == {'"bob@company.example" <bob@evil.example>': False,
                     "bob (bob@company.example) <bob@evil.example>": False,
                     "'ann@example.test": False, ".ann@example.test": False}, res
    res, _ = discovery.known.ask(["a@example.test, b@example.test"])
    assert res["bad"] == ["a@example.test, b@example.test"] and not res["items"], res


def test_items_carry_only_known_and_self():
    _fresh()
    _msg("lio@example.test")
    res, _ = discovery.known.ask(["lio@example.test", "new@example.test"])
    assert set(res) == {"ok", "items", "bad", "thin"}, res
    assert all(set(v) == {"known", "self"} for v in res["items"].values()), res


def test_route_requires_csrf():
    _fresh()
    _msg("lio@example.test")
    srv = old_door._Server(("127.0.0.1", 0), web.H)
    port = srv.server_address[1]
    before = settings.current()
    settings.configure(dataclasses.replace(before, port=port))
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        def post(headers):
            h = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            h.request("POST", "/api/contacts/known", json.dumps({"addrs": ["lio@example.test"]}),
                      {"Content-Type": "application/json", **headers})
            r = h.getresponse()
            out = r.status, json.loads(r.read() or b"{}")
            h.close()
            return out
        origin = {"Origin": f"http://127.0.0.1:{port}", "Sec-Fetch-Site": "same-origin"}
        code, _ = post(origin)
        assert code == 403, code
        code, body = post({**origin, "X-CSRF-Token": web.H.CSRF})
        assert code == 200 and body["items"]["lio@example.test"]["known"] is True, (code, body)
    finally:
        srv.shutdown()
        srv.server_close()
        settings.configure(before)


def test_never_waits_on_the_imap_lock():
    _fresh()
    done, holding, release = [], threading.Event(), threading.Event()
    holder = threading.Thread(target=lambda: gates.run(gates.EVERY_ACCOUNT, lambda: (holding.set(), release.wait(10)),
                                                       priority=gates.Priority.INTERACTIVE), daemon=True)
    holder.start()                                       # a long mail operation holds the gate
    assert holding.wait(5)
    try:
        t = threading.Thread(target=lambda: done.append(discovery.known.ask(["lio@example.test"])),
                             daemon=True)
        t.start()
        t.join(5)
    finally:
        release.set()
        holder.join(5)
    assert done and done[0][1] == 200, done


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
