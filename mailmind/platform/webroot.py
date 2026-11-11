"""The app's page and its files, as `just build-web` wrote them to `mailmind/http/dist`.

The app exists only as built: `web/src` doesn't run as it stands. With nothing built there is no page to serve, and `page` and
`is_built` say so. A build names each file by its content, so a file is served for good, and a new build is a new `edition`.
"""
import dataclasses
import gzip
import hashlib
import os
from html import escape

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DIST = os.path.join(REPO, "mailmind", "http", "dist")
PREFIX = "/assets/"
TYPES = {".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".woff2": "font/woff2"}
FOREVER = "public, max-age=31536000, immutable"
_COMPRESSED = {".css", ".js"}


@dataclasses.dataclass(frozen=True)
class Asset:
    body: bytes
    gzipped: bytes | None     # the body compressed, for a type worth compressing
    ctype: str
    etag: str


_read = {}                    # path -> (size and modification time, Asset)
_builds = {}                  # what the files were -> the build id they make


def _index():
    return os.path.join(DIST, "index.html")


def is_built():
    return os.path.isfile(_index())


def _files():
    """Each file of the build the page is made of, leaving out the licences and anything else not served."""
    found = [_index()]
    for folder, _, names in os.walk(os.path.join(DIST, "assets")):
        found += [os.path.join(folder, n) for n in names if os.path.splitext(n)[1] in TYPES]
    return sorted(p for p in found if os.path.isfile(p))


def _stamp(path):
    st = os.stat(path)
    return st.st_size, st.st_mtime_ns


def build():
    """A name for what is being served now, which a tab compares with the one it loaded under. It goes by the files' content,
    so a build that made the same files changes nothing. With nothing built it names that."""
    files = _files()
    seen = tuple((p, *_stamp(p)) for p in files)
    if seen not in _builds:
        digest = hashlib.sha256()
        for p in files:
            digest.update(os.path.relpath(p, DIST).encode() + b"\0")
            with open(p, "rb") as f:
                digest.update(f.read())
        _builds.clear()
        _builds[seen] = digest.hexdigest()[:16]
    return _builds[seen]


def edition(csrf):
    """What a tab compares with what it loaded under: the files, and the token it was given, which a restart changes, so a
    tab that would be refused from now on offers a reload as one on old files does. Only a hash of the token goes out."""
    return f"{build()}-{hashlib.sha256(csrf.encode()).hexdigest()[:8]}"


def page(csrf, place=None):
    """The app's page holding this run's token, or None when nothing has built it. `place` is the route a place holds, as its
    JSON text, which the page reads where it is from (`<meta name="mailmind-place">`, empty for none): escaped for an attribute,
    and put in last, so nothing a person wrote in it is read as the token or the build."""
    if not is_built():
        return None
    with open(_index(), encoding="utf-8") as f:
        return (f.read().replace("__CSRF_TOKEN__", csrf).replace("__BUILD__", edition(csrf))
                .replace("__PLACE__", escape(place or "", quote=True)))


def asset(url_path):
    """The Asset at a URL path under PREFIX, or None for anything that isn't one of the page's files."""
    relative = url_path.removeprefix(PREFIX)
    ext = os.path.splitext(relative)[1]
    if ext not in TYPES or "\\" in relative or any(not p or p.startswith(".") for p in relative.split("/")):
        return None
    path = os.path.join(DIST, "assets", *relative.split("/"))
    if not os.path.isfile(path) or not os.path.realpath(path).startswith(os.path.realpath(DIST) + os.sep):
        return None
    stamp = _stamp(path)
    known = _read.get(path)
    if known and known[0] == stamp:
        return known[1]
    with open(path, "rb") as f:
        body = f.read()
    found = Asset(body, gzip.compress(body, 6, mtime=0) if ext in _COMPRESSED else None, TYPES[ext],
                  f'W/"{hashlib.sha256(body).hexdigest()[:24]}"')
    _read[path] = (stamp, found)
    return found
