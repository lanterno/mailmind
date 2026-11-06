"""The domain layer's hard-won fixes, pinned before the refactor moves them: the store's SQL and
text helpers, the migrations that carry data, threading's sticky ids, search ranking, the
classifier's precedence and its rules file, the Screener, saved views, parsing and composing, and
calendar recurrence. Each test names one behaviour a rewrite could silently lose.

Where today's behaviour is a known bug, the test states the right behaviour and is marked
xfail(strict=True): fixing the bug makes it pass, and strict makes that an error until the marker
is removed. Most tests need only a temporary database (data_dir); a few use the fictional world.
Every address is made up.
"""
import hashlib
import itertools
import json
import os
from datetime import datetime, timedelta
from email.message import EmailMessage

import pytest

from fixtures import world as w
from mailmind.content.text import body_text_of, html_to_snippet_text, snippet_of, text_body

A = "ada@example.test"
NOW_TS = 1_800_000_000
DAY = 86400
_uid = itertools.count(1)


@pytest.fixture
def db(data_dir):
    import store
    c = store.conn()
    yield c
    c.close()


def put(c, mid="", *, account=A, folder="INBOX", irt="", refs="", gm="", subject="Hello", ts=0,
        snippet="", bucket="other/personal-ish", is_junk=0, **cols):
    """Store one message as ingest does, then set any further columns (thread_id, answered, ...)."""
    import store
    pk = store.upsert_message(c, {"account": account, "kind": "gmail" if gm else "imap", "folder": folder,
                                  "uid": next(_uid), "message_id": mid, "gmail_thread": gm or None,
                                  "from_addr": "lin@example.test", "from_name": "Lin Mori", "subject": subject,
                                  "date_iso": None, "ts": ts, "snippet": snippet, "bucket": bucket,
                                  "is_junk": is_junk})
    cols = {"in_reply_to": irt, "refs": refs, **cols}
    c.execute(f"UPDATE messages SET {', '.join(k + '=?' for k in cols)} WHERE pk=?", [*cols.values(), pk])
    store.fts_index(c, pk)
    return pk


def recompute(c, account=A):
    import threads
    threads.recompute_threads(c, account)


def tid(c, pk):
    return c.execute("SELECT thread_id FROM messages WHERE pk=?", (pk,)).fetchone()["thread_id"]


def col(c, pk, name):
    return c.execute(f"SELECT {name} FROM messages WHERE pk=?", (pk,)).fetchone()[0]


def hits(c, match):
    return sorted(r[0] for r in c.execute("SELECT rowid FROM msg_fts WHERE msg_fts MATCH ?", (match,)))


def rows(sql, *args):
    import store
    c = store.conn()
    try:
        return [dict(r) for r in c.execute(sql, args)]
    finally:
        c.close()


# -- migrations that carry data -----------------------------------------------

def _db_at(monkeypatch, data_dir, version, name):
    """A new database made by init() with only the first `version` migrations, in a data directory
    of its own that the app then uses (data_dir puts its own back after the test)."""
    from mailmind.platform import settings
    from mailmind.platform.db import migrate, migrations
    home = data_dir / name
    home.mkdir()
    settings.configure(settings.Settings(home=str(home)))
    migrate.init(settings.current().db, migrations.MIGRATIONS[:version])


REPLY = (b"From: Lin Mori <lin@example.test>\r\nTo: Noor Haddad <noor@studio.example.test>\r\n"
         b"Subject: Re: Quilt\r\nMessage-ID: <q2@example.test>\r\nIn-Reply-To: <q1@example.test>\r\n\r\n"
         b"The pattern is ready.\r\n")


def test_hwf_migrations_5_and_6_thread_and_index_mail_stored_before_them(data_dir, monkeypatch):
    import store
    _db_at(monkeypatch, data_dir, 4, "v4.db")
    c = store.conn()
    ins = ("INSERT INTO messages(account, kind, folder, uid, message_id, in_reply_to, subject, from_addr) "
           "VALUES (?,?,?,?,?,?,?,?)")
    root = c.execute(ins, (A, "imap", "INBOX", 1, "<q1@example.test>", "", "Quilt", "noor@studio.example.test")).lastrowid
    reply = c.execute(ins, (A, "imap", "INBOX", 2, "<q2@example.test>", "<q1@example.test>", "Re: Quilt",
                            "lin@example.test")).lastrowid
    c.execute("INSERT INTO bodies(message_pk, raw, fetched_at) VALUES (?,?,?)", (reply, REPLY, "2026-11-01"))
    c.commit()
    c.close()
    store.init()                                         # migrations 5 to 20 run over the stored mail
    c = store.conn()
    try:
        assert tid(c, root) == tid(c, reply) and tid(c, root).startswith(f"rf:{A}:")
        assert "The pattern is ready." in col(c, reply, "body_text")
        assert "noor@studio.example.test" in col(c, reply, "recipients")
        assert hits(c, 'recipients : "noor"') == [reply]
        assert hits(c, '"pattern"') == [reply] and hits(c, '"quilt"') == sorted([root, reply])
    finally:
        c.close()


def test_hwf_migration_10_rebuilds_a_search_index_that_lacks_recipients(data_dir, monkeypatch):
    import store
    _db_at(monkeypatch, data_dir, 9, "v9.db")
    c = store.conn()
    c.executescript("DROP TABLE msg_fts; CREATE VIRTUAL TABLE msg_fts USING fts5("
                    "subject, from_name, from_addr, snippet, body_text, tokenize='unicode61 remove_diacritics 2');")
    pk = c.execute("INSERT INTO messages(account, kind, folder, uid, message_id, subject, recipients) "
                   "VALUES (?,?,?,?,?,?,?)", (A, "imap", "INBOX", 1, "<h@example.test>", "Heron notes",
                                              "Noor Haddad <noor@studio.example.test>")).lastrowid
    c.commit()
    c.close()
    store.init()
    c = store.conn()
    try:
        assert hits(c, 'recipients : "noor"') == [pk] and hits(c, '"heron"') == [pk]
    finally:
        c.close()


def test_hwf_migration_18_drops_the_retired_notes_table(data_dir, monkeypatch):
    import store
    _db_at(monkeypatch, data_dir, 17, "v17.db")
    c = store.conn()
    c.execute("CREATE TABLE notes (id INTEGER PRIMARY KEY, body TEXT)")
    c.commit()
    c.close()
    store.init()
    assert rows("SELECT name FROM sqlite_master WHERE name='notes'") == []


# -- the search index and the text it holds -----------------------------------

def test_hwf_every_delete_path_drops_the_search_row_summary_and_body(db):
    import store
    one, two, three = (put(db, f"<d{i}@example.test>", subject="Heron notes") for i in range(3))
    for pk in (one, two, three):
        store.save_summary(db, pk, "s", 3, "info", "", "m", "now")
        store.store_body(db, pk, b"Subject: Heron notes\r\n\r\nbody", "now")
    assert hits(db, '"heron"') == [one, two, three]
    store.delete_message(db, one)
    uid_three = col(db, three, "uid")
    store.reconcile(db, A, "INBOX", [uid_three], "now")          # prunes two, keeps three
    assert hits(db, '"heron"') == [three]
    store.reset_folder(db, A, "INBOX")
    assert hits(db, '"heron"') == []
    assert [db.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
            for t in ("msg_fts", "summaries", "bodies", "messages")] == [0, 0, 0, 0]


def test_hwf_search_index_helpers_tolerate_a_database_without_the_index(db):
    import store
    db.execute("DROP TABLE msg_fts")
    pk = put(db, "<x@example.test>")                      # upsert indexes; a missing index is skipped
    store.fts_set_body(db, pk, "text")
    store.delete_message(db, pk)
    assert db.execute("SELECT count(*) FROM messages").fetchone()[0] == 0


def test_hwf_snippet_unescapes_entities_then_drops_invisible_padding():
    padding = "&zwnj;&nbsp;&#8203;\u200d\u2060\ufeff\u034f\u00ad\u200e\u200f" * 5
    assert snippet_of("Doors open at nine &amp; close at six" + padding + "\n\nSee   you there.") == \
        "Doors open at nine & close at six See you there."
    assert len(snippet_of("word " * 200)) == 280


HTML_WITH_NOISE = ("<html><head><title>Issue 12</title><style>.ReadMsgBody{width:100%}</style></head>"
                   "<body><script>var tracking = 1;</script><!-- a hidden note --><p>Hello &amp; welcome</p>"
                   "</body></html>")


def test_hwf_body_text_drops_the_contents_of_script_style_head_and_title():
    for text in (body_text_of({"text": None, "html": HTML_WITH_NOISE}),
                 html_to_snippet_text(HTML_WITH_NOISE)):
        assert "Hello & welcome" in text
        assert not any(s in text for s in ("ReadMsgBody", "tracking", "Issue 12", "hidden note")), text
    assert body_text_of({"text": "plain wins", "html": HTML_WITH_NOISE}) == "plain wins"


def test_hwf_body_text_strips_json_ld_blocks_twelve_at_most():
    block = '[{"@context": "https://schema.org", "@type": "Order", "items": [{"n": 1}]}]'
    assert body_text_of({"text": f"Order shipped. {block} Thanks."}).split() == ["Order", "shipped.", "Thanks."]
    many = body_text_of({"text": " ".join([block] * 13)})
    assert many.count("@context") == 1


def test_hwf_body_text_is_capped_at_100k_characters():
    assert len(body_text_of({"text": "x" * 200_000})) == 100_000


def test_hwf_a_fetched_body_fills_only_an_empty_snippet(db):
    import store
    kept = put(db, "<k@example.test>", snippet="what ingest saw")
    empty = put(db, "<e@example.test>")
    for pk in (kept, empty):
        store.fts_set_body(db, pk, "The heron   came back &amp; stayed.")
    assert col(db, kept, "snippet") == "what ingest saw"
    assert col(db, empty, "snippet") == "The heron came back & stayed."
    assert col(db, kept, "body_text") == "The heron   came back &amp; stayed."
    assert hits(db, '"heron"') == [kept, empty]


def test_hwf_a_full_rescan_keeps_the_snippet_of_an_opened_message(world):
    import accounts
    import ingest
    pk = world.pk("ada-sent-01-lunch-reply.eml")
    ingest.fetch_body(pk)
    before = world.row("ada-sent-01-lunch-reply.eml")["snippet"]
    assert before.startswith("Thursday works.")
    ingest.sync_folder(accounts.get(w.ADA), "Sent", full=True)        # as a draft-409 resync does to Drafts
    assert world.row("ada-sent-01-lunch-reply.eml")["snippet"] == before


def test_hwf_an_inbox_snippet_drops_invisible_preheader_padding(world):
    raw = (b"From: Noor Haddad <noor@studio.example.test>\r\nTo: ada@example.test\r\nSubject: Studio hours\r\n"
           b"Date: Mon, 02 Nov 2026 09:00:00 +0000\r\nMessage-ID: <hours-1@studio.example.test>\r\n"
           b"MIME-Version: 1.0\r\nContent-Type: text/html; charset=utf-8\r\n\r\n"
           b"<div>Doors open at nine" + b"&zwnj;&nbsp;" * 30 + b"</div><p>See you there.</p>\r\n")
    world.deliver(w.ADA, "INBOX", raw)
    [row] = rows("SELECT snippet FROM messages WHERE message_id='<hours-1@studio.example.test>'")
    assert row["snippet"].startswith("Doors open at nine")
    assert "\u200c" not in row["snippet"]


def test_hwf_an_upsert_of_a_stored_uid_keeps_its_sender_date_and_ids(db):
    import store
    first = {"account": A, "kind": "gmail", "folder": "INBOX", "uid": 7, "message_id": "<one@example.test>",
             "gmail_thread": "1781", "from_addr": "lin@example.test", "from_name": "Lin Mori", "subject": "Old",
             "date_iso": "2026-10-01T09:00:00+00:00", "ts": 100, "snippet": "old", "bucket": "other/personal-ish",
             "confidence": "low", "reason": "r1", "is_junk": 0}
    pk = store.upsert_message(db, first)
    again = store.upsert_message(db, {**first, "message_id": "<two@example.test>", "gmail_thread": "1799",
                                      "from_addr": "x@example.test", "from_name": "X", "subject": "New",
                                      "date_iso": "2026-11-01T09:00:00+00:00", "ts": 200, "snippet": "new",
                                      "bucket": "blocked", "confidence": "high", "reason": "r2", "is_junk": 1})
    row = dict(store.get_message(db, pk))
    assert again == pk
    assert {k: row[k] for k in ("message_id", "gmail_thread", "from_addr", "from_name", "date_iso", "ts")} == {
        "message_id": "<one@example.test>", "gmail_thread": "1781", "from_addr": "lin@example.test",
        "from_name": "Lin Mori", "date_iso": "2026-10-01T09:00:00+00:00", "ts": 100}
    assert {k: row[k] for k in ("subject", "snippet", "bucket", "confidence", "reason", "is_junk")} == {
        "subject": "New", "snippet": "new", "bucket": "blocked", "confidence": "high", "reason": "r2", "is_junk": 1}


# -- mute, Reply Later and Waiting: badges and lists --------------------------

def test_hwf_badges_count_only_threads_that_still_have_mail(db):
    import store
    put(db, "<a@example.test>", thread_id="T1")
    put(db, "<b@example.test>", thread_id="T2")
    put(db, "<c@example.test>", thread_id="T3")
    for t in ("T1", "GONE1"):
        store.mute_thread(db, A, t, "now")
    for t in ("T2", "GONE2"):
        store.set_replylater(db, A, t, None, "now")
    db.executemany("INSERT INTO followups(account, thread_id, kind, state, due_ts) VALUES (?,?,?,?,?)",
                   [(A, "T3", "sent", "due", 1), (A, "GONE3", "received", "due", 2)])
    assert (store.muted_count(db, A), len(store.list_muted(db, A))) == (1, 1)
    assert (store.replylater_count(db, A), len(store.list_replylater(db, A))) == (1, 1)
    assert (store.waiting_count(db, A), len(store.list_waiting(db, A))) == (1, 1)


def test_hwf_a_listed_thread_shows_its_latest_message_the_higher_pk_on_a_tie(db):
    import store
    put(db, "<1@example.test>", thread_id="T", ts=100)
    put(db, "<2@example.test>", thread_id="T", ts=200)
    latest = put(db, "<3@example.test>", thread_id="T", ts=200, folder="Sent")
    put(db, "<4@example.test>", thread_id="U", ts=300)
    waiting = put(db, "<5@example.test>", thread_id="U", ts=300)
    store.mute_thread(db, A, "T", "now")
    store.set_replylater(db, A, "T", None, "now")
    db.execute("INSERT INTO followups(account, thread_id, kind, state, due_ts) VALUES (?,?,?,?,?)",
               (A, "U", "sent", "due", 1))
    assert [(r["pk"], r["thread_count"]) for r in store.list_muted(db, A)] == [(latest, 3)]
    assert [(r["pk"], r["thread_count"]) for r in store.list_replylater(db, A)] == [(latest, 3)]
    assert [(r["pk"], r["thread_count"]) for r in store.list_waiting(db, A)] == [(waiting, 2)]


def test_hwf_a_dismissed_reply_later_thread_leaves_the_stack(db):
    import store
    put(db, "<a@example.test>", thread_id="T")
    store.set_replylater(db, A, "T", None, "now")
    store.dismiss_followup(db, A, "T", "now")          # what POST /api/followups/dismiss does
    assert [r["thread_id"] for r in store.list_replylater(db, A)] == []
    assert store.replylater_count(db, A) == 0           # nor counted on the rail
    store.set_replylater(db, A, "T", None, "later")     # pinned again, it's back
    assert ([r["thread_id"] for r in store.list_replylater(db, A)], store.replylater_count(db, A)) == (["T"], 1)


def test_hwf_a_draft_of_a_reply_is_no_reply_to_reply_later(db):
    import store
    put(db, "<a@example.test>", thread_id="T", ts=100)
    put(db, "<b@example.test>", thread_id="T", ts=200, from_addr=A, draft=1)
    store.set_replylater(db, A, "T", None, "now")
    store.resolve_followups(db, A)
    assert store.replylater_count(db, A) == 1
    put(db, "<c@example.test>", thread_id="T", ts=300, from_addr=A, folder="Sent")
    store.resolve_followups(db, A)
    assert store.replylater_count(db, A) == 0


def test_hwf_a_draft_of_a_reply_leaves_a_thread_waiting_on_you(db):
    import store
    day = 86400
    asked = put(db, "<a@example.test>", thread_id="T", ts=NOW_TS - 5 * day, seen=1)
    put(db, "<b@example.test>", thread_id="T", ts=NOW_TS - day, from_addr=A, draft=1)
    db.execute("INSERT INTO summaries(message_pk, importance, category) VALUES (?, 5, 'action-needed')", (asked,))

    def nudged():
        store.followup_detect_received(db, NOW_TS, 3, 14)
        store.resolve_followups(db, A)
        return [(r["anchor_pk"], r["last_msg_ts"])
                for r in db.execute("SELECT anchor_pk, last_msg_ts FROM followups WHERE kind='received'")]

    assert nudged() == [(asked, NOW_TS - 5 * day)]     # dated by the mail, not by the draft
    put(db, "<c@example.test>", thread_id="T", ts=NOW_TS - 1, from_addr=A, folder="Sent")
    assert nudged() == []


# -- the draft-ahead cache's hygiene ------------------------------------------

def _ahead(c, ref, created, body="Sounds good."):
    import store
    store.draft_ahead_put(c, A, ref, "fp", body, "warm", "m", None, None, "dwell", created)


def _ahead_keys(c):
    return sorted(r[0] for r in c.execute("SELECT ref_key FROM draft_ahead"))


def test_hwf_draft_ahead_ages_out_after_a_week_unless_a_failure_is_recent(db):
    import store
    for t in "ABCD":
        put(db, f"<{t}@example.test>", thread_id=t)
    _ahead(db, "t:A", NOW_TS - 8 * DAY)
    _ahead(db, "t:B", NOW_TS - 8 * DAY)
    store.draft_ahead_fail(db, A, "t:B", NOW_TS - DAY)       # a failed regeneration keeps the row as backoff
    store.draft_ahead_fail(db, A, "t:C", NOW_TS - DAY)       # failure-only rows persist the backoff
    store.draft_ahead_fail(db, A, "t:D", NOW_TS - 8 * DAY)
    store.draft_ahead_gc(db, NOW_TS)
    assert _ahead_keys(db) == ["t:B", "t:C"]


def test_hwf_draft_ahead_sweeps_orphans_only_when_asked_and_after_an_hour(db):
    import store
    _ahead(db, "t:gone", NOW_TS - 2 * 3600)
    _ahead(db, "m:<gone@example.test>", NOW_TS - 2 * 3600)
    _ahead(db, "t:just-made", NOW_TS - 600)
    store.draft_ahead_gc(db, NOW_TS, orphans=False)          # the opportunistic call sites: mid-sync safe
    assert _ahead_keys(db) == ["m:<gone@example.test>", "t:gone", "t:just-made"]
    store.draft_ahead_gc(db, NOW_TS)
    assert _ahead_keys(db) == ["t:just-made"]


def test_hwf_draft_ahead_drops_a_draft_once_the_conversation_ended(db):
    import store
    put(db, "<s1@example.test>", thread_id="SENT", ts=100)
    put(db, "<s2@example.test>", thread_id="SENT", ts=200, folder="Sent")      # a synced Sent copy: answered=0
    put(db, "<a1@example.test>", thread_id="ANSWERED", ts=100, answered=1)
    put(db, "<o1@example.test>", thread_id="OPEN", ts=100, folder="Sent")
    put(db, "<o2@example.test>", thread_id="OPEN", ts=200)                     # they wrote back last
    put(db, "<f1@example.test>", thread_id="FAILED", ts=200, folder="Sent")
    put(db, "<j@example.test>", is_junk=1)
    put(db, "<k@example.test>", answered=1)
    put(db, "<o@example.test>")
    for ref in ("t:SENT", "t:ANSWERED", "t:OPEN", "m:<j@example.test>", "m:<k@example.test>", "m:<o@example.test>"):
        _ahead(db, ref, NOW_TS - 60)
    store.draft_ahead_fail(db, A, "t:FAILED", NOW_TS - 60)   # no body: only a backoff record, never moot
    store.draft_ahead_gc(db, NOW_TS, orphans=False)
    assert _ahead_keys(db) == ["m:<o@example.test>", "t:FAILED", "t:OPEN"]


def test_hwf_draft_ahead_keeps_the_500_most_recently_useful(db):
    import store
    for i in range(502):
        _ahead(db, f"t:x{i:03}", NOW_TS - 1000 + i)
    store.draft_ahead_touch(db, A, "t:x000", NOW_TS)          # oldest made, but just served
    store.draft_ahead_gc(db, NOW_TS, orphans=False)
    keys = _ahead_keys(db)
    assert len(keys) == 500 and "t:x000" in keys and not {"t:x001", "t:x002"} & set(keys)


# -- idempotent inserts that lose a race --------------------------------------

class _LosesTheRace:
    """A connection whose first SELECT finds nothing: another writer stores the same key between
    the caller's SELECT and its INSERT."""

    def __init__(self, c):
        self.c, self.first = c, True

    def execute(self, sql, *args):
        if self.first and sql.lstrip().upper().startswith("SELECT"):
            self.first = False
            return self.c.execute("SELECT 1 WHERE 0")
        return self.c.execute(sql, *args)


def test_hwf_an_outbox_insert_that_loses_a_race_returns_the_winners_row(db):
    import store
    row = {"account": A, "idempotency_key": "send:k1", "message_id": "<o@example.test>", "in_reply_to_pk": None,
           "envelope": "[]", "recipients": "{}", "subject": "s", "raw": b"x", "next_attempt_at": 0,
           "draft_id": None, "created_at": "now"}
    oid, created = store.outbox_insert(db, row)
    assert created and store.outbox_insert(_LosesTheRace(db), row) == (oid, False)
    assert db.execute("SELECT count(*) FROM outbox").fetchone()[0] == 1


def test_hwf_a_queued_mutation_insert_that_loses_a_race_returns_the_winners_row(db):
    import store
    row = {"account": A, "idempotency_key": "trash:k1", "op": "trash", "message_id": "<m@example.test>",
           "src_folder": "INBOX", "dest": "Trash", "payload": "{}", "next_attempt_at": 0, "created_at": "now"}
    mid, created = store.read_mut_insert(db, row)
    assert created and store.read_mut_insert(_LosesTheRace(db), row) == (mid, False)
    assert db.execute("SELECT count(*) FROM read_mutations").fetchone()[0] == 1


# -- threading ----------------------------------------------------------------

def test_hwf_a_new_thread_id_is_the_sha1_of_its_smallest_message_id(db):
    # Thread ids are stored keys (muted, followups, thread_summaries). A thread whose every copy is
    # re-ingested mints its id again, so the formula is part of the on-disk contract.
    reply = put(db, "<r@example.test>", irt="<m@example.test>")
    root = put(db, "<m@example.test>")
    recompute(db)
    assert tid(db, root) == tid(db, reply) == f"rf:{A}:" + hashlib.sha1(b"<m@example.test>").hexdigest()[:16]


def test_hwf_a_reply_stored_before_its_original_takes_the_threads_id_at_once(db):
    # The smallest id the thread names, not the smallest of the rows stored so far: the reply
    # alone already holds the id the whole thread would mint.
    reply = put(db, "<r@example.test>", irt="<m@example.test>")
    recompute(db)
    assert tid(db, reply) == f"rf:{A}:" + hashlib.sha1(b"<m@example.test>").hexdigest()[:16]


def test_hwf_a_thread_that_joins_a_gmail_thread_takes_its_state_along(db):
    import store
    old = put(db, "<p@example.test>")
    recompute(db)
    was = tid(db, old)
    store.set_replylater(db, A, was, None, "now")
    db.execute("INSERT INTO thread_summaries(account, thread_id, scope, fingerprint, tldr) VALUES(?,?,?,?,?)",
               (A, was, "thread", "fp", "Lunch on Friday."))
    _ahead(db, "t:" + was, NOW_TS - 60)
    put(db, "<q@example.test>", gm="1781", irt="<p@example.test>")
    recompute(db)
    now = f"gm:{A}:1781"
    assert [r["thread_id"] for r in db.execute("SELECT thread_id FROM followups")] == [now]
    assert [r["tldr"] for r in db.execute("SELECT tldr FROM thread_summaries WHERE thread_id=?", (now,))] == \
        ["Lunch on Friday."]
    assert _ahead_keys(db) == ["t:" + now]
    assert db.execute("SELECT count(*) FROM thread_summaries WHERE thread_id=?", (was,)).fetchone()[0] == 0


def test_hwf_a_merge_keeps_the_survivors_own_state_over_the_absorbed_threads(db):
    import store
    a = put(db, "<a@example.test>")
    b = put(db, "<b@example.test>")
    recompute(db)
    ta, tb = tid(db, a), tid(db, b)
    _ahead(db, "t:" + ta, NOW_TS - 60, body="From the first thread.")
    _ahead(db, "t:" + tb, NOW_TS - 60, body="From the second thread.")
    store.mute_thread(db, A, tb, "now")
    put(db, "<c@example.test>", refs="<a@example.test> <b@example.test>")      # joins the two
    recompute(db)
    surv = min(ta, tb)
    assert tid(db, a) == tid(db, b) == surv
    assert store.is_muted(db, A, surv)
    assert [r["body"] for r in db.execute("SELECT body FROM draft_ahead")] == [
        "From the first thread." if surv == ta else "From the second thread."]


def test_hwf_a_queued_reply_follows_its_thread_when_the_thread_takes_another_id(db):
    a = put(db, "<a@example.test>")
    b = put(db, "<b@example.test>")
    recompute(db)
    ta, tb = tid(db, a), tid(db, b)
    gone = max(ta, tb)
    db.execute("INSERT INTO outbox (account, idempotency_key, message_id, envelope, recipients, subject, raw, "
               "state, next_attempt_at, created_at, orig_account, orig_message_id, orig_thread_id) "
               "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", (A, "k", "<m@example.test>", "[]", "{}", "Re", b"x", "queued", 0,
                                                       "now", A, "<a@example.test>", gone))
    put(db, "<c@example.test>", refs="<a@example.test> <b@example.test>")      # joins the two
    recompute(db)
    assert db.execute("SELECT orig_thread_id FROM outbox").fetchone()[0] == min(ta, tb)


def test_hwf_a_message_without_an_id_keeps_a_thread_of_its_own_row(db):
    import store
    pk = put(db, "")
    recompute(db)
    first = tid(db, pk)
    recompute(db)
    assert tid(db, pk) == first
    store.delete_message(db, pk)
    again = put(db, "")                                    # the same message stored again: a new row
    recompute(db)
    assert tid(db, again) != first


def test_hwf_two_gmail_threads_joined_by_headers_take_the_later_rows_id(db):
    a = put(db, "<g1@example.test>", gm="1781", ts=100)
    b = put(db, "<g2@example.test>", gm="1799", ts=200, irt="<g1@example.test>")
    recompute(db)
    assert tid(db, a) == tid(db, b) == f"gm:{A}:1799"


def test_hwf_a_thread_that_joins_a_gmail_thread_keeps_its_mute(db):
    import store
    old = put(db, "<p@example.test>")                      # stored before Gmail's thread id was seen
    recompute(db)
    store.mute_thread(db, A, tid(db, old), "now")
    put(db, "<q@example.test>", gm="1781", irt="<p@example.test>")
    recompute(db)
    assert tid(db, old) == f"gm:{A}:1781"
    assert store.is_muted(db, A, f"gm:{A}:1781")


def test_hwf_a_thread_stored_again_whole_keeps_its_mute(db):
    import store
    reply = put(db, "<r@example.test>", irt="<m@example.test>")     # the reply synced before its original
    recompute(db)
    root = put(db, "<m@example.test>")
    recompute(db)
    store.mute_thread(db, A, tid(db, root), "now")
    for pk in (reply, root):                               # every copy moves away, then comes back
        store.delete_message(db, pk)
    back = put(db, "<m@example.test>")
    put(db, "<r@example.test>", irt="<m@example.test>")
    recompute(db)
    assert store.is_muted(db, A, tid(db, back))


# -- search ranking -----------------------------------------------------------

def test_hwf_search_ranks_subject_over_sender_over_recipients_over_snippet(db):
    import server
    # Every row has text of the same length in each column; "heron" sits in one column per row, and
    # newer rows would win a tie. bm25 gets 5 weights for 6 columns (subject 8, from_name 4,
    # from_addr 4, recipients 2, snippet 1, body_text 1 by default).
    spec = [("heron notes", "Lin Mori", "Noor Haddad", "a note at dawn"),
            ("plain notes", "Heron Mori", "Noor Haddad", "a note at dawn"),
            ("plain notes", "Lin Mori", "Heron Haddad", "a note at dawn"),
            ("plain notes", "Lin Mori", "Noor Haddad", "a heron at dawn")]
    pks = []
    for i, (subject, name, rcpt, snip) in enumerate(spec):
        pks.append(put(db, f"<s{i}@example.test>", subject=subject, snippet=snip, ts=100 + i,
                       from_name=name, recipients=rcpt))
    for i in range(8):                                     # enough rows without it for a positive idf
        put(db, f"<f{i}@example.test>", subject="plain notes", snippet="a note at dawn", recipients="Noor Haddad")
    db.commit()
    got = server.search_json({"q": ["heron"], "scope": ["all"]})
    assert [i["pk"] for i in got["items"]] == pks


# -- the classifier and its rules file ----------------------------------------

RULES = {"keep_senders": ["both@rules.example.test"],
         "block_senders": ["both@rules.example.test", "noreply@kept.example.test"],
         "keep_domains": ["kept.example.test", "dual.example.test"],
         "block_domains": ["dual.example.test", "blocked.example.test"]}

PRECEDENCE = [
    # (from, subject, List-Unsubscribe/List-ID, bucket, confidence)
    ("both@rules.example.test", "hi", False, "personal/protected", "high"),        # keep sender beats block sender
    ("noreply@kept.example.test", "hi", False, "blocked", "high"),                 # block sender beats keep domain
    ("x@dual.example.test", "hi", False, "personal/protected", "high"),            # keep domain beats block domain
    ("x@sub.kept.example.test", "You have won the lottery", False, "personal/protected", "high"),  # rules beat scam
    ("winner@blocked.example.test", "hi", False, "blocked", "high"),
    ("records@example.gov", "You have won the lottery", False, "spam/scam", "high"),   # scam beats protected
    ("ops@bank.example.test", "Your invoice", False, "personal/protected", "high"),  # protected beats receipt
    ("news@bank.example.test", "Rates", False, "newsletters/marketing", "med"),      # a marketing role loses it
    ("billing@studio.example.test", "hi", True, "receipts/transactional", "med"),    # receipt beats List-Unsubscribe
    ("lin@studio.example.test", "Re: lunch", True, "newsletters/marketing", "high"),  # list header beats a reply
    ("lin.example@gmail.com", "Re: [weave] patterns", True, "newsletters/marketing", "high"),
    ("lin@sendgrid.example.test", "Re: lunch", False, "newsletters/marketing", "high"),  # ESP beats a reply
    ("lin@e.studio.example.test", "Re: lunch", False, "newsletters/marketing", "med"),   # bulk sub-domain too
    ("marketing@studio.example.test", "Re: your quote", False, "other/personal-ish", "high"),  # reply beats role
    ("no-reply@studio.example.test", "Re: your quote", False, "automated-notifications", "med"),
    ("lin@studio.example.test", "AW: Termin", False, "other/personal-ish", "high"),
    ("lin@studio.example.test", "WG: Termin", False, "other/personal-ish", "high"),
    ("lin@studio.example.test", "TR: devis", False, "other/personal-ish", "high"),
    ("lin@studio.example.test", "RV: cita", False, "other/personal-ish", "high"),
    ("lin@studio.example.test", "R\u00e9f: devis", False, "other/personal-ish", "high"),
    ("lin@studio.example.test", "Fw: notes", False, "other/personal-ish", "high"),
    ("marketing@studio.example.test", "Spring range", False, "newsletters/marketing", "med"),
    ("news@gmail.com", "hello", False, "other/personal-ish", "low"),               # a role on free mail: not bulk
    ("alerts@studio.example.test", "Login", False, "automated-notifications", "med"),
    ("lin.example@gmail.com", "hi", False, "other/personal-ish", "med"),
    ("lin@studio.example.test", "hi", False, "other/personal-ish", "low"),
    ("make.example@gmail.com", "make money now", False, "spam/scam", "high"),       # weak scam from free mail
    ("deals@example.tk", "make money now", False, "spam/scam", "high"),             # or a junk TLD
    ("lin@studio.example.test", "make money now", False, "other/personal-ish", "low"),  # but not from elsewhere
]


@pytest.fixture
def rules_file(data_dir):
    path = data_dir / "mailmind_rules.json"
    with open(path, "w") as f:
        json.dump(RULES, f)
    return path


@pytest.mark.parametrize("addr,subject,listed,bucket,conf", PRECEDENCE, ids=[f"{a} {s}" for a, s, *_ in PRECEDENCE])
def test_hwf_classifier_precedence(rules_file, addr, subject, listed, bucket, conf):
    import mailclass
    assert mailclass.classify(addr, subject, "", listed)[:2] == (bucket, conf)


@pytest.mark.parametrize("addr", ["lin@msg.example.test", "ivo@nsg.example.test", "lin@studio.sg"])
def test_hwf_a_domain_merely_containing_an_esp_marker_is_not_bulk(data_dir, addr):
    import mailclass
    assert mailclass.classify(addr, "Lunch on Thursday?", "", False)[0] != "newsletters/marketing"


@pytest.mark.parametrize("addr", ["deals@sg.example.test", "deals@em.sg.example.test"])
def test_hwf_the_short_esp_marker_still_counts_as_a_whole_label(data_dir, addr):
    import mailclass
    assert mailclass.classify(addr, "Lunch on Thursday?", "", False)[:3] == (
        "newsletters/marketing", "high", "bulk email-service-provider domain")


def test_hwf_the_classifier_sees_rule_edits_and_reads_any_case(data_dir):
    import mailclass
    import rules

    def write(data, stamp):
        path = data_dir / "mailmind_rules.json"
        with open(path, "w") as f:
            json.dump(data, f)
        os.utime(path, (stamp, stamp))                   # the classifier caches the file by mtime
    write({"keep_senders": ["Lin@Studio.Example.Test"]}, NOW_TS)
    assert mailclass.classify("lin@studio.example.test", "hi")[0] == "personal/protected"
    assert rules.load()["keep_senders"] == ["lin@studio.example.test"]
    write({"block_domains": ["STUDIO.example.test"]}, NOW_TS + 1)
    assert mailclass.classify("lin@studio.example.test", "hi")[0] == "blocked"


def test_hwf_a_malformed_rules_file_does_not_stop_sync(world):
    import accounts
    import ingest
    with open(world.dir / "mailmind_rules.json", "w") as f:
        f.write('{"keep_senders": ["lin@example.test",]')
    world.server.deliver(w.ADA, "INBOX", w.variant("ada-inbox-02-lunch-confirm.eml", Message_ID="<late@example.test>"))
    assert ingest.sync_new(accounts.get(w.ADA))[0] == 1


def test_hwf_expiry_takes_the_shortest_of_matching_domain_rules():
    import rules
    expire = {"example.test": 72, "ci.example.test": 24, "noreply@ci.example.test": 240}
    assert rules.expiry_hours("bot@ci.example.test", expire) == 24
    assert rules.expiry_hours("bot@ci.example.test", {"example.test": 12, "ci.example.test": 24}) == 12
    assert rules.expiry_hours("noreply@ci.example.test", expire) == 240


def test_hwf_a_rule_change_reaches_stored_mail_only_when_it_is_stored_again(world):
    import accounts
    import ingest
    import rules
    rules.add("lin@example.test", "block")
    assert world.row("ada-inbox-01-lunch.eml")["bucket"] == "other/personal-ish"
    ingest.sync_folder(accounts.get(w.ADA), "INBOX", full=True)
    assert world.row("ada-inbox-01-lunch.eml")["bucket"] == "blocked"
    assert world.row("ada-archive-01-old-thread.eml")["bucket"] == "other/personal-ish"


def test_hwf_the_stored_inbox_plan_counts_the_junk_ingest_stored(world):
    import accounts
    import manage
    world.deliver(w.ADA, "INBOX", w.variant("ada-inbox-03-newsletter.eml", From="Lin Mori <lin@studio.example.test>",
                                            Message_ID="<issue-219@loom.example.test>"))
    [stored] = rows("SELECT count(*) AS n FROM messages WHERE account=? AND folder='INBOX' AND is_junk=1", w.ADA)
    assert stored["n"] == 4
    assert manage.plan_account_db(accounts.get(w.ADA))["junk"] == stored["n"]


def test_hwf_the_screener_skips_senders_a_parent_domain_rule_decided(world):
    import rules
    import server
    rules.add("example.test", "keep")
    assert [i["from_addr"] for i in server.do_screener(w.ADA)["items"]] == []


def test_hwf_cleanup_skips_senders_a_parent_domain_rule_decided(world):
    import rules
    import server
    for n in range(4):                                       # enough unread issues to rank as noise
        world.deliver(w.ADA, "INBOX", w.variant("ada-inbox-03-newsletter.eml", Message_ID=f"<issue-{n}@loom.example.test>",
                                                Date=f"Sun, 0{n + 1} Nov 2026 07:00:00 +0000"))
    assert "news@loom.example.test" in [s["from_addr"] for s in server.cleanup_senders({})["senders"]]
    rules.add("example.test", "block")                       # every sender in the world is under it
    assert server.cleanup_senders({})["senders"] == []


# -- saved views and the rules file on disk -----------------------------------

def test_hwf_views_load_skips_malformed_entries_and_builtin_ids(data_dir):
    import views
    with open(data_dir / "mailmind_views.json", "w") as f:
        json.dump([{"id": "v1", "name": "From Lin", "q": "from:lin"}, {"id": "__unread__", "name": "Spoof", "q": "x"},
                   {"id": "v2", "name": " ", "q": "x"}, "junk", {"id": "v3", "name": "N" * 80, "q": "q" * 600}], f)
    assert views.load() == [{"id": "v1", "name": "From Lin", "q": "from:lin"},
                            {"id": "v3", "name": "N" * 60, "q": "q" * 512}]
    merged = views.merged()
    assert [v["id"] for v in merged if v["builtin"]] == [v["id"] for v in views.DEFAULTS + views.LANES]
    assert views.remove("__unread__") is False


def test_hwf_saved_views_are_written_owner_only_and_without_builtins(data_dir):
    import views
    views.add("From Lin", "from:lin")
    assert os.stat(data_dir / "mailmind_views.json").st_mode & 0o777 == 0o600
    with open(data_dir / "mailmind_views.json") as f:
        assert [v["name"] for v in json.load(f)] == ["From Lin"]


def test_hwf_a_bad_edit_of_the_rules_keeps_the_rules_last_read(data_dir, capsys):
    import mailclass
    import rules
    rules.add("lin@example.test", "keep")
    assert mailclass.classify("lin@example.test", "Win a prize now", "Lin", False)[2] == "kept by rule (sender)"
    with open(data_dir / "mailmind_rules.json", "w") as f:
        f.write('{"keep_senders": ["lin@example.test",]')         # a hand edit with a stray comma
    assert rules.load()["keep_senders"] == ["lin@example.test"]
    assert mailclass.classify("lin@example.test", "Win a prize now", "Lin", False)[2] == "kept by rule (sender)"
    rules.load()
    assert capsys.readouterr().out.count("can't be read") == 1     # said once, not at every read


def test_hwf_a_save_over_an_unreadable_rules_file_keeps_it_in_backups(data_dir):
    import rules
    bad = '{"keep_senders": ["lin@example.test",]'
    with open(data_dir / "mailmind_rules.json", "w") as f:
        f.write(bad)
    assert rules.load()["keep_senders"] == []                     # never read well: no rules apply
    rules.add("prizes.example.test", "block")
    assert rules.load()["block_domains"] == ["prizes.example.test"]
    [aside] = (data_dir / "backups").iterdir()
    assert aside.read_text() == bad and aside.stat().st_mode & 0o777 == 0o600


def test_hwf_the_rules_file_is_written_owner_only(data_dir):
    import rules
    old = os.umask(0o022)                                  # the usual umask, whatever the test runner's
    try:
        rules.add("lin@example.test", "keep")
    finally:
        os.umask(old)
    assert os.stat(data_dir / "mailmind_rules.json").st_mode & 0o777 == 0o600


# -- parsing a message --------------------------------------------------------

def _layered():
    m = EmailMessage()
    m["From"], m["To"], m["Subject"] = "lin@example.test", A, "Swatches"
    m.set_content("first plain")
    m.add_alternative("<p>first html</p>", subtype="html")
    m.make_mixed()
    for subtype, body in (("plain", "second plain"), ("html", "<p>second html</p>")):
        part = EmailMessage()
        part.set_content(body, subtype=subtype)
        m.attach(part)
    m.add_attachment(b"%PDF-1.4 swatch", maintype="application", subtype="pdf", filename="swatch.pdf")
    m.add_attachment(b"\x89PNG quilt", maintype="image", subtype="png", disposition="inline", cid="<quilt@example.test>")
    return m.as_bytes()


def test_hwf_part_ids_count_every_leaf_in_walk_order_and_extract_part_agrees():
    from mailmind.content import mime
    raw = _layered()
    p = mime.parse(raw)
    assert (p["text"], p["html"]) == ("first plain\n", "<p>first html</p>\n")      # the first of each wins
    assert [(a["part_id"], a["filename"], a["is_inline"]) for a in p["attachments"]] == [
        ("5", "swatch.pdf", 0), ("6", "inline-6", 1)]
    assert mime.extract_part(raw, "5") == (b"%PDF-1.4 swatch", "application/pdf")
    assert mime.extract_part(raw, "6") == (b"\x89PNG quilt", "image/png")
    assert mime.extract_part(raw, "1")[0] == b"first plain\n"


def _with_part(headers):
    """A text part, then one application/octet-stream part carrying `headers`."""
    if b"Content-Type" not in headers:
        headers = b"Content-Type: application/octet-stream\r\n" + headers
    return (b"From: lin@example.test\r\nMIME-Version: 1.0\r\nContent-Type: multipart/mixed; boundary=\"b\"\r\n\r\n"
            b"--b\r\nContent-Type: text/plain\r\n\r\nhi\r\n--b\r\n" + headers + b"\r\ndata\r\n--b--\r\n")


ATTACHMENT_RULE = [
    # (case, part headers, (filename, is_inline, content_id), or None when the part isn't listed)
    ("attachment, no name", b"Content-Disposition: attachment\r\n", ("part-2", 0, None)),
    ("attachment with a cid", b"Content-Disposition: attachment; filename=a.bin\r\nContent-ID: <a@example.test>\r\n",
     ("a.bin", 1, "a@example.test")),
    ("a name, no disposition", b"Content-Type: application/octet-stream; name=a.bin\r\n", ("a.bin", 0, None)),
    ("inline with a name, no cid", b"Content-Disposition: inline; filename=a.bin\r\n", ("a.bin", 1, None)),
    ("inline with a name and a cid", b"Content-Disposition: inline; filename=a.bin\r\nContent-ID: <a@example.test>\r\n",
     ("a.bin", 1, "a@example.test")),
    ("a cid alone", b"Content-ID: <a@example.test>\r\n", ("inline-2", 1, "a@example.test")),
    ("nothing", b"", None),
]


@pytest.mark.parametrize("headers,want", [c[1:] for c in ATTACHMENT_RULE], ids=[c[0] for c in ATTACHMENT_RULE])
def test_hwf_which_parts_are_listed_as_attachments(headers, want):
    from mailmind.content import mime
    got = [(a["filename"], a["is_inline"], a["content_id"]) for a in mime.parse(_with_part(headers))["attachments"]]
    assert got == ([want] if want else [])


def _forwarded_email(outer_text):
    inner = EmailMessage()
    inner["From"], inner["Subject"] = "lin@example.test", "Fabric"
    inner.set_content("the inner note")
    inner.add_attachment(b"%PDF-1.4", maintype="application", subtype="pdf", filename="swatch.pdf")
    outer = EmailMessage()
    outer["From"], outer["Subject"] = "noor@studio.example.test", "Fwd: Fabric"
    if outer_text:
        outer.set_content("see attached")
    else:
        outer.add_alternative("<p>see attached</p>", subtype="html")
    outer.add_attachment(inner)
    return outer.as_bytes()


def test_hwf_an_attached_emails_files_are_not_listed_as_the_outers():
    from mailmind.content import mime
    assert [a["content_type"] for a in mime.parse(_forwarded_email(True))["attachments"]] == ["message/rfc822"]


def test_hwf_an_attached_emails_text_is_not_taken_for_the_outers():
    from mailmind.content import mime
    assert mime.parse(_forwarded_email(False))["text"] is None


def test_hwf_an_attached_email_is_one_part_of_the_right_size():
    from mailmind.content import mime
    raw = _forwarded_email(True)
    [att] = mime.parse(raw)["attachments"]
    data, ctype = mime.extract_part(raw, att["part_id"])
    assert ctype == "message/rfc822" and att["size"] == len(data) and b"Subject: Fabric" in data


def test_hwf_an_attached_email_with_no_file_name_is_still_listed():
    from mailmind.content import mime
    raw = _forwarded_email(True).replace(b"Content-Disposition: attachment\n", b"")
    assert [(a["filename"], a["content_type"]) for a in mime.parse(raw)["attachments"]] == [
        ("message.eml", "message/rfc822")]


def test_hwf_a_snippet_leaves_an_attached_emails_text_out():
    import email
    assert "the inner note" not in text_body(email.message_from_bytes(_forwarded_email(False)))


def test_hwf_a_forward_carries_an_attached_email_whole_and_not_base64():
    import email
    from email import policy
    import compose
    import server
    [att] = server._extract_attachments(_forwarded_email(True))
    assert att["content_type"] == "message/rfc822" and b"Subject: Fabric" in att["data"]
    built = compose.build_message({"account": A, "to": ["lin@example.test"], "subject": "Fwd: Fabric", "text": "fyi",
                                   "attachments": [att]})
    [out] = email.message_from_bytes(built["raw"], policy=policy.default).iter_attachments()
    assert out.get_content_type() == "message/rfc822" and out["Content-Transfer-Encoding"] != "base64"
    assert out.get_content()["Subject"] == "Fabric"
    assert [a.get_filename() for a in out.get_content().iter_attachments()] == ["swatch.pdf"]


def test_hwf_a_message_whose_part_rows_were_cleared_records_them_when_opened(world):
    import ingest
    import store
    name = "ada-inbox-07-forward-rfc822.eml"
    pk = world.pk(name)
    first = ingest.fetch_body(pk)["attachments"]
    assert first
    c = store.conn()
    c.execute("DELETE FROM attachments WHERE message_pk=?", (pk,))     # what migration 28 does
    c.commit()
    c.close()
    assert ingest.fetch_body(pk)["attachments"] == first
    assert [r["part_id"] for r in rows("SELECT part_id FROM attachments WHERE message_pk=?", pk)] == [
        a["part_id"] for a in first]


def test_hwf_an_encoded_line_break_in_to_does_not_break_parsing():
    from mailmind.content import mime
    raw = (b"From: lin@example.test\r\nTo: =?utf-8?q?Bo=0D=0AX?= <bo@example.test>\r\nSubject: x\r\n\r\nhello\r\n")
    to = mime.parse(raw)["to"]
    assert "bo@example.test" in to and "\r" not in to and "\n" not in to


# -- composing ----------------------------------------------------------------

def test_hwf_a_recorded_tail_is_cut_only_when_it_starts_with_a_blank_line():
    import compose
    quote = "On Mon, Lin wrote:\n> hi\n"
    assert compose.resume_split("Mine\n\n" + quote, {"own_text": None, "tail_text": "\n\n" + quote}, False) == (
        "Mine", "\n\n" + quote, "tail")
    assert compose.resume_split("Mine\n" + quote, {"own_text": None, "tail_text": quote}, False) == (
        "Mine\n" + quote.rstrip("\n"), "", "whole")


def test_hwf_a_fold_in_a_header_leaves_one_space():
    from mailmind.content import mime
    assert mime.dec("Items\r\n for next week") == "Items for next week"
    assert mime.dec("Items\n\tfor next week") == "Items for next week"


def test_hwf_a_fold_beside_an_encoded_word_keeps_its_space():
    from mailmind.content import mime
    assert mime.dec("Re:\r\n =?utf-8?B?2YXYsdit2KjYpw==?=") == "Re: \u0645\u0631\u062d\u0628\u0627"
    assert mime.dec("=?utf-8?Q?Caf=C3=A9_menu?=\r\n for next week") == "Caf\u00e9 menu for next week"
    assert mime.dec("=?utf-8?Q?no?=\r\n =?utf-8?Q?vembre?=") == "novembre"     # one word, split by the fold


def test_hwf_a_fold_in_a_quoted_sender_keeps_the_address():
    import email
    from mailmind.content import mime
    msg = email.message_from_bytes(b'From: "Bo\r\n Lin" <bo@example.test>\r\n\r\nHi\r\n')
    assert mime.sender_of(msg) == ("Bo Lin", "bo@example.test")


def test_hwf_a_tab_in_a_subject_is_not_deleted():
    import email
    import compose
    built = compose.build_message({"account": A, "to": ["lin@example.test"], "subject": "Plans\tfor next week",
                                   "text": "hi"})
    assert email.message_from_bytes(built["raw"])["Subject"] == "Plans for next week"


FOLDABLE = {"from_name": "Lin Mori", "from_addr": "lin@example.test", "date": "Mon, 2 Nov 2026", "text": None,
            "html": "<p>Sounds good.</p><blockquote>On Sun, Ada wrote: lunch?</blockquote>"}


@pytest.mark.parametrize("bodies", ["reply_bodies", "forward_bodies"])
def test_hwf_mail_going_out_carries_no_reader_quote_fold(bodies):
    import compose
    text, html = getattr(compose, bodies)("Great", FOLDABLE)
    assert "<details" not in html and "quoted text" not in text
    assert "On Sun, Ada wrote: lunch?" in html and "On Sun, Ada wrote: lunch?" in text


def test_hwf_the_reader_still_folds_a_quote():
    from mailmind.content import sanitize
    assert '<details class="mm-quote">' in sanitize.sanitize(FOLDABLE["html"])[0]


def test_hwf_a_quote_stored_with_the_fold_loses_only_the_fold():
    from mailmind.content import sanitize
    folded = sanitize.sanitize(FOLDABLE["html"])[0]
    assert sanitize.without_fold(folded) == sanitize.sanitize(FOLDABLE["html"], fold=False)[0]
    assert sanitize.without_fold(f"<blockquote>{folded}</blockquote>") == \
        f'<blockquote>{sanitize.sanitize(FOLDABLE["html"], fold=False)[0]}</blockquote>'
    theirs = "<details><summary>Their own</summary>Kept</details>"
    assert sanitize.without_fold(theirs) == theirs


def test_hwf_a_draft_saved_with_the_fold_goes_out_without_it(world):
    import server
    import store
    c = store.conn()
    fold = '<details class="mm-quote"><summary>··· quoted text</summary>'
    did = store.draft_upsert(c, {"id": None, "account": A, "folder": "Drafts", "uid": 7, "message_id": "<d@example.test>",
                                 "uidvalidity": 1, "in_reply_to_pk": None, "mode": "reply", "subject": "Re: Lunch",
                                 "created_at": "2026-10-01T09:00:00", "updated_at": "2026-10-01T09:00:00"})
    store.draft_set_resume(c, did, "Great", "\n\nOn Sunday:\n> lunch?\n",
                           f"<div>On Sunday:</div><blockquote>{fold}<blockquote>lunch?</blockquote></details></blockquote>",
                           None, "sha")
    c.commit()
    c.close()
    spec, _, _ = server._compose_spec({"account": A, "mode": "reply", "to": ["lin@example.test"], "subject": "Re: Lunch",
                                       "text": "Great", "draft_id": did,
                                       "resume": {"tail_text": "\n\nOn Sunday:\n> lunch?\n"}})
    assert "<details" not in spec["html"] and "lunch?" in spec["html"]


# -- calendar recurrence ------------------------------------------------------

def _cal(*events):
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0"]
    for ev in events:
        lines += ["BEGIN:VEVENT", *ev, "END:VEVENT"]
    return "\r\n".join(lines + ["END:VCALENDAR", ""])


START = datetime(2026, 10, 5, 10, 0)                       # a Monday; floating times throughout


def _next(rule, *extra, start="20261005T100000", end="20261005T103000", after=datetime(2026, 11, 1, 12, 0)):
    import ics
    head = "DTSTART;VALUE=DATE:" if len(start) == 8 else "DTSTART:"
    tail = "DTEND;VALUE=DATE:" if len(end) == 8 else "DTEND:"
    inv = ics.parse(_cal(["UID:r", head + start, tail + end, "RRULE:" + rule, *extra]))
    got = ics.next_occurrence(inv, after)
    return got and got[0]


def test_hwf_only_the_first_event_of_a_calendar_is_read():
    import ics
    inv = ics.parse(_cal(["UID:a", "SUMMARY:First", "DTSTART:20261110T100000"],
                         ["UID:b", "SUMMARY:Second", "DTSTART:20261111T100000"]))
    assert (inv["uid"], inv["summary"], inv["start_iso"]) == ("a", "First", "2026-11-10T10:00:00")


def test_hwf_count_includes_occurrences_an_exdate_removes():
    # Mondays from Oct 5: COUNT=5 ends on Nov 2, which EXDATE removes; it still counts
    assert _next("FREQ=WEEKLY;COUNT=5", "EXDATE:20261102T100000") is None
    assert _next("FREQ=WEEKLY;COUNT=6", "EXDATE:20261102T100000") == datetime(2026, 11, 9, 10, 0)


def test_hwf_an_all_day_series_skips_an_exdate_by_its_date():
    assert _next("FREQ=WEEKLY", "EXDATE:20261102T090000", start="20261005", end="20261006") == datetime(2026, 11, 9)


def test_hwf_bymonthday_only_with_monthly_and_byday_never_with_yearly():
    assert _next("FREQ=MONTHLY;BYMONTHDAY=5") == datetime(2026, 11, 5, 10, 0)
    assert _next("FREQ=WEEKLY;BYMONTHDAY=5") is None
    assert _next("FREQ=YEARLY;BYMONTHDAY=5") is None
    assert _next("FREQ=YEARLY;BYDAY=MO") is None


def test_hwf_a_rule_is_walked_20000_periods_at_most():
    last = START + timedelta(days=19_999)
    assert _next("FREQ=DAILY", after=last.replace(hour=0)) == last
    assert _next("FREQ=DAILY", after=last + timedelta(days=1)) is None
