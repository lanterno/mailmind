"""The event stream as the server runs it: what a tab holds, and every way a stream ends. What a stream says is
tests/characterization/test_http_events.py, and the hub's own part tests/unit/platform/test_sse.py.
"""
import asyncio
import dataclasses
import http.client
import json
import socket
import threading
import time

import pytest
from anyio import from_thread, to_thread
from anyio.from_thread import start_blocking_portal

import app_client
import problems
from asgi_server import Uvicorn
from mailmind.http import route
from mailmind.http.app import create_app
from mailmind.http.routers import events
from mailmind.http.routers.events import _Stream
from mailmind.http.schemas.common import Answer, answers
from mailmind.platform import settings, sse, webroot
from waiting import blocked_in, wait_until

GRACE = 5           # long enough to notice anything that waits it out


class Said(Answer):
    tokens: int | None = None


helpers = route.router()
on_the_way = threading.Event()         # what /api/slow waits for
arrived = threading.Event()


@helpers.get("/api/pool", responses=answers(Said))
def pool():
    return {"tokens": from_thread.run_sync(lambda: to_thread.current_default_thread_limiter().borrowed_tokens)}


@helpers.get("/api/slow", responses=answers(Said))
def slow():
    arrived.set()
    on_the_way.wait(10)
    return {}


def serving(grace=GRACE):
    return Uvicorn(create_app(routers=[events.router(), helpers], token=lambda: app_client.CSRF, body_cap=lambda method, path: 0),
                   grace=grace).start()


@pytest.fixture
def server(data_dir, monkeypatch):
    monkeypatch.setattr(sse, "HUB", sse.Hub())
    arrived.clear()
    on_the_way.clear()
    running = serving()
    try:
        with settings.use(dataclasses.replace(settings.current(), port=running.port)):
            yield running
    finally:
        on_the_way.set()
        if running.thread.is_alive():
            running.stop()


class Tab:
    def __init__(self, port):
        self.conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        self.conn.request("GET", "/api/events", headers={"Host": f"127.0.0.1:{port}", "Sec-Fetch-Site": "same-origin"})
        self.answer = self.conn.getresponse()
        assert self.answer.status == 200

    def hears(self, line):
        """Reads up to `line`. A stream that doesn't say it fails on the connection's timeout."""
        while self.answer.readline() != line:
            pass
        return True

    def hang_up(self):
        self.answer.close()
        self.conn.close()


def get(port, path):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=15)
    try:
        conn.request("GET", path, headers={"Host": f"127.0.0.1:{port}", "Sec-Fetch-Site": "same-origin"})
        r = conn.getresponse()
        return r.status, r.read()
    finally:
        conn.close()


def test_the_opening_comes_at_once_and_a_frame_when_there_is_one(server):
    tab = Tab(server.port)
    assert tab.answer.readline() == b"retry: 3000\n"     # with nothing published, before the keepalive's 20 s
    assert tab.answer.readline().startswith(b"id: ")
    sse.publish("outbox", {})
    assert tab.hears(b"event: outbox\n")
    tab.hang_up()


def test_a_tab_holds_nothing_of_the_pool(server):
    """Requests waiting on IMAP share it. Every tab the hub lets listen is open, and only this request has a token."""
    tabs = [Tab(server.port) for _ in range(sse.MAX_LISTENERS)]
    assert sse.HUB.listening() == sse.MAX_LISTENERS
    assert get(server.port, "/api/pool") == (200, b'{"tokens": 1}')
    for tab in tabs:
        tab.hang_up()


def test_a_tab_that_hangs_up_frees_its_listener_long_before_a_keepalive(server, monkeypatch):
    assert sse.HEARTBEAT >= 20
    monkeypatch.setattr(sse, "HEARTBEAT", 3600)          # so no keepalive finds the hang-up while the test waits
    tab = Tab(server.port)
    assert sse.HUB.listening() == 1
    tab.hang_up()
    assert wait_until(lambda: sse.HUB.listening() == 0)
    assert Tab(server.port).answer.status == 200         # its place is free


def test_a_failure_before_the_stream_is_the_fixed_failure_with_no_listener_held(server, monkeypatch, capsys):
    def boom(token):
        raise RuntimeError("hunter2")
    monkeypatch.setattr(webroot, "edition", boom)
    status, body = get(server.port, "/api/events")
    assert (status, json.loads(body)) == (500, problems.FAILED)
    assert sse.HUB.listening() == 0 and "hunter2" not in capsys.readouterr().out


# ── however the response ends, the listener goes back ──────────────────────────────────────────────

async def running(send, receive):
    """The response as the server runs it, as a task of its own."""
    return asyncio.ensure_future(_Stream(None, "a-build")({"type": "http", "asgi": {"spec_version": "2.3"}}, receive, send))


async def never_any_message():
    await asyncio.Event().wait()


async def sends_fine(message):
    pass


async def settled(task):
    """However the task ended, it has."""
    await asyncio.wait([task], timeout=30)
    assert task.done()


async def waiting_for_an_event():
    """Once the stream's wait for its next event has begun, on a thread of AnyIO's."""
    async def waits():
        while not any(blocked_in(t, sse.Listener.next) for t in threading.enumerate()):
            await asyncio.sleep(0.005)
    await asyncio.wait_for(waits(), 30)


@pytest.fixture
def hub(monkeypatch):
    monkeypatch.setattr(sse, "HUB", sse.Hub())
    return sse.HUB


def on_a_loop(work):
    """On a loop of its own, on a thread of its own: the test's may have one running already (Playwright's, under --browser)."""
    with start_blocking_portal() as portal:
        return portal.call(work)


def test_a_tab_that_hangs_up_during_a_write_gives_it_back(hub):
    """The generator is left waiting at its `yield`, so a `finally` of its own wouldn't run until it was collected."""
    async def work():
        writing, hung_up = asyncio.Event(), asyncio.Event()

        async def stuck_on_the_first_frame(message):
            if message["type"] == "http.response.body":
                writing.set()
                await asyncio.Event().wait()

        async def receive():
            await hung_up.wait()
            return {"type": "http.disconnect"}
        task = await running(stuck_on_the_first_frame, receive)
        await asyncio.wait_for(writing.wait(), 30)
        assert hub.listening() == 1
        hung_up.set()
        await settled(task)
        return hub.listening()
    assert on_a_loop(work) == 0


def test_a_tab_that_hung_up_before_the_first_frame_gives_it_back(hub):
    async def work():
        async def gone():
            return {"type": "http.disconnect"}
        await settled(await running(sends_fine, gone))
        return hub.listening()
    assert on_a_loop(work) == 0


def test_a_stream_the_server_cancels_gives_it_back(hub):
    """What uvicorn does to a task still running when the grace is over."""
    async def work():
        started = asyncio.Event()

        async def noting(message):
            started.set()
        task = await running(noting, never_any_message)
        await asyncio.wait_for(started.wait(), 30)
        await waiting_for_an_event()
        task.cancel()
        await settled(task)
        return hub.listening()
    assert on_a_loop(work) == 0


def test_the_hub_ending_a_stream_ends_the_body_too(hub):
    async def work():
        messages = []

        async def noting(message):
            messages.append(message)
        task = await running(noting, never_any_message)
        await waiting_for_an_event()
        hub.end_streams()
        await settled(task)
        return messages[-1], hub.listening()
    assert on_a_loop(work) == ({"type": "http.response.body", "body": b"", "more_body": False}, 0)


# ── a server that stops ──────────────────────────────────────────────────────────────────────────────

def test_shutting_down_ends_an_open_stream_at_once(server):
    """Not when the grace runs out: the stream would be cut off then, and reading it would raise IncompleteRead."""
    tab = Tab(server.port)
    assert tab.answer.readline() == b"retry: 3000\n"
    server.stop()
    tab.answer.read()                                    # ended properly
    assert sse.HUB.listening() == 0
    tab.hang_up()


def test_shutting_down_lets_a_request_still_being_answered_finish(server, monkeypatch):
    got = []
    client = threading.Thread(target=lambda: got.append(get(server.port, "/api/slow")))
    client.start()
    assert arrived.wait(30)
    ending = sse.HUB.end_streams
    monkeypatch.setattr(sse.HUB, "end_streams", lambda: (ending(), on_the_way.set()))     # the answer comes once shutting down has begun
    server.stop()
    client.join(30)
    assert got == [(200, b"{}")]


def peek(sock):
    """What has come to `sock` so far, left unread."""
    try:
        return sock.recv(65536, socket.MSG_PEEK | socket.MSG_DONTWAIT)
    except BlockingIOError:
        return b""


def test_a_tab_that_stopped_reading_holds_the_server_no_longer_than_its_grace(data_dir, monkeypatch):
    """The hub can't end a stream waiting on a full socket, so it runs out the grace and is cancelled, and it is the cancelling
    that gives its listener back."""
    monkeypatch.setattr(sse, "HUB", sse.Hub())
    grace = 0.5
    running_server = serving(grace)
    with settings.use(dataclasses.replace(settings.current(), port=running_server.port)):
        silent = socket.socket()
        silent.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
        silent.connect(("127.0.0.1", running_server.port))
        silent.sendall(f"GET /api/events HTTP/1.1\r\nHost: 127.0.0.1:{running_server.port}\r\nSec-Fetch-Site: same-origin\r\n\r\n".encode())
        try:
            assert wait_until(lambda: sse.HUB.listening() == 1)
            for _ in range(100):
                sse.publish("outbox", {"more": "x" * 1_000_000})
            assert wait_until(lambda: b"event: outbox" in peek(silent))     # the frame is on its way, as far as it will go
            began = time.monotonic()
            running_server.stop()                        # which fails if the server is still up 15 s on
            assert time.monotonic() - began >= grace * 0.8                # it waited out the grace
            assert sse.HUB.listening() == 0
        finally:
            silent.close()


def test_a_server_can_start_again_in_the_same_process(server):
    """The hub isn't closed for good by a server stopping, as tests build many apps and each one's streams must work."""
    tab = Tab(server.port)
    server.stop()
    tab.hang_up()
    again = serving()
    try:
        with settings.use(dataclasses.replace(settings.current(), port=again.port)):
            next_tab = Tab(again.port)
            assert next_tab.answer.readline() == b"retry: 3000\n"
            assert sse.HUB.listening() == 1
            sse.publish("outbox", {})
            assert next_tab.hears(b"event: outbox\n")
            next_tab.hang_up()
    finally:
        again.stop()
