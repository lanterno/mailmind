"""export.py's read-only view of the database, wherever the data directory is."""
import pytest


@pytest.mark.parametrize("name", ["plain", "with space", "hash#mark", "what?", "per%cent"])
def test_the_export_reads_the_database_whatever_its_directory_is_called(tmp_path, name):
    import export
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
