"""The migrations that carry data: run over mail stored at an older version, and the table they retire."""
from stored_rows import A, col, hits, rows, tid


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
    from mailmind.bootstrap.mailbox import store
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
    from mailmind.bootstrap.mailbox import store
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
    from mailmind.bootstrap.mailbox import store
    _db_at(monkeypatch, data_dir, 17, "v17.db")
    c = store.conn()
    c.execute("CREATE TABLE notes (id INTEGER PRIMARY KEY, body TEXT)")
    c.commit()
    c.close()
    store.init()
    assert rows("SELECT name FROM sqlite_master WHERE name='notes'") == []
