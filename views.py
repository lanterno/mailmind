"""Saved searches ("Views") — named, pinned search-grammar queries shown in the folder rail.

A view is just {id, name, q} where q is a raw search-grammar string (the same grammar /api/search
parses), so a saved search and an auto-label "lane" are the same thing. Built-in DEFAULTS (and
caller-registered lanes) are merged in read-only at load time: they always appear, can't be deleted,
and are never written to disk. User views persist to mailmind_views.json with the atomic-write
discipline from config.save(). Modeled on rules.py.

  python3 views.py list
  python3 views.py add "Unread from Stripe" "from:stripe is:unread"
  python3 views.py remove <id>
"""
import os
import sys
import json
import tempfile
import secrets

from mailmind.platform import settings

# A path pinned here wins over the settings'. Only the legacy test suites still pin one.
HERE = None                 # where save() writes its temp file; None: beside the views file
FILE = None


def _file():
    return FILE or settings.current().views

# Built-in smart defaults (always present, non-deletable). q uses the live search grammar; the
# __LAST7__ sentinel is expanded to after:<date> client-side so the FIXED grammar stays untouched.
DEFAULTS = (
    {"id": "__unread__", "name": "Unread", "q": "is:unread"},
    {"id": "__flagged__", "name": "Flagged", "q": "is:flagged"},
    {"id": "__attach__", "name": "Has attachments", "q": "has:attachment"},
    {"id": "__last7__", "name": "Last 7 days", "q": "__LAST7__", "dynamic": "last7"},
)
# Auto-label lanes (registered by the lanes feature) — also built-in, non-deletable, non-persisted.
LANES = (
    {"id": "__lane_feed__", "name": "Feed", "q": "is:newsletter"},
    {"id": "__lane_papertrail__", "name": "Paper trail", "q": "is:receipt"},
    {"id": "__lane_notifications__", "name": "Notifications", "q": "is:notification"},
    {"id": "__lane_action__", "name": "Action needed", "q": "category:action-needed"},
    {"id": "__lane_finance__", "name": "Finance", "q": "category:finance"},
)
_BUILTIN_IDS = {v["id"] for v in DEFAULTS} | {v["id"] for v in LANES}


def load():
    """User views only (no builtins). TOTAL — never raises, drops malformed entries."""
    path = _file()
    if not os.path.exists(path):
        return []
    try:
        with open(path) as f:
            raw = json.load(f)
    except Exception:
        return []
    out = []
    for v in (raw if isinstance(raw, list) else []):
        if not isinstance(v, dict):
            continue
        vid, name, q = str(v.get("id", "")), str(v.get("name", "")).strip(), str(v.get("q", "")).strip()
        if not vid or not name or not q or vid in _BUILTIN_IDS:
            continue
        out.append({"id": vid, "name": name[:60], "q": q[:512]})
    return out


def merged(include_lanes=True):
    """What the API + client see: DEFAULTS, then lanes, then user views — builtins flagged."""
    builtins = list(DEFAULTS) + (list(LANES) if include_lanes else [])
    return [dict(v, builtin=True) for v in builtins] + [dict(v, builtin=False) for v in load()]


def save(user_views):
    """Atomic write (tempfile + fsync + chmod 600 + os.replace), persisting ONLY user views."""
    blob = json.dumps(user_views, indent=2, ensure_ascii=False)
    json.loads(blob)
    path = _file()
    fd, tmp = tempfile.mkstemp(dir=HERE or os.path.dirname(path), prefix=".views-", suffix=".tmp")
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


def add(name, q):
    name, q = (name or "").strip()[:60], (q or "").strip()[:512]
    if not name or not q:
        return None
    v = {"id": "v" + secrets.token_hex(4), "name": name, "q": q}
    cur = load()
    cur.append(v)
    save(cur)
    return v


def remove(view_id):
    if view_id in _BUILTIN_IDS:        # refuse to delete a built-in / lane
        return False
    cur = load()
    kept = [v for v in cur if v["id"] != view_id]
    if len(kept) == len(cur):
        return False
    save(kept)
    return True


def reorder(ids):
    cur = {v["id"]: v for v in load()}
    ordered = [cur[i] for i in ids if i in cur] + [v for k, v in cur.items() if k not in ids]
    save(ordered)
    return True


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "list"
    if cmd == "list":
        for v in merged():
            print(f"  {'*' if v['builtin'] else ' '} {v['id']:18} {v['name']:24} {v['q']}")
    elif cmd == "add" and len(sys.argv) > 3:
        print(add(sys.argv[2], sys.argv[3]))
    elif cmd == "remove" and len(sys.argv) > 2:
        print("removed" if remove(sys.argv[2]) else "not removed (builtin or missing)")
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
