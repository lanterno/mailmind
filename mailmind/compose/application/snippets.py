"""The words a person keeps for the composer, which /api/snippets lists and changes."""
from collections.abc import Mapping
import threading
from typing import Any

from mailmind.compose.application.ports import SnippetsFile
from mailmind.compose.domain import snippets
from mailmind.compose.domain.snippets import Snippet
from mailmind.kernel.errors import Refused


class Snippets:
    def __init__(self, file: SnippetsFile):
        self._file = file
        self._change = threading.Lock()                 # a change reads, changes and writes as one step: two tabs may ask at once

    def load(self) -> list[Snippet]:
        return snippets.clean(self._file.read())

    def listed(self) -> dict[str, Any]:
        return {"ok": True, "snippets": self.load()}

    def post(self, b: Mapping[str, Any]) -> dict[str, Any]:
        """A change /api/snippets asked for, and the snippets after it. Raises Refused for one it can't make."""
        verb = b.get("verb")
        if verb not in ("save", "delete"):
            raise Refused("verb must be save|delete")
        with self._change:
            now = self.load()
            after = snippets.saved(now, b.get("trigger"), b.get("text")) if verb == "save" else snippets.without(now, b.get("trigger"))
            if after is not None:
                self._file.write(after)
        return {"ok": True, "snippets": now if after is None else after}
