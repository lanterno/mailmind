"""A loopback port for a server the suite starts by number, and may start again on the same one.

A port the system hands out for port 0 comes from its ephemeral range, and once its socket closes the system can hand
it to anything else that binds port 0: a fake IMAP server in another test, a client's own end. A server stopped and
started again on its port, or a port found free and then passed to a child to bind, can lose it that way, more often on
Linux, which reuses ephemeral ports sooner. So these ports come from below every system's ephemeral range (Linux's
starts at 32768, macOS's at 49152), where nothing binds by accident, and each pytest-xdist worker has a block of its
own, so two workers never choose the same one.
"""

import itertools
import os
import random
import socket

_FIRST = 20_000
_BLOCK = 500
_BLOCKS = 25                                   # 20000 to 32499: under Linux's ephemeral range, past every known model server's port
_turn = None


def _block():
    worker = os.environ.get("PYTEST_XDIST_WORKER", "gw0")
    number = int(worker[2:]) if worker[2:].isdigit() else 0
    return _FIRST + (number % _BLOCKS) * _BLOCK


def quiet_port():
    """The next port in this worker's block that nothing listens on now.

    The block is handed out in turn, from a random place in it, so a port comes round again only after every other one
    has: a server stopped long since, never one a test may still start again. A worker that starts more servers than the
    block holds goes round again rather than running out."""
    global _turn
    first = _block()
    if _turn is None:
        start = random.randrange(_BLOCK)
        _turn = itertools.cycle([first + (start + i) % _BLOCK for i in range(_BLOCK)])
    for _ in range(_BLOCK):
        port = next(_turn)
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
            except OSError:
                continue
        return port
    raise RuntimeError(f"no free loopback port from {first} to {first + _BLOCK - 1}")
