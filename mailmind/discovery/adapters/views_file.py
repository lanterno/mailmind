"""mailmind_views.json: the user's saved views, with the atomic-write discipline of config.save(). Modeled on the
rule book's file."""
import json
import os
import tempfile

from mailmind.platform import settings


def read():
    """What the file holds, or nothing if it can't be read. TOTAL: never raises."""
    path = settings.current().views
    if not os.path.exists(path):
        return []
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return []


def write(user_views):
    """Atomic write (tempfile + fsync + chmod 600 + os.replace), persisting ONLY user views."""
    blob = json.dumps(user_views, indent=2, ensure_ascii=False)
    json.loads(blob)
    path = settings.current().views
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".views-", suffix=".tmp")
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
