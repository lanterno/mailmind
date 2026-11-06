"""What an export would hold, and a snapshot of the database as a download."""
import os
import shutil
import tempfile

from mailmind.http.routing import ANSWERED, Routes
from mailmind.platform.db import export
from mailmind.platform.logging import get_logger

log = get_logger("export")


def _snapshot(h):
    """Stream a consistent SQLite snapshot (checkpoint + .backup) as a download, chunked so a
    large DB never buffers in memory. Read-only: cannot touch the live DB. Temp file removed after."""
    tmp = tempfile.mkdtemp(prefix="mm-export-")
    try:
        path = export.export_db(tmp)
        size = os.path.getsize(path)
        h.send_response(200)
        h.send_header("Content-Type", "application/octet-stream")
        h.send_header("Content-Length", str(size))
        h.send_header("Content-Disposition",
                         "attachment; filename=" + os.path.basename(path))
        h.end_headers()
        with open(path, "rb") as f:
            while True:
                chunk = f.read(262144)
                if not chunk:
                    break
                h.wfile.write(chunk)
    except Exception as e:
        log.error("db error", exc_info=e)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def routes():
    r = Routes()

    @r.get("/api/export/plan")
    def plan(req):
        return export.plan(req.arg("account"))

    @r.get("/api/export")
    def snapshot(req):
        _snapshot(req.handler)
        return ANSWERED

    return r
