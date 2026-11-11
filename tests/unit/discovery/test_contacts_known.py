"""The composer's "new to you" lookup (discovery.known and POST /api/contacts/known): who counts as
known, exact address matching, drafts never vouching, the thin-history flag, input limits, the CSRF
guard, and that it never waits on the IMAP lock. All addresses are fictional."""
import email
import itertools
import json
import threading

import pytest

from gate import held_elsewhere
from answered import answered
from mailmind.bootstrap import discovery
from mailmind.bootstrap.compose import book
from mailmind.bootstrap import compose
from mailmind.bootstrap.mailbox import store
from mailmind.content import mime
from mailmind.platform import settings
from stored_rows import put_document

ACCT = "ada@example.test"
CONFIG = {"accounts": [{"email": ACCT, "type": "imap", "host": "imap.example.test"}]}
_uid = itertools.count(1000)     # past the UIDs the world holds


@pytest.fixture
def home(data_dir):
    """A data directory holding the schema and Ada's config, and no mail yet."""
    with open(settings.current().config, "w") as f:
        json.dump(CONFIG, f)
    return data_dir


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


def _sql(q, args=()):
    c = store.conn()
    c.execute(q, args)
    c.commit()
    c.close()


def _outbox(envelope, delivered):
    uid = next(_uid)
    c = store.conn()
    oid, _ = book.outbox_insert(c, {
        "account": ACCT, "idempotency_key": f"k{uid}", "message_id": f"<o{uid}@example.test>",
        "envelope": json.dumps(envelope),
        "recipients": json.dumps({"to": envelope, "cc": [], "bcc": []}), "subject": "Plans",
        "raw": b"x", "next_attempt_at": 0, "draft_id": None, "created_at": "2026-10-01T09:00:00"})
    if delivered:
        compose.outbox.QUEUE.mark_gate(c, oid)
    c.commit()
    c.close()


def _known(addrs, **kw):
    res = discovery.known.ask(addrs, **kw)
    return {k: v["known"] for k, v in res["items"].items()}


def test_received_non_junk_only(home):
    _msg("lio@example.test")
    _msg("spoof@example.test", is_junk=1)
    assert _known(["lio@example.test", "spoof@example.test"]) == {"lio@example.test": True, "spoof@example.test": False}


def test_provider_spam_folders_never_vouch(home):
    _msg("jordan@lookalike.example", folder="Spam")              # classifier said personal
    _msg("bulk@lookalike.example", folder="INBOX.Bulk Mail")
    _msg(ACCT, "victim@other.example", folder="[Gmail]/Spam")     # spam forging your From:
    _msg(ACCT, "mark@other.example", folder="Sent", is_junk=1)
    _msg("ok@example.test", folder="Spammers club")              # a folder merely named like it
    k = _known(["jordan@lookalike.example", "bulk@lookalike.example", "victim@other.example",
                "mark@other.example", "ok@example.test"])
    assert k == {"jordan@lookalike.example": False, "bulk@lookalike.example": False,
                 "victim@other.example": False, "mark@other.example": False,
                 "ok@example.test": True}


def test_own_sent_rows_count_every_recipient(home):
    _msg(ACCT, 'Lio Park <lio@example.test>, "Smith, Cy" <cy@example.test>, kim@example.test, '
               'mo@example.test', folder="Sent")
    k = _known(["lio@example.test", "cy@example.test", "kim@example.test", "mo@example.test"])
    assert all(k.values()), k


def test_exact_tokens_not_substrings(home):
    _msg(ACCT, "joann@example.test, ann@example.test.au", folder="Sent")
    _msg(ACCT, '"ann@example.test via List" <list@example.test>', folder="Sent")
    assert _known(["ann@example.test", "list@example.test"]) == {"ann@example.test": False, "list@example.test": True}


def test_drafts_never_vouch(home):
    _msg(ACCT, "d1@example.test", folder="Sent", draft=1)
    _msg(ACCT, "d2@example.test", folder="Drafts")
    _msg(ACCT, "d3@example.test", folder="Work in progress")
    _sql("INSERT INTO drafts (account, folder) VALUES (?, ?)", (ACCT, "Work in progress"))
    k = _known(["d1@example.test", "d2@example.test", "d3@example.test"])
    assert not any(k.values()), k


def test_send_as_identity_in_a_synced_sent_folder(home):
    _sql("INSERT INTO sync_state (account, folder) VALUES (?, ?)", (ACCT, "INBOX.Sent"))
    _sql("INSERT INTO sync_state (account, folder) VALUES (?, ?)", (ACCT, "Consent"))
    _msg("alias@example.test", "pat@example.test", folder="INBOX.Sent")
    _msg("stranger@example.test", "quin@example.test", folder="Consent")
    assert _known(["pat@example.test", "quin@example.test"]) == {"pat@example.test": True, "quin@example.test": False}


def test_outbox_counts_only_delivered(home):
    _outbox(["to@example.test", "hidden@example.test"], delivered=True)
    _outbox(["typo@example.test"], delivered=False)
    assert _known(["hidden@example.test", "typo@example.test"]) == {"hidden@example.test": True, "typo@example.test": False}


def test_idna_forms_match_both_ways(home):
    _outbox(["ann@xn--bcher-kva.example"], delivered=True)
    _msg("bo@bücher.example")
    k = _known(["ann@bücher.example", "bo@xn--bcher-kva.example"])
    assert k == {"ann@bücher.example": True, "bo@xn--bcher-kva.example": True}


def test_keep_senders_count_keep_domains_do_not(home):
    put_document("rules", json.dumps({"keep_senders": ["vip@example.test"], "keep_domains": ["friends.example"]}))
    assert _known(["vip@example.test", "someone@friends.example"]) == {"vip@example.test": True, "someone@friends.example": False}


def test_own_addresses_are_self(home):
    res = discovery.known.ask(["ADA@example.test", "lio@example.test"])
    assert res["items"]["ada@example.test"] == {"known": True, "self": True}, res
    assert res["items"]["lio@example.test"] == {"known": False, "self": False}, res
    res = discovery.known.ask(["lio@example.test", "ada@example.test"], own=["Lio@example.test"])
    assert res["items"]["lio@example.test"]["self"] is True, res
    assert res["items"]["ada@example.test"]["self"] is False, res


def test_a_plus_address_of_yours_is_yours(home):
    res = discovery.known.ask(["Ada+News@example.test", "ada.news@example.test", "lio+ada@example.test"])
    assert res["items"]["ada+news@example.test"] == {"known": True, "self": True}, res
    assert res["items"]["ada.news@example.test"]["self"] is False, res
    assert res["items"]["lio+ada@example.test"]["self"] is False, res


def test_a_tagged_account_owns_its_plain_mailbox():
    from mailmind.discovery.domain import known
    own = known.own_addresses(["Ada+Work@example.test"], [], [])
    assert known.is_own("ada@example.test", own) and known.is_own("ada+other@example.test", own)
    assert known.untagged("+only@example.test") == "+only@example.test"
    assert known.untagged("nobody") == "nobody"


def test_an_address_you_have_sent_from_is_yours(home):
    _msg("ada@alias.example.test", "pat@example.test", folder="Sent")
    _msg("mira@example.test", "pat@example.test")                # received: known, but no address of yours
    res = discovery.known.ask(["ada@alias.example.test", "mira@example.test"])
    assert res["items"]["ada@alias.example.test"] == {"known": True, "self": True}, res
    assert res["items"]["mira@example.test"] == {"known": True, "self": False}, res


def test_mail_sent_from_a_tag_of_yours_vouches_for_who_it_went_to(home):
    _msg("ada+work@example.test", "pat@example.test", folder="Archive")
    assert _known(["pat@example.test"]) == {"pat@example.test": True}


@pytest.mark.parametrize("addrs", ["lio@example.test", None])
def test_input_that_is_no_list_is_refused(addrs, home):
    assert answered(discovery.known.ask, addrs)[1] == 400


def test_input_shape_and_limits(home):
    res, code = answered(discovery.known.ask, [f"p{i}@example.test" for i in range(101)])
    assert code == 400 and res["detail"] == "too many addresses", res
    _msg("lio@example.test")
    res, code = answered(discovery.known.ask, ["nobody", "a@b@c", "Lio <LIO@Example.test>",
                                     "LIO@example.test", "lio@example.test"])
    assert code == 200 and res["bad"] == ["nobody", "a@b@c"], res
    assert res["items"]["lio <lio@example.test>"]["known"] is True, res
    assert list(res["items"]) == ["lio <lio@example.test>", "lio@example.test"], res


def test_thin_history_flag(home):
    for i in range(2):
        _msg(f"p{i}@example.test")
    _msg("junk@example.test", is_junk=1)
    res = discovery.known.ask(["p0@example.test"], min_history=3)
    assert res["thin"] is True, res
    _msg("p2@example.test")
    res = discovery.known.ask(["p0@example.test"], min_history=3)
    assert res["thin"] is False, res


def test_the_delivered_address_is_judged_not_the_display_name(home):
    _msg("bob@company.example")
    _msg("ann@example.test")
    res = discovery.known.ask(['"bob@company.example" <bob@evil.example>',
                                  "Bob (bob@company.example) <bob@evil.example>",
                                  "'ann@example.test", ".ann@example.test"])
    assert {k: v["known"] for k, v in res["items"].items()} == {
        '"bob@company.example" <bob@evil.example>': False,
        "bob (bob@company.example) <bob@evil.example>": False,
        "'ann@example.test": False, ".ann@example.test": False}, res
    res = discovery.known.ask(["a@example.test, b@example.test"])
    assert res["bad"] == ["a@example.test, b@example.test"] and not res["items"], res


def test_items_carry_only_known_and_self(home):
    _msg("lio@example.test")
    res = discovery.known.ask(["lio@example.test", "new@example.test"])
    assert set(res) == {"ok", "items", "bad", "thin"}, res
    assert all(set(v) == {"known", "self"} for v in res["items"].values()), res


# ── a new address that reads as someone you've written to, mistyped ──────────────────────────────────────────────────────

SLIPS = [
    ("jon@example.test", "john@example.test"),              # a letter dropped
    ("johhn@example.test", "john@example.test"),            # a letter doubled
    ("joan@example.test", "john@example.test"),             # a letter changed
    ("jonh@example.test", "john@example.test"),             # two neighbours swapped
    ("john@exmaple.test", "john@example.test"),             # the same, in the domain
    ("john@example.tes", "john@example.test"),
    ("bo@exmaple.test", "bo@example.test"),                 # a short name is safe to read past when the domain slips
]


@pytest.mark.parametrize("typed,meant", SLIPS, ids=[t for t, _ in SLIPS])
def test_a_new_address_one_slip_from_someone_you_write_to_says_who(home, typed, meant):
    _msg(ACCT, meant, folder="Sent")
    assert discovery.known.ask([typed])["items"][typed] == {"known": False, "self": False, "near": meant}


NOT_SLIPS = [
    ("two slips apart", "jhn@exampel.test", "john@example.test"),
    ("two edits in the name", "johnny@example.test", "john@example.test"),
    ("the name and the domain both", "jon@exmaple.test", "john@example.test"),
    ("too short to tell apart", "ed@example.test", "ad@example.test"),
    ("numbered siblings", "room4@example.test", "room3@example.test"),
    ("another tag of the same mailbox", "john+a@example.test", "john+b@example.test"),
    ("the mailbox a tag names", "john+a@example.test", "john@example.test"),
]


@pytest.mark.parametrize("typed,person", [c[1:] for c in NOT_SLIPS], ids=[c[0] for c in NOT_SLIPS])
def test_what_is_no_slip(home, typed, person):
    _msg(ACCT, person, folder="Sent")
    assert "near" not in discovery.known.ask([typed])["items"][typed]


def test_a_slip_of_someone_youve_only_heard_from_says_nothing(home):
    _msg("john@example.test")                                # they wrote to you; you never wrote to them
    _msg("mira@example.test", "kate@example.test")           # nor does someone else's mail to a third person count
    res = discovery.known.ask(["jon@example.test", "kat@example.test"])
    assert all("near" not in v for v in res["items"].values()), res


def test_an_address_you_already_know_is_never_a_slip(home):
    _msg(ACCT, "john@example.test", folder="Sent")
    _msg("jon@example.test")                                 # one edit from John, but a real correspondent
    assert discovery.known.ask(["jon@example.test"])["items"]["jon@example.test"] == {"known": True, "self": False}


def test_your_own_address_is_no_one_to_be_a_slip_of(home):
    _msg(ACCT, ACCT, folder="Sent")
    assert "near" not in discovery.known.ask(["adam@example.test"])["items"]["adam@example.test"]


def test_the_outbox_counts_as_writing_to_someone_but_only_once_delivered(home):
    _outbox(["john@example.test"], delivered=True)
    _outbox(["kate@example.test"], delivered=False)
    res = discovery.known.ask(["jon@example.test", "kat@example.test"])
    assert res["items"]["jon@example.test"]["near"] == "john@example.test", res
    assert "near" not in res["items"]["kat@example.test"], res


def test_drafts_and_spam_arent_writing_to_someone(home):
    _msg(ACCT, "john@example.test", folder="Drafts")
    _msg(ACCT, "kate@example.test", folder="Sent", is_junk=1)
    res = discovery.known.ask(["jon@example.test", "kat@example.test"])
    assert all("near" not in v for v in res["items"].values()), res


def test_the_person_written_to_most_is_the_one_named(home):
    _msg(ACCT, "joan@example.test", folder="Sent")
    _msg(ACCT, "john@example.test", folder="Sent")
    _msg(ACCT, "john@example.test", folder="Archive")
    assert discovery.known.ask(["jon@example.test"])["items"]["jon@example.test"]["near"] == "john@example.test"


def test_nothing_is_read_of_who_you_write_to_for_someone_known(home, monkeypatch):
    from mailmind.bootstrap.discovery import _mail
    _msg("lio@example.test")
    calls = []
    monkeypatch.setattr(_mail, "written_copies", lambda *a: calls.append(a) or [])
    discovery.known.ask(["lio@example.test", ACCT])
    assert calls == []
    discovery.known.ask(["new@example.test"])
    assert len(calls) == 1


def test_never_waits_on_the_imap_lock(home):
    done = []
    with held_elsewhere():                               # a long mail operation holds the gate
        t = threading.Thread(target=lambda: done.append(discovery.known.ask(["lio@example.test"])), daemon=True)
        t.start()
        t.join(5)
        assert done and done[0]["ok"], done               # while the gate is still held
