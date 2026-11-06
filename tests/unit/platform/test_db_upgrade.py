"""Upgrading a database that holds data, from every version to the newest.

Each database is fictional: built at its version in a temp folder, then filled by `seed`, which
puts three rows in every table there is. The second message answers the first, and each has its
raw mail in `bodies`, so the migrations that read the mail have something to read.

An upgrade keeps every row and every value. The exceptions are the columns a migration derives on
purpose: m5 computes the thread ids, m6 and m10 read the body text and the recipients out of the
stored mail, and m22 rewrites thread summaries' fingerprints. Those must come out right instead. The database base is today's schema, so m1's
rebuild of a pre-folder sync_state is not exercised here.
"""
import sqlite3

import pytest

from mailmind.platform.db import migrate, migrations
from mailmind.platform.db.migrations import m0001_m0020 as frozen

NEWEST = migrations.MIGRATIONS[-1][0]
ROWS = 3
ACCOUNT = "ada@example.test"
# (table, column): the version whose migration derives it; an upgrade from below that one may
# change it.
DERIVED = {("messages", "thread_id"): 5, ("messages", "body_text"): 6, ("messages", "recipients"): 10,
           ("thread_summaries", "fingerprint"): 22, ("messages", "message_key"): 30}
DROPPED = migrations.RETIRED            # m18 retired the private notes, and m37 briefing_cache


def mail(n, to, text, **headers):
    lines = ["From: Bea <bea@example.test>", f"To: {to}", f"Subject: message {n}",
             f"Message-ID: <m{n}@example.test>", *[f"{k.replace('_', '-')}: {v}" for k, v in headers.items()],
             "Content-Type: text/plain; charset=utf-8", "", text, ""]
    return "\r\n".join(lines).encode()


TEXTS = ["The marigold border needs four more squares.", "Four more, and then the binding.",
         "Your turn on Saturday."]
TO = ["Cy <cy@example.test>", "Bea <bea@example.test>", "Cy <cy@example.test>"]
REPLY = {"in_reply_to": "<m1@example.test>", "refs": "<m1@example.test>"}
# Where a made-up value won't do. Each message holds what the app would have read from its mail.
MESSAGES = [{"pk": i + 1, "account": ACCOUNT, "folder": "INBOX", "uid": i + 1, "message_id": f"<m{i + 1}@example.test>",
             "in_reply_to": None, "refs": None, "gmail_thread": None, "thread_id": None,
             "subject": ["Quilt pattern for the fair", "Re: Quilt pattern for the fair", "Allotment rota"][i],
             "body_text": TEXTS[i], "recipients": TO[i], **(REPLY if i == 1 else {})} for i in range(ROWS)]
BODIES = [{"message_pk": i + 1, "raw": mail(i + 1, TO[i], TEXTS[i], **({"In_Reply_To": "<m1@example.test>"}
                                                                       if i == 1 else {}))} for i in range(ROWS)]
SPECIAL = {"messages": MESSAGES, "bodies": BODIES}


def made_up(table, column, kind, i):
    """A value of the column's type, different in each row (so every UNIQUE holds) and, for an
    integer, a pk of one of the messages (so every foreign key holds)."""
    kind = kind.upper()
    if "INT" in kind:
        return i + 1
    if "BLOB" in kind:
        return f"{table}.{column}.{i}".encode()
    if "REAL" in kind:
        return i + 0.5
    return f"{table}.{column}.{i}"


def tables(c):
    """The tables a migration may carry rows through: not SQLite's, and not the full-text index,
    which is checked by searching it."""
    return [n for (n,) in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE "
                                    "'sqlite_%' AND name NOT LIKE 'msg_fts%' ORDER BY name")]


def seed(path):
    c = sqlite3.connect(path)
    c.row_factory = sqlite3.Row             # as fts_index reads its rows
    c.execute("PRAGMA foreign_keys=ON")
    try:
        for t in sorted(tables(c), key=lambda t: (t != "messages", t != "message_identity", t != "ai_batches")):    # what the rest refer to first
            columns = [(r["name"], r["type"]) for r in c.execute(f'PRAGMA table_info("{t}")')]
            for i in range(ROWS):
                row = {name: made_up(t, name, kind, i) for name, kind in columns}
                row.update({k: v for k, v in SPECIAL.get(t, [{}] * ROWS)[i].items() if k in row})
                c.execute(f'INSERT INTO "{t}"({", ".join(row)}) VALUES({", ".join("?" * len(row))})',
                          list(row.values()))
        for pk in range(1, ROWS + 1):
            frozen.fts_index(c, pk)         # as ingest indexes each message; a no-op before m6
        c.commit()
    finally:
        c.close()


def query(path, sql, params=()):
    c = sqlite3.connect(path)
    try:
        return c.execute(sql, params).fetchall()
    finally:
        c.close()


def shape(path):
    c = sqlite3.connect(path)
    try:
        return {t: [r[1] for r in c.execute(f'PRAGMA table_info("{t}")')] for t in tables(c)}
    finally:
        c.close()


def contents(path, shape_):
    """Each table's rows, by rowid, holding only the columns `shape_` names."""
    out = {}
    for t, columns in shape_.items():
        listed = ", ".join(f'"{x}"' for x in columns)
        out[t] = {r[0]: dict(zip(columns, r[1:])) for r in query(path, f'SELECT rowid, {listed} FROM "{t}"')}
    return out


def upgraded(tmp_path, version):
    """A database built and filled at `version`, then brought to the newest; and what it held."""
    path = str(tmp_path / "upgraded.db")
    migrate.init(path, migrations.MIGRATIONS[:version])
    seed(path)
    before = contents(path, shape(path))
    migrate.init(path)
    return path, before


@pytest.mark.parametrize("version", range(NEWEST + 1))
def test_an_upgrade_keeps_every_row_and_every_value(tmp_path, version):
    path, before = upgraded(tmp_path, version)
    assert query(path, "PRAGMA user_version") == [(NEWEST,)]
    gone = {t for t, v in DROPPED.items() if t in before and version < v}
    assert set(shape(path)) >= set(before) - gone and not gone & set(shape(path))
    after = contents(path, {t: list(rows[1]) if rows else [] for t, rows in before.items() if t not in gone})
    derived = {k for k, v in DERIVED.items() if version < v}

    def kept(t, rows):
        return {pk: {k: v for k, v in row.items() if (t, k) not in derived} for pk, row in rows.items()}
    for t in after:
        assert len(before[t]) == ROWS, t
        assert kept(t, after[t]) == kept(t, before[t]), t
    assert query(path, "PRAGMA integrity_check") == [("ok",)]
    assert query(path, "PRAGMA foreign_key_check") == []
    query(path, "INSERT INTO msg_fts(msg_fts) VALUES('integrity-check')")


@pytest.mark.parametrize("version", range(NEWEST + 1))
def test_the_mail_can_be_searched_after_an_upgrade(tmp_path, version):
    """Below m6 there was no index; m6 builds it, from the body text and recipients it reads out
    of the stored mail. m38 builds it again from the messages."""
    path, _ = upgraded(tmp_path, version)
    search = "SELECT rowid FROM msg_fts WHERE msg_fts MATCH ? ORDER BY rowid"
    assert query(path, search, ("marigold",)) == [(1,)]
    assert query(path, search, ("recipients:cy",)) == [(1,), (3,)]
    got = {pk: (text, rcpt) for pk, text, rcpt in query(path, "SELECT pk, body_text, recipients FROM messages")}
    for i in range(ROWS):
        assert got[i + 1][0].strip() == TEXTS[i] and TO[i].split("<")[1].rstrip(">") in got[i + 1][1]


@pytest.mark.parametrize("version", range(5))
def test_an_upgrade_from_before_m5_threads_the_reply_with_what_it_answers(tmp_path, version):
    path, _ = upgraded(tmp_path, version)
    thread = dict(query(path, "SELECT pk, thread_id FROM messages"))
    assert thread[1] and thread[3]
    assert thread[1] == thread[2] != thread[3]


def test_a_folder_synced_before_m24_still_counts_as_synced(tmp_path):
    """m24 can't tell a folder warming read in part from one read whole, so a folder that has
    finished a sync counts as synced, as it did before; one that never has doesn't."""
    path = str(tmp_path / "m24.db")
    migrate.init(path, migrations.MIGRATIONS[:23])
    c = sqlite3.connect(path)
    c.execute("INSERT INTO sync_state(account, folder, last_sync, last_uid) "
              "VALUES (?, 'INBOX', '2026-11-01T09:00:00+00:00', 12)", (ACCOUNT,))
    c.execute("INSERT INTO sync_state(account, folder, uidvalidity) VALUES (?, 'Junk', 7)", (ACCOUNT,))
    c.commit()
    c.close()
    migrate.init(path)
    assert query(path, "SELECT folder, scan_state FROM sync_state ORDER BY folder") == [
        ("INBOX", "synced"), ("Junk", "never")]


def test_part_rows_go_only_for_a_message_with_an_email_attached(tmp_path):
    """m28: parsing stopped numbering an attached email's parts along with the message's own."""
    path = str(tmp_path / "m28.db")
    migrate.init(path, migrations.MIGRATIONS[:27])
    c = sqlite3.connect(path)
    c.execute("PRAGMA foreign_keys=OFF")
    for pk, ctype in ((1, "application/pdf"), (2, "application/pdf"), (2, "Message/RFC822")):
        c.execute("INSERT INTO attachments(message_pk, part_id, filename, content_type) VALUES (?, '2', 'f', ?)", (pk, ctype))
    c.commit()
    c.close()
    migrate.init(path)
    assert query(path, "SELECT message_pk, content_type FROM attachments") == [(1, "application/pdf")]


@pytest.mark.parametrize("table,version", [(t, v) for t, v in migrations.RETIRED.items()])
def test_a_table_a_migration_retired_stays_gone_on_the_next_start(tmp_path, table, version):
    path = str(tmp_path / "started.db")
    migrate.init(path, migrations.MIGRATIONS[:version - 1])
    migrate.init(path)
    assert table not in shape(path)
    migrate.init(path)
    assert table not in shape(path)


def test_the_queue_s_rows_become_commands_with_their_ids(tmp_path):
    """m39: `messages.pending_removal` names a row by id, and any open row may already have reached the server."""
    path = str(tmp_path / "m39.db")
    migrate.init(path, migrations.MIGRATIONS[:38])
    c = sqlite3.connect(path)
    c.execute("""INSERT INTO read_mutations(id, account, idempotency_key, op, message_id, src_folder, dest, payload,
                   state, attempts, next_attempt_at, applied, created_at)
                 VALUES (7, 'ada@example.test', 'k', 'trash', '<m@example.test>', 'INBOX', 'Trash', '{"uid": 3}',
                   'queued', 2, 40, 0, 'now')""")
    c.commit()
    c.close()
    migrate.init(path)
    assert "read_mutations" not in shape(path)
    assert query(path, """SELECT id, kind, params, state, attempts, next_attempt_at, applied, begun, outcome
                          FROM mail_commands""") == [(7, "trash", '{"uid": 3}', "queued", 2, 40, 0, 1, None)]


def test_a_database_with_no_migration_to_run_is_left_alone(tmp_path):
    path = str(tmp_path / "started.db")
    migrate.init(path)
    query(path, "DROP TABLE muted")          # the base schema creates it; only the next run could bring it back
    migrate.init(path)
    assert "muted" not in shape(path)
    assert query(path, "PRAGMA user_version") == [(NEWEST,)]


def test_a_new_database_starts_from_the_base_schema_with_no_migration_at_all(tmp_path):
    path = str(tmp_path / "base.db")
    migrate.init(path, [])
    assert "messages" in shape(path) and query(path, "PRAGMA user_version") == [(0,)]


def test_a_database_older_than_the_frozen_migrations_gets_what_the_base_schema_has_first(tmp_path):
    path = str(tmp_path / "older.db")
    migrate.init(path, migrations.MIGRATIONS[:15])
    query(path, "DROP TABLE muted")
    migrate.init(path)
    assert "muted" in shape(path) and query(path, "PRAGMA user_version") == [(NEWEST,)]
