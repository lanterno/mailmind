"""The login Keychain as the tests have it: guard.install() puts this in the real one's place (mailmind.platform.keychain's
backend), so no test, and no server a test starts with the guard, reaches the login Keychain.

It holds MailMind's items in this process's memory, or, while the variable VARIABLE names a file, in that file, so processes
started together share them as they share the real one: the demo writes the world's passwords, and the server it starts
reads them. conftest empties it before each test.
"""
import json
import os
import threading

VARIABLE = "MAILMIND_TEST_KEYCHAIN"


class FakeKeychain:
    def __init__(self):
        self._held = {}
        self._lock = threading.Lock()

    def _file(self):
        return os.environ.get(VARIABLE) or None

    def _load(self):
        path = self._file()
        if path is None:
            return dict(self._held)
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except FileNotFoundError:
            return {}

    def _save(self, held):
        path = self._file()
        if path is None:
            self._held = held
            return
        fd = os.open(path + ".tmp", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(held, f)
        os.replace(path + ".tmp", path)

    def find(self, name):
        with self._lock:
            return self._load().get(name)

    def add(self, name, secret, *, replace):
        with self._lock:
            held = self._load()
            if replace or name not in held:
                held[name] = secret
                self._save(held)

    def delete(self, name):
        with self._lock:
            held = self._load()
            if name not in held:
                return False
            del held[name]
            self._save(held)
            return True

    def items(self):
        """What it holds, {name: secret}, for a test to read."""
        with self._lock:
            return self._load()

    def clear(self):
        with self._lock:
            self._save({})
