"""/api/export: a snapshot of the database as a download, read in chunks, and its folder gone afterwards however that went. What it
holds, and the headers, are in tests/golden/http_get/.
"""
import asyncio
import os
import re
import socket
import tempfile
import threading
import time

import pytest
from anyio import from_thread, to_thread
from anyio.from_thread import start_blocking_portal

import schema_contract
from mailmind.http.routers import export as export_routes
from mailmind.platform.db import export

BIG = 3 * export_routes.CHUNK + 5


@pytest.fixture
def folders(monkeypatch):
    """The folder of each export the test made, with the real snapshot in it, so a test can see it go."""
    made, real = [], export.export_db

    def noting(out):
        made.append(out)
        return real(out)
    monkeypatch.setattr(export, "export_db", noting)
    return made


@pytest.fixture
def big_export(monkeypatch):
    """An export too big for any socket's buffers to take at once. Each folder it made is noted."""
    made = []

    def big(out):
        made.append(out)
        path = os.path.join(out, "mailmind-big.db")
        with open(path, "wb") as f:
            f.truncate(64 * 1024 * 1024)
        return path
    monkeypatch.setattr(export, "export_db", big)
    return made


def until(holds, seconds=5):
    deadline = time.monotonic() + seconds
    while not holds() and time.monotonic() < deadline:
        time.sleep(0.01)
    return holds()


def opened(app):
    """A client that asks for the export and reads no answer."""
    s = socket.create_connection(("127.0.0.1", app.port), timeout=10)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
    s.sendall(f"GET /api/export HTTP/1.1\r\nHost: 127.0.0.1:{app.port}\r\nSec-Fetch-Site: same-origin\r\n\r\n".encode())
    return s


def test_a_download_holds_the_snapshot_and_its_folder_goes_afterwards(app, folders):
    r = app.get("/api/export")
    assert r.status == 200 and r.body.startswith(b"SQLite format 3") and int(r.headers["content-length"]) == len(r.body)
    assert r.headers["content-type"] == "application/octet-stream"
    assert re.fullmatch(r"attachment; filename=mailmind-\d{8}T\d{6}Z\.db", r.headers["content-disposition"])
    assert len(folders) == 1 and until(lambda: not os.path.exists(folders[0]))


def test_a_failed_snapshot_is_the_fixed_failure_and_leaves_no_folder(app, monkeypatch, capsys):
    made = []

    def boom(out):
        made.append(out)
        raise OSError(13, "hunter2", "hunter2")
    monkeypatch.setattr(export, "export_db", boom)
    r = app.get("/api/export")
    assert (r.status, r.json) == (500, {"ok": False, "error": "request failed"})
    assert not os.path.exists(made[0])
    said = capsys.readouterr().out
    assert "[http] GET /api/export failed: PermissionError" in said and "hunter2" not in said


def test_a_client_that_hangs_up_midway_leaves_no_folder(app, big_export):
    s = opened(app)
    assert s.recv(4096).startswith(b"HTTP/1.1 200")
    assert os.path.exists(big_export[0])
    s.close()
    assert until(lambda: not os.path.exists(big_export[0]))


def test_a_client_that_hangs_up_during_the_snapshot_leaves_no_folder(app, monkeypatch):
    made, asked, go_on = [], threading.Event(), threading.Event()

    def slow(out):
        made.append(out)
        asked.set()
        go_on.wait(10)
        path = os.path.join(out, "mailmind-slow.db")
        with open(path, "wb") as f:
            f.write(b"x")
        return path
    monkeypatch.setattr(export, "export_db", slow)
    s = opened(app)
    assert asked.wait(5)
    s.close()
    go_on.set()
    assert until(lambda: made and not os.path.exists(made[0]))


def test_a_client_that_reads_slowly_holds_no_thread_of_the_pool(app, big_export, monkeypatch):
    def limiter():
        return from_thread.run_sync(lambda: to_thread.current_default_thread_limiter().borrowed_tokens)
    s = opened(app)
    try:
        assert s.recv(4096).startswith(b"HTTP/1.1 200")
        time.sleep(0.2)                                  # the socket is full, and the response waits on it
        monkeypatch.setattr(schema_contract, "HOLDING", False)
        monkeypatch.setattr(schema_contract.route_for("GET", "/api/stats"), "work", lambda: {"tokens": limiter()})
        assert app.get("/api/stats").json == {"tokens": 1}
    finally:
        s.close()


# ── the response itself ──────────────────────────────────────────────────────────────────────────────

def download(tmp_size):
    folder = tempfile.TemporaryDirectory(prefix="mm-test-export-")
    path = os.path.join(folder.name, "mailmind-test.db")
    with open(path, "wb") as f:
        f.write(b"x" * tmp_size)
    return export_routes._Download(folder, path), folder.name


def on_a_loop(work):
    """On a loop of its own, on a thread of its own: the test's may have one running already (Playwright's, under --browser)."""
    with start_blocking_portal() as portal:
        return portal.call(work)


SCOPE = {"type": "http", "asgi": {"spec_version": "2.3"}}


async def never_any_message():
    await asyncio.Event().wait()


def test_it_goes_in_chunks_with_only_the_one_being_sent_read():
    response, folder = download(BIG)
    read_when_sent = []
    bodies = []

    async def send(message):
        if message["type"] == "http.response.body":
            read_when_sent.append(None if response._file.closed else response._file.tell())
            bodies.append(message.get("body", b""))

    async def work():
        await response(SCOPE, never_any_message, send)
    on_a_loop(work)
    assert [len(b) for b in bodies] == [export_routes.CHUNK, export_routes.CHUNK, export_routes.CHUNK, 5, 0]
    assert read_when_sent == [export_routes.CHUNK, 2 * export_routes.CHUNK, 3 * export_routes.CHUNK, BIG, BIG]
    assert response._file.closed and not os.path.exists(folder)


def test_a_download_the_server_cancels_leaves_no_folder():
    """What uvicorn does to a task still running when the grace is over."""
    response, folder = download(BIG)

    async def work():
        writing = asyncio.Event()

        async def stuck(message):
            if message["type"] == "http.response.body":
                writing.set()
                await asyncio.Event().wait()
        task = asyncio.ensure_future(response(SCOPE, never_any_message, stuck))
        await asyncio.wait_for(writing.wait(), 3)
        task.cancel()
        await asyncio.wait([task], timeout=3)
        return task.done()
    assert on_a_loop(work)
    assert response._file.closed and not os.path.exists(folder)
