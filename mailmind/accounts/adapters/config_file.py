"""mailmind.config.json, parsed once per change.

The accounts, their credentials (or where to find them) and the top-level settings live in this
one file, which the app consults several times for each request. `ConfigFile.read` parses it
only when it has changed since the last parse: another inode (a save replaces the file), another
size or another modification time (an edit in place). Each read returns a copy of its own, so a
caller may change what it got before handing it to `write`.

`write` replaces the file atomically: the new text is written to a temporary file beside it,
flushed to disk and made readable by its owner only, then renamed over the old one, so a crash
never leaves a partial or world-readable file.

Standard library only.
"""
import copy
import json
import os
import tempfile
import threading

from mailmind.platform import settings

EMPTY = {"accounts": []}


class ConfigFile:
    def __init__(self):
        self._lock = threading.Lock()
        self._seen = None             # (path, inode, size, mtime) of the parse kept in _raw
        self._raw = None

    def read(self):
        """The file's contents as a dict of the caller's own, or EMPTY's copy when there is no
        file. A file that isn't valid JSON raises, every time it is read."""
        path = settings.current().config
        try:
            st = os.stat(path)
        except FileNotFoundError:
            return copy.deepcopy(EMPTY)
        seen = (path, st.st_ino, st.st_size, st.st_mtime_ns)
        with self._lock:
            if seen != self._seen:
                self._raw, self._seen = self._parse(path), seen
            return copy.deepcopy(self._raw)

    def _parse(self, path):
        with open(path) as f:
            return json.load(f)

    def write(self, raw):
        """Replace the file with `raw`, atomically and readable by its owner only."""
        if not (isinstance(raw, dict) and isinstance(raw.get("accounts"), list)):   # not an assert: -O strips those
            raise ValueError("bad config shape")
        blob = json.dumps(raw, indent=2, ensure_ascii=False)
        json.loads(blob)  # round-trip validation
        path = settings.current().config
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".cfg-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                f.write(blob)
                f.flush()
                os.fsync(f.fileno())
            os.chmod(tmp, 0o600)
            os.replace(tmp, path)
        except Exception:
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise


FILE = ConfigFile()
