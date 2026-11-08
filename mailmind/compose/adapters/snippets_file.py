"""mailmind_snippets.json: the words a person keeps for the composer, written as the saved views' file is."""
import json
import os
import tempfile

from mailmind.compose.domain.snippets import Snippet
from mailmind.platform import settings


def read() -> object:
    """What the file holds, or nothing if it can't be read. TOTAL: never raises."""
    try:
        with open(settings.current().snippets, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return []


def write(snippets: list[Snippet]) -> None:
    """Atomic: a temp file beside it, synced, its owner's alone, renamed over it."""
    path = settings.current().snippets
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".snippets-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(snippets, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
