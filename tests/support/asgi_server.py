"""An ASGI app on uvicorn, on a random loopback port, serving from a background thread: the app's
server the way the tests run it."""
import asyncio
import contextlib
import socket
import threading
import time

from mailmind.http import serving


class _Server(serving.Server):
    """The app's server, there the moment it has started and woken the moment it is told to stop. uvicorn's own main loop looks
    for should_exit once a tenth of a second, and the app fixture stops a server after every test."""

    def __init__(self, config):
        super().__init__(config)
        self.up = threading.Event()
        self._loop = self._stopping = None

    async def startup(self, sockets=None):
        self._loop, self._stopping = asyncio.get_running_loop(), asyncio.Event()
        try:
            await super().startup(sockets=sockets)
        finally:
            self.up.set()

    async def main_loop(self):
        should_exit = await self.on_tick(0)
        while not should_exit:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._stopping.wait(), 0.1)
            should_exit = await self.on_tick(0)

    async def shutdown(self, sockets=None):
        """uvicorn's shutdown asks each open connection to close and then always waits a tenth of a second for them to. With
        none open and no request still running, there is nothing to wait for, so this closes up without that pause; with one,
        it is uvicorn's shutdown (and the app's: event streams end at once) as `serve` runs it. A connection the test's client
        has just closed is still open here for a millisecond or so, so that much is waited for first."""
        for server in self.servers:
            server.close()                               # no connection comes in from here on
        deadline = self._loop.time() + 0.02
        while self.server_state.connections and self._loop.time() < deadline:
            await asyncio.sleep(0.001)
        if self.server_state.connections or self.server_state.tasks:
            await super().shutdown(sockets=sockets)
            return
        for sock in sockets or []:
            sock.close()
        for server in self.servers:
            await server.wait_closed()
        if not self.force_exit:
            await self.lifespan.shutdown()

    def stop(self):
        """From another thread: shutdown begins at once."""
        self.should_exit = True
        if self._loop is not None:
            with contextlib.suppress(RuntimeError):          # its loop has closed already
                self._loop.call_soon_threadsafe(self._stopping.set)


class Uvicorn:
    def __init__(self, app, name="app-http", grace=5, port=0):
        # A port of its own unless told one: a server the API suite restarts comes back where its client calls.
        self.sock = socket.create_server(("127.0.0.1", port))
        self.port = self.sock.getsockname()[1]
        self.server = _Server(serving.config(app, port=self.port, grace=grace))
        self.thread = threading.Thread(target=self.server.run, kwargs={"sockets": [self.sock]},
                                       name=name, daemon=True)

    def start(self):
        self.thread.start()
        deadline = time.monotonic() + 10
        while not self.server.up.wait(0.01):
            if not self.thread.is_alive() or time.monotonic() > deadline:
                raise RuntimeError("uvicorn didn't start")
        if not self.server.started:
            raise RuntimeError("uvicorn didn't start")
        return self

    def stop(self):
        self.server.stop()
        self.thread.join(15)
        self.sock.close()
        if self.thread.is_alive():
            raise RuntimeError("uvicorn didn't stop")
