"""The saved views a person keeps, as the `views` document in the database (mailmind.platform.db.documents): the list of
views the page and `python -m mailmind views` made, builtins left out."""
from mailmind.discovery.domain.views import View
from mailmind.kernel.row import Connection
from mailmind.platform.db import documents

NAME = "views"


def read(c: Connection) -> object:
    """What is kept, as JSON parses it, or None when nothing is kept yet or it won't parse."""
    return documents.read(c, NAME)


def write(c: Connection, user_views: list[View]) -> None:
    documents.write(c, NAME, user_views)
