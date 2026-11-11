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
