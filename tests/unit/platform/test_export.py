"""The export's read-only view of the database, wherever the data directory is, and what an archive leaves behind it."""
import os
import zipfile

import pytest


@pytest.mark.parametrize("name", ["plain", "with space", "hash#mark", "what?", "per%cent"])
def test_the_export_reads_the_database_whatever_its_directory_is_called(tmp_path, name):
    from mailmind.platform.db import export
    from mailmind.bootstrap.mailbox import store
    from mailmind.platform import settings
    home = tmp_path / name
    home.mkdir()
    with settings.use(settings.Settings(home=str(home))):
        store.init()
        c = store.conn()
        c.execute("INSERT INTO messages (account, kind, folder, uid) VALUES ('ada@example.test', 'imap', 'INBOX', 1)")
        c.commit()
        c.close()
        assert export.plan()["total"] == 1


@pytest.mark.parametrize("form", ["eml", "mbox"])
def test_only_the_zip_is_left_of_what_an_archive_wrote(data_dir, form):
    from mailmind.platform.db import export
    from mailmind.bootstrap.mailbox import store
    c = store.conn()
    c.executemany("INSERT INTO messages (account, kind, folder, uid, message_id) VALUES ('ada@example.test', 'imap', ?, ?, ?)",
                  [("INBOX", 1, "<one@example.test>"), ("Archive", 2, "<two@example.test>")])
    c.commit()
    c.close()
    out = data_dir / "out"
    out.mkdir()
    path = export.archive(str(out), form)
    assert os.listdir(out) == [os.path.basename(path)]
    with zipfile.ZipFile(path) as z:
        assert len(z.namelist()) == 2


def test_everything_an_export_writes_is_its_owners_alone_wherever_it_goes(data_dir, tmp_path_factory):
    """`--out /tmp/mail` is the usage's own example, and /tmp is every user's: each file readable by its owner only,
    and each folder the export makes listable by them only, since its names are addresses and folders."""
    from mailmind.platform.db import export
    from mailmind.bootstrap.mailbox import store
    c = store.conn()
    c.execute("INSERT INTO messages (account, kind, folder, uid, message_id) VALUES ('ada@example.test', 'imap', 'INBOX', 1, "
              "'<one@example.test>')")
    c.commit()
    c.close()
    shared = tmp_path_factory.mktemp("shared")
    os.chmod(shared, 0o755)                                  # a folder that was there: left as it is
    export.export_eml(str(shared / "eml"))
    export.export_mbox(str(shared / "mbox" / "nested"))
    export.export_db(str(shared / "db"))
    zipped = tmp_path_factory.mktemp("zip")
    export.archive(str(zipped), "eml")
    modes = {str(p.relative_to(root)): oct(p.stat().st_mode & 0o777)
             for root in (shared, zipped) for p in root.rglob("*")}
    assert modes and {m for m in modes.values()} == {"0o600", "0o700"}, modes
    assert all(m == ("0o700" if (shared / k).is_dir() or (zipped / k).is_dir() else "0o600") for k, m in modes.items())
    assert oct(shared.stat().st_mode & 0o777) == "0o755"


def _stored(*rows):
    """Messages whose whole bytes were never fetched, so the export writes each from what is stored of it: each row
    a dict of `messages` columns over ada's INBOX."""
    from mailmind.bootstrap.mailbox import store
    c = store.conn()
    for uid, row in enumerate(rows, 1):
        row = {"account": "ada@example.test", "kind": "imap", "folder": "INBOX", "uid": uid, "body_fetched": 0, **row}
        c.execute(f"INSERT INTO messages ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})", list(row.values()))
    c.commit()
    c.close()


def _written(out):
    """The one .eml file under `out`, as a mail program reads it."""
    import email
    from email import policy
    [path] = list(out.rglob("*.eml"))
    return email.message_from_bytes(path.read_bytes(), policy=policy.default)


@pytest.mark.xfail(strict=True, reason="the stored date is ISO, which EmailMessage can't read as a Date: it is lost")
def test_a_message_written_from_its_row_keeps_its_date(data_dir):
    from datetime import datetime, timedelta, timezone
    from mailmind.platform.db import export
    _stored({"message_id": "<brief@example.test>", "date_iso": "2026-11-01T08:05:09-05:30", "subject": "Brief"})
    export.export_eml(str(data_dir / "out"))
    written = _written(data_dir / "out")
    assert written["Date"].datetime == datetime(2026, 11, 1, 8, 5, 9, tzinfo=timezone(-timedelta(hours=5, minutes=30)))


@pytest.mark.xfail(strict=True, reason="the sender's name goes in unquoted, so a comma in it makes two senders")
def test_a_sender_whose_name_holds_a_comma_is_one_sender(data_dir):
    from mailmind.platform.db import export
    _stored({"message_id": "<brief@example.test>", "from_addr": "jane@example.test", "from_name": "Doe, Jane"})
    export.export_eml(str(data_dir / "out"))
    sender = _written(data_dir / "out")["From"]
    assert [(a.display_name, a.addr_spec) for a in sender.addresses] == [("Doe, Jane", "jane@example.test")]


@pytest.mark.xfail(strict=True, reason="only CR and LF are made spaces, and EmailMessage refuses a header any other "
                                       "line break splits, which fails the whole export")
def test_a_stored_line_separator_in_a_header_is_a_space(data_dir):
    from mailmind.platform.db import export
    _stored({"message_id": "<brief@example.test>", "subject": "Brief two"})
    export.export_eml(str(data_dir / "out"))
    assert _written(data_dir / "out")["Subject"] == "Brief two"


@pytest.mark.xfail(strict=True, reason="each folder opens its .mbox afresh, so two whose names come to one file write "
                                       "over each other")
def test_two_folders_whose_names_come_to_one_mbox_file_share_it(data_dir):
    import mailbox
    from mailmind.platform.db import export
    _stored({"folder": "Clients/Acme", "message_id": "<one@example.test>", "subject": "One", "ts": 1},
            {"folder": "Clients_Acme", "message_id": "<two@example.test>", "subject": "Two, a longer one", "ts": 2})
    export.export_mbox(str(data_dir / "out"))
    [path] = list((data_dir / "out").rglob("*.mbox"))
    box = mailbox.mbox(path, create=False)
    try:
        assert sorted(m["Subject"] for m in box) == ["One", "Two, a longer one"]
    finally:
        box.close()
