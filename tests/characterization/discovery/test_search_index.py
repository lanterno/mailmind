"""The search index: every way a row leaves it, a database without it, how its columns rank, and a query it rejects."""
import problems
from mailmind.bootstrap import discovery
from mailmind.bootstrap.assistant import answers
from stored_rows import A, col, hits, put


def test_hwf_every_delete_path_drops_the_search_row_and_body(db):
    from mailmind.bootstrap.mailbox import store
    one, two, three = (put(db, f"<d{i}@example.test>", subject="Heron notes") for i in range(3))
    for pk in (one, two, three):
        answers.save_summary(db, pk, "s", 3, "info", "", "m", "now")
        store.store_body(db, pk, b"Subject: Heron notes\r\n\r\nbody", "now")
    assert hits(db, '"heron"') == [one, two, three]
    store.delete_message(db, one)
    uid_three = col(db, three, "uid")
    store.reconcile(db, A, "INBOX", [uid_three], "now")          # prunes two, keeps three
    assert hits(db, '"heron"') == [three]
    store.reset_folder(db, A, "INBOX")
    assert hits(db, '"heron"') == []
    assert [db.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
            for t in ("msg_fts", "bodies", "messages")] == [0, 0, 0]


def test_hwf_search_index_helpers_tolerate_a_database_without_the_index(db):
    from mailmind.bootstrap.mailbox import store
    db.execute("DROP TABLE msg_fts")
    pk = put(db, "<x@example.test>")                      # upsert indexes; a missing index is skipped
    store.fts_set_body(db, pk, "text")
    store.delete_message(db, pk)
    assert db.execute("SELECT count(*) FROM messages").fetchone()[0] == 0


def test_hwf_search_ranks_subject_over_sender_over_recipients_over_snippet(db):
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
    got = discovery.search.answer({"q": ["heron"], "scope": ["all"]})
    assert [i["key"] for i in got["items"]] == [col(db, pk, "message_key") for pk in pks]


def test_hwf_a_search_the_index_rejects_answers_bad_query(app, world, monkeypatch):
    from mailmind.bootstrap import discovery
    monkeypatch.setattr(discovery.fts, "to_sql", lambda spec: ('"', [], []))   # a MATCH string FTS5 can't parse
    r = app.get("/api/search?q=lunch")
    assert (r.status, r.json) == (400, problems.refused("bad query"))
