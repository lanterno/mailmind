"""The words a person keeps for the composer, which /api/snippets lists and changes."""
from collections.abc import Mapping
from typing import Any

from mailmind.compose.application.ports import SnippetsStore
from mailmind.compose.domain import snippets
from mailmind.compose.domain.snippets import Snippet
from mailmind.kernel.errors import Refused
from mailmind.platform.db.uow import UnitOfWork


class Snippets:
    """A change reads the snippets, changes them and writes them in one write block, so two at once, from two tabs, lose
    neither."""

    def __init__(self, uow: UnitOfWork, store: SnippetsStore):
        self._uow = uow
        self._store = store

    def load(self) -> list[Snippet]:
        with self._uow.read() as c:
            return snippets.clean(self._store.read(c))

    def listed(self) -> dict[str, Any]:
        return {"ok": True, "snippets": self.load()}

    def post(self, b: Mapping[str, Any]) -> dict[str, Any]:
        """A change /api/snippets asked for, and the snippets after it. Raises Refused for one it can't make."""
        verb = b.get("verb")
        if verb not in ("save", "delete"):
            raise Refused("verb must be save|delete")
        with self._uow.write() as c:
            now = snippets.clean(self._store.read(c))
            after = snippets.saved(now, b.get("trigger"), b.get("text")) if verb == "save" else snippets.without(now, b.get("trigger"))
            if after is not None:
                self._store.write(c, after)
        return {"ok": True, "snippets": now if after is None else after}
