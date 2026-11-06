"""The saved views a person keeps. `python -m mailmind views` lists and changes them, and so does /api/views."""
from mailmind.discovery.domain import views


class SavedViews:
    def __init__(self, file):
        self._file = file

    def load(self):
        return views.clean(self._file.read())

    def merged(self, include_lanes=True):
        return views.merged(self.load(), include_lanes)

    def add(self, name, q):
        v = views.new(name, q)
        if v:
            self._file.write(self.load() + [v])
        return v

    def remove(self, view_id):
        if view_id in views.BUILTIN_IDS:        # refuse to delete a built-in / lane
            return False
        cur = self.load()
        kept = [v for v in cur if v["id"] != view_id]
        if len(kept) == len(cur):
            return False
        self._file.write(kept)
        return True

    def reorder(self, ids):
        self._file.write(views.reordered(self.load(), ids))
        return True

    def post(self, b):
        """A change /api/views asked for, and the views after it."""
        verb = b.get("verb")
        if verb == "create":
            if not self.add(b.get("name"), b.get("q")):
                return {"ok": False, "msg": "need a name and a query", "views": self.merged()}
        elif verb == "delete":
            self.remove(b.get("id"))            # no-op (returns False) for a builtin/lane
        elif verb == "reorder" and isinstance(b.get("ids"), list):
            self.reorder([str(x) for x in b["ids"]])
        else:
            return {"ok": False, "msg": "verb must be create|delete|reorder", "views": self.merged()}
        return {"ok": True, "views": self.merged()}
