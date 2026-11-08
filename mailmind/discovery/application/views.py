"""The saved views a person keeps. `python -m mailmind views` lists and changes them, and so does /api/views."""
from collections.abc import Mapping
from typing import Any

from mailmind.discovery.application.ports import ViewsFile
from mailmind.discovery.domain import views
from mailmind.discovery.domain.views import ListedView, View
from mailmind.kernel.errors import Refused


class SavedViews:
    def __init__(self, file: ViewsFile):
        self._file = file

    def load(self) -> list[View]:
        return views.clean(self._file.read())

    def merged(self, include_lanes: bool = True) -> list[ListedView]:
        return views.merged(self.load(), include_lanes)

    def add(self, name: str | None, q: str | None) -> View | None:
        v = views.new(name, q)
        if v:
            self._file.write(self.load() + [v])
        return v

    def remove(self, view_id: str | None) -> bool:
        kept = views.without(self.load(), view_id)
        if kept is None:
            return False
        self._file.write(kept)
        return True

    def post(self, b: Mapping[str, Any]) -> dict[str, Any]:
        """A change /api/views asked for, and the views after it. Raises Refused for one it can't make."""
        if any(not isinstance(b.get(name), str | None) for name in ("id", "name", "q")):
            raise Refused()
        verb = b.get("verb")
        if verb == "create":
            if not self.add(b.get("name"), b.get("q")):
                raise Refused("need a name and a query")
        elif verb == "delete":
            self.remove(b.get("id"))            # no-op (returns False) for a builtin/lane
        else:
            raise Refused("verb must be create|delete")
        return {"ok": True, "views": self.merged()}
