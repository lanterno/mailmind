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
