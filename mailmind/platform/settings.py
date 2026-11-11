"""Where MailMind keeps its data, and the port it serves on.

The process has one Settings. `current()` builds it from the environment the first time it is
asked; `configure()` replaces it, and `use()` replaces it for the length of a `with` block. The
legacy modules ask `current()` each time they open one of these files, never once at import, so
a replaced Settings reaches every module at once. That is how a test points the whole app at a
temp directory.

MAILMIND_HOME (mailmind.platform.environment) names the data directory. Unset, it is the repo root,
where the data has always lived. Set, it must name an existing directory; it is never created here.
"""
import contextlib
import os
import threading
from collections.abc import Iterator, Mapping
from dataclasses import dataclass

from mailmind.platform import environment

# The repo root.
SOURCE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_PORT = 8721


class SettingsError(ValueError):
    """The environment asks for something MailMind can't use."""


@dataclass(frozen=True)
class Settings:
    home: str                       # the data directory
    port: int = DEFAULT_PORT        # served on 127.0.0.1

    @property
    def db(self) -> str:
        return os.path.join(self.home, "mailmind.db")

    @property
    def config(self) -> str:
        return os.path.join(self.home, "mailmind.config.json")

    @property
    def rules(self) -> str:
        return os.path.join(self.home, "mailmind_rules.json")

    @property
    def rules_lock(self) -> str:
        return os.path.join(self.home, ".rules.lock")

    @property
    def snippets(self) -> str:
        return os.path.join(self.home, "mailmind_snippets.json")

    @property
    def sweep_audit(self) -> str:
        return os.path.join(self.home, "sweep_audit.jsonl")

    @property
    def reconcile_audit(self) -> str:
        return os.path.join(self.home, "reconcile_audit.jsonl")

    @property
    def log(self) -> str:
        return os.path.join(self.home, "mailmind.log")


def from_env(environ: Mapping[str, str] | None = None) -> Settings:
    """Settings from the environment (os.environ unless given). An empty MAILMIND_HOME counts as
    unset. A relative one is taken from the current directory, and a leading ~ is expanded."""
    raw = environment.HOME.value(environ)
    if not raw:
        return Settings(home=SOURCE_DIR)
    home = os.path.abspath(os.path.expanduser(raw))
    if not os.path.isdir(home):
        raise SettingsError(f"{environment.HOME.name} is not a directory: {home}")
    return Settings(home=home)


_current: Settings | None = None
_lock = threading.Lock()


def current() -> Settings:
    """The process's Settings, read from the environment on first use."""
    global _current
    s = _current
    if s is None:
        with _lock:
            if _current is None:
                _current = from_env()
            s = _current
    return s


def configure(settings: Settings | None) -> None:
    """Make `settings` the process's Settings. None means read the environment again next time."""
    global _current
    with _lock:
        _current = settings


@contextlib.contextmanager
def use(settings: Settings) -> Iterator[Settings]:
    """`settings` for the length of the block, then whatever was there before."""
    global _current
    with _lock:
        before, _current = _current, settings
    try:
        yield settings
    finally:
        with _lock:
            _current = before
