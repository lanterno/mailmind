"""Files in the data directory that hold what is in someone's mail or what was done with it: readable by their owner
only, as the database, the rules, views and snippets files and the log are."""
import os
from typing import TextIO


def append(path: str) -> TextIO:
    """`path` opened to append text, its owner's alone: made so when new, and brought into line when an older MailMind
    left it readable by others."""
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        os.fchmod(fd, 0o600)
        return os.fdopen(fd, "a", encoding="utf-8")
    except BaseException:
        os.close(fd)
        raise
