"""/api/export: a snapshot of the database as a download, read in chunks, and its folder gone afterwards however that went. What it
holds, and the headers, are in tests/golden/http_get/. /api/export/archive: the mail in a standard format, the same way.
"""
import asyncio
import os
import tempfile

from anyio.from_thread import start_blocking_portal

from mailmind.http.routers import export as export_routes

BIG = 3 * export_routes.CHUNK + 5


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
