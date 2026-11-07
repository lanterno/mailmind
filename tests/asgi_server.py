"""An ASGI app on uvicorn, on a random loopback port, serving from a background thread: the app's
server the way the tests run it."""
import socket
import threading
import time


class Uvicorn:
    def __init__(self, app, name="app-http", grace=5):
        from mailmind.http import serving
        self.sock = socket.create_server(("127.0.0.1", 0))
        self.port = self.sock.getsockname()[1]
        self.server = serving.Server(serving.config(app, port=self.port, grace=grace))
        self.thread = threading.Thread(target=self.server.run, kwargs={"sockets": [self.sock]},
                                       name=name, daemon=True)

    def start(self):
        self.thread.start()
        deadline = time.monotonic() + 10
        while not self.server.started:
            if not self.thread.is_alive() or time.monotonic() > deadline:
                raise RuntimeError("uvicorn didn't start")
            time.sleep(0.01)
        return self

    def stop(self):
        self.server.should_exit = True
        self.thread.join(15)
        self.sock.close()
        if self.thread.is_alive():
            raise RuntimeError("uvicorn didn't stop")
