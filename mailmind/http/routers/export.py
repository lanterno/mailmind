"""What an export would hold, and as a download a snapshot of the database or the mail in a standard format."""
import os
import tempfile
from typing import Annotated

from fastapi import Query
from starlette.responses import StreamingResponse

from mailmind.http import route
from mailmind.http.schemas.common import NotJson, answers
from mailmind.http.schemas.export import ExportArgs, ExportPlan
from mailmind.platform.db import export

CHUNK = 262144


class _Download(StreamingResponse):
    """A file in chunks, so a large database never sits in memory: each read on a thread, and none held while a client is slow.
    Its folder goes with the response, however that ends."""

    def __init__(self, folder, path, kind="application/octet-stream"):
        self._folder = folder
        self._file = open(path, "rb")
        super().__init__(iter(lambda: self._file.read(CHUNK), b""),
                         headers={"Content-Type": kind, "Content-Length": str(os.path.getsize(path)),
                                  "Content-Disposition": "attachment; filename=" + os.path.basename(path)})

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            self._file.close()
            self._folder.cleanup()      # here on the loop, since a cancelled task can't wait for a thread: it is one unlink


def router():
    r = route.router()

    @r.get("/api/export/plan", responses=answers(ExportPlan))
    def plan():
        return export.plan()

    @r.get("/api/export", responses=answers(NotJson))
    def snapshot():
        """A consistent SQLite snapshot (checkpoint + .backup), read-only: it can't touch the live database. Its folder holds a
        copy of the mail, and one no response came to own still goes when the process does (`TemporaryDirectory`)."""
        folder = tempfile.TemporaryDirectory(prefix="mm-export-", ignore_cleanup_errors=True)
        try:
            return _Download(folder, export.export_db(folder.name))
        except Exception:
            folder.cleanup()
            raise

    @r.get("/api/export/archive", responses=answers(NotJson))
    def archive(args: Annotated[ExportArgs, Query()]):
        """Every synced message as a zip of .eml files or of an mbox per folder, for any other mail program. Read-only, as the
        snapshot is, and it holds a copy of the mail in a folder that goes the same way."""
        folder = tempfile.TemporaryDirectory(prefix="mm-export-", ignore_cleanup_errors=True)
        try:
            return _Download(folder, export.archive(folder.name, args.format), "application/zip")
        except Exception:
            folder.cleanup()
            raise

    return r
