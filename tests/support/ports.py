"""A loopback port for a server the suite starts by number, and may start again on the same one.

A port the system hands out for port 0 comes from its ephemeral range, and once its socket closes the system can hand
it to anything else that binds port 0: a fake IMAP server in another test, a client's own end. A server stopped and
started again on its port, or a port found free and then passed to a child to bind, can lose it that way, more often on
Linux, which reuses ephemeral ports sooner. So these ports come from below every system's ephemeral range (Linux's
starts at 32768, macOS's at 49152), where nothing binds by accident, and each pytest-xdist worker has a block of its
own, so two workers never choose the same one.
"""

import os
import random
import socket

_FIRST = 20_000
_BLOCK = 500
_BLOCKS = 25                                   # 20000 to 32499: under Linux's ephemeral range, past every known model server's port
_handed = set()


def _block():
    worker = os.environ.get("PYTEST_XDIST_WORKER", "gw0")
    number = int(worker[2:]) if worker[2:].isdigit() else 0
    return _FIRST + (number % _BLOCKS) * _BLOCK


def quiet_port():
    """A port in this worker's block that nothing listens on now and this process hasn't handed out before."""
    first = _block()
    choices = [p for p in range(first, first + _BLOCK) if p not in _handed]
    random.shuffle(choices)
    for port in choices:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
            except OSError:
                continue
        _handed.add(port)
        return port
    raise RuntimeError(f"no free loopback port from {first} to {first + _BLOCK - 1}")
