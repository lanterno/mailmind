"""The search grammar (discovery.build): each operator's (FTS MATCH, WHERE fragments, params) triple,
the promise that no input raises and every MATCH it builds is one SQLite accepts, and two known
bugs in negation. The results these give over the world's mail are pinned in
tests/golden/http_get/search_*.json.
"""
import random
import string

import pytest

import problems
from fixtures import world as w

LABELLED = "EXISTS (SELECT 1 FROM message_labels l WHERE l.message_pk = m.pk AND l.label COLLATE NOCASE IN (?, ?))"
FROM = "{from_name from_addr}"

TABLE = [
    # words and phrases
    ("lunch", ('"lunch"', [], [])),
    ('"a phrase"', ('"a phrase"', [], [])),
    ('say "he said ""hi"""', ('"say" "he said hi"', [], [])),
    ("lunch OR quilt", ('"lunch" "OR" "quilt"', [], [])),          # OR is a word, not an operator
    ('"unbalanced', ('"""unbalanced"', [], [])),                   # shlex fails: split on spaces, quote kept
    ("nope:value", ('"nope:value"', [], [])),                      # unknown operator: a word
    ("from:", ('"from:"', [], [])),                                # operator without a value: a word
    ("-", ('"-"', [], [])),
    # FTS columns
    ("from:lin", (f'{FROM} : "lin"', [], [])),
    ("FROM:lin", (f'{FROM} : "lin"', [], [])),
    ('subject:"a b"', ('subject : "a b"', [], [])),
    ("body:quilt", ('body_text : "quilt"', [], [])),
    ("to:lin cc:noor", ('recipients : "lin" recipients : "noor"', [], [])),
    ("lunch -from:ada", (f'"lunch" NOT ({FROM} : "ada")', [], [])),
    ("lunch -from:ada -quilt", (f'"lunch" NOT ({FROM} : "ada" OR "quilt")', [], [])),
    ("-from:lin", (None, ["m.pk NOT IN (SELECT rowid FROM msg_fts WHERE msg_fts MATCH ?)"], [f'{FROM} : "lin"'])),
    # flags, buckets, categories
    ("is:unread", (None, ["m.seen = ?"], [0])),
    ("Is:Unread", (None, ["m.seen = ?"], [0])),
    ("-is:unread", (None, ["m.seen = ?"], [1])),
    ("is:read", (None, ["m.seen = ?"], [1])),
    ("is:flagged", (None, ["m.flagged = ?"], [1])),
    ("is:answered", (None, ["m.answered = ?"], [1])),
    ("is:junk", (None, ["m.is_junk = ?"], [1])),
    ("is:newsletter", (None, ["m.bucket = ?"], ["newsletters/marketing"])),
    ("-is:receipt", (None, ["m.bucket IS NOT ?"], ["receipts/transactional"])),
    ("bucket:receipt", (None, ["m.bucket = ?"], ["receipts/transactional"])),
    ("bucket:weird", (None, ["m.bucket = ?"], ["weird"])),
    ("category:Finance", (None, ["s.category = ?"], ["finance"])),
    ("-category:info", (None, ["s.category IS NOT ?"], ["info"])),
    ("has:attachment", (None, ["m.has_attach = ?"], [1])),
    ("-has:attachment", (None, ["m.has_attach = ?"], [0])),
    # scope and time
    ("in:INBOX", (None, ["m.folder = ?"], ["INBOX"])),
    ("-in:Trash", (None, ["m.folder <> ?"], ["Trash"])),
    (f"account:{w.ADA}", (None, ["m.account = ?"], [w.ADA])),
    ("before:2026-10-25", (None, ["m.ts < ?"], [1792886400])),
    ("after:2026-10-31T09:00", (None, ["m.ts >= ?"], [1793437200])),
    ("after:soon", (None, [], [])),                                # a date that isn't one is ignored
    # together
    ("from:lin is:unread in:INBOX", (f'{FROM} : "lin"', ["m.seen = ?", "m.folder = ?"], [0, "INBOX"])),
    ("label:Travel", (None, [LABELLED], ["Travel", "\\Travel"])),
    ("-label:Travel", (None, ["NOT " + LABELLED], ["Travel", "\\Travel"])),
]


@pytest.mark.parametrize("query,want", TABLE, ids=[q for q, _ in TABLE])
def test_the_grammar(query, want):
    from mailmind.bootstrap import discovery
    assert discovery.build(query) == want


def test_a_query_is_cut_at_512_characters():
    from mailmind.bootstrap import discovery
    match, _, _ = discovery.build("x" * 600)
    assert match == '"' + "x" * 512 + '"'


NASTY = ['"', '""', '"""', "'", "\\", "-\"", "from:\"", ":", "::", "-:", "a:b:c", "NOT", "AND", "NEAR(a b)",
         "*", "a*", "^a", "{from_name}", "(", ")", "é\u0301", "\u202e", "-- ;DROP TABLE messages"]


def test_no_query_raises_and_every_match_is_one_fts_accepts(data_dir):
    from mailmind.bootstrap import discovery
    from mailmind.bootstrap.mailbox import store
    rnd = random.Random(20261102)
    alphabet = string.printable + "éß\u202e\u0301"
    queries = NASTY + ["".join(rnd.choice(alphabet) for _ in range(rnd.randint(1, 40))) for _ in range(300)]
    c = store.conn()
    try:
        for q in queries:
            match, where, params = discovery.build(q)
            assert len(where) == len(params), q
            if match is not None:
                c.execute("SELECT rowid FROM msg_fts WHERE msg_fts MATCH ?", (match,)).fetchall()
    finally:
        c.close()


def test_a_nul_in_the_query_is_a_bad_query_not_a_server_error(app):
    # FTS5 reads the bound MATCH string only up to the NUL, so it sees an unterminated quote
    r = app.get("/api/search?scope=all&q=lunch%00quilt")
    assert (r.status, r.json) == (400, problems.refused("bad query"))


def senders(app, q):
    r = app.get(f"/api/search?scope=all&q={q}").json
    return {i["from_addr"] for i in r["items"]}, len(r["items"])


def test_a_negation_alone_excludes_what_it_names(app, world):
    found, n = senders(app, "-from:lin")
    assert n > 0 and "lin@example.test" not in found



@pytest.mark.parametrize("query,fragment", [("-before:2026-11-02", "m.ts >= ?"), ("-after:2026-11-02", "m.ts < ?"),
                                            ("before:2026-11-02", "m.ts < ?"), ("after:2026-11-02", "m.ts >= ?")])
def test_not_before_a_time_is_after_it(query, fragment):
    from mailmind.bootstrap import discovery
    assert discovery.build(query)[1] == [fragment]


def test_excluding_a_category_keeps_mail_that_has_none(app, world):
    _, everything = senders(app, "in:INBOX")
    assert senders(app, "in:INBOX%20-category:info")[1] == everything     # nothing is summarized here
