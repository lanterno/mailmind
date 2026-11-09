"""Opening the database and bringing it up to date. Every other function in the store takes its caller's connection and leaves the commit to it."""
from mailmind.mailbox.adapters.sqlite.messages import repair_keys
from mailmind.platform import settings
from mailmind.platform.db import database, migrate
from mailmind.platform.db.uow import UnitOfWork


def conn():
    return database.connect(settings.current().db)


def init():
    """Bring the data directory's database to the newest schema (see mailmind.platform.db)."""
    migrate.init(settings.current().db)
    with UnitOfWork(conn).write() as c:
        repair_keys(c)
