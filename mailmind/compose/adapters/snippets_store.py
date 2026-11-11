"""The words a person keeps for the composer, as the `snippets` document in the database
(mailmind.platform.db.documents): the list of snippets, in order of their triggers."""
from mailmind.compose.domain.snippets import Snippet
from mailmind.kernel.row import Connection
from mailmind.platform.db import documents

NAME = "snippets"


def read(c: Connection) -> object:
    """What is kept, as JSON parses it, or None when nothing is kept yet or it won't parse."""
    return documents.read(c, NAME)


def write(c: Connection, snippets: list[Snippet]) -> None:
    documents.write(c, NAME, snippets)
