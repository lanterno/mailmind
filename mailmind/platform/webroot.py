"""The page and its files, as both front doors serve them.

The source in `web/` runs as it stands, classic scripts and plain CSS, so `python3 server.py` serves the
app with nothing installed and nothing built. `just build-web` writes the same files, named by their
content, to `mailmind/http/dist`. That is what gets served while it is at least as new as every source
file, so an edit or a checkout never leaves the app on old files.

The React app at /next exists only as built: `web/src` doesn't run as it stands.

Standard library only: python3 server.py uses it.
"""
import dataclasses
import gzip
import hashlib
import os

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SOURCE = os.path.join(REPO, "web")
DIST = os.path.join(REPO, "mailmind", "http", "dist")
PREFIX = "/assets/"
TYPES = {".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".woff2": "font/woff2"}
_COMPRESSED = {".css", ".js"}
_FOREVER, _ASK_FIRST = "public, max-age=31536000, immutable", "no-cache"


@dataclasses.dataclass(frozen=True)
class Asset:
    body: bytes
    gzipped: bytes | None     # the body compressed, for a type worth compressing
    ctype: str
    etag: str
    cache: str


_read = {}                    # path -> (size and modification time, Asset)
_builds = {}                  # what the files were -> the build id they make


def _files(root):
    """Each file the page is made of under `root`, leaving out the licences and anything else not served."""
    found = [os.path.join(root, "index.html")]
    for folder, _, names in os.walk(os.path.join(root, "assets")):
        found += [os.path.join(folder, n) for n in names if os.path.splitext(n)[1] in TYPES]
    return sorted(p for p in found if os.path.isfile(p))


def _root():
    index = os.path.join(DIST, "index.html")
    if not os.path.isfile(index):
        return SOURCE
    newest = max(os.path.getmtime(p) for p in _files(SOURCE))
    return DIST if os.path.getmtime(index) >= newest else SOURCE


def _stamp(path):
    st = os.stat(path)
    return st.st_size, st.st_mtime_ns


def build():
    """A name for what is being served now, which a tab compares with the one it loaded under."""
    root = _root()
    files = _files(root)
    seen = tuple((p, *_stamp(p)) for p in files)
    if seen not in _builds:
        digest = hashlib.sha256()
        for p in files:
            digest.update(os.path.relpath(p, root).encode() + b"\0")
            with open(p, "rb") as f:
                digest.update(f.read())
        _builds.clear()
        _builds[seen] = digest.hexdigest()[:16]
    return _builds[seen]


def page(csrf):
    with open(os.path.join(_root(), "index.html"), encoding="utf-8") as f:
        return f.read().replace("__CSRF_TOKEN__", csrf).replace("__BUILD__", build())


def next_page(csrf):
    """The React app's page, or None when nothing has built it."""
    path = os.path.join(DIST, "next.html")
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as f:
        return f.read().replace("__CSRF_TOKEN__", csrf).replace("__BUILD__", build())


def asset(url_path):
    """The Asset at a URL path under PREFIX, or None for anything that isn't one of the page's files."""
    relative = url_path.removeprefix(PREFIX)
    ext = os.path.splitext(relative)[1]
    if ext not in TYPES or "\\" in relative or any(not p or p.startswith(".") for p in relative.split("/")):
        return None
    root = _root()
    path = os.path.join(root, "assets", *relative.split("/"))
    if not os.path.isfile(path) and root != DIST:     # one of /next's, which only a build has
        root = DIST
        path = os.path.join(root, "assets", *relative.split("/"))
    if not os.path.isfile(path) or not os.path.realpath(path).startswith(os.path.realpath(root) + os.sep):
        return None
    stamp = _stamp(path)
    known = _read.get(path)
    if known and known[0] == stamp:
        return known[1]
    with open(path, "rb") as f:
        body = f.read()
    found = Asset(body, gzip.compress(body, 6, mtime=0) if ext in _COMPRESSED else None, TYPES[ext],
                  f'W/"{hashlib.sha256(body).hexdigest()[:24]}"', _FOREVER if root == DIST else _ASK_FIRST)
    _read[path] = (stamp, found)
    return found
