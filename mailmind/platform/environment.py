"""Every environment variable MailMind reads, each declared here once: its name, its type, what unset
means and what it does. This module is the only reader of the environment, which
`tests/unit/platform/test_environment.py` holds the package to, so a variable is found by its name here.

A variable is read where it is used, at each call, through the reader its type gives it. Nothing here is
kept, so a test changes one with `monkeypatch.setenv` and the next read sees it, or hands a reader a mapping
in place of os.environ. What is kept, its caller keeps: `settings.current()` the data directory, which
`settings.use` swaps, and the module that reads the model or an interval as it loads keeps that.

Unset and empty are the same, for every variable. A value a variable doesn't take reads as what its
declaration says, and the log says so once (`[env] MAILMIND_WORKERS is set to a value it doesn't take, so
it reads as global`), naming the variable and never the value.

The types:

- `Switch`: on or off, by one rule. On is 1, true, yes or on, and off is 0, false, no or off, in any case
  with spaces around. Any other value reads as the switch's `typo` side: the one that writes, sends or
  spends less, and where neither does, off. So MAILMIND_NOSYNC fails toward read-only, MAILMIND_NO_DRAFT_TICK
  toward drafting nothing ahead, and MAILMIND_NO_BATCHES and MAILMIND_NO_SCHEMA toward their default.
- `Choice`: one of a closed set of names, in any case, with spaces around. Any other reads as the one its
  declaration says: MAILMIND_PROFILE as read-only (and the app refuses to start), MAILMIND_WORKERS as
  global and MAILMIND_LOG_LEVEL as info.
- `Accounts`: a switch, on for every account and off for none, or a comma-separated list of addresses.
  An entry that is no address reads as no account, so a typo turns nothing on.
- `Seconds`: a whole number of seconds, where 0 or less turns its job off. Any other value reads as unset.
- `Text` and `Secret`: as set. Nothing here logs a variable's value, and a secret's least of all.
- `Directory`: a path, which `settings` resolves.

The refactor proposal named pydantic-settings for this. The command line runs on Python alone
(`tests/e2e/test_cli.py` runs it with no site-packages), so this is the standard library.
"""
import os
import threading
from collections.abc import Mapping
from dataclasses import dataclass

type Environ = Mapping[str, str]

ON = frozenset({"1", "true", "yes", "on"})
OFF = frozenset({"", "0", "false", "no", "off"})


def _env(environ: Environ | None) -> Environ:
    return os.environ if environ is None else environ


class Variable:
    """One environment variable: its name, and what it does."""

    def __init__(self, name: str, means: str):
        self.name = name
        self.means = means

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self.name})"

    def raw(self, environ: Environ | None = None) -> str | None:
        """The value as set, None when unset."""
        return _env(environ).get(self.name)

    def given(self, environ: Environ | None = None) -> str:
        """The value without the spaces around it, "" when unset."""
        return (self.raw(environ) or "").strip()


class Switch(Variable):
    """On or off: ON or OFF, and `typo` for any other value."""

    def __init__(self, name: str, means: str, *, typo: bool):
        super().__init__(name, means)
        self.typo = typo

    def on(self, environ: Environ | None = None) -> bool:
        value = self.given(environ).lower()
        if value in ON:
            return True
        if value in OFF:
            return False
        _unread(self, "on" if self.typo else "off", value)
        return self.typo


class Choice(Variable):
    """One of `choices`: `unset` when unset, and `unknown` for a name that isn't one."""

    def __init__(self, name: str, means: str, *, choices: tuple[str, ...], unset: str, unknown: str):
        super().__init__(name, means)
        self.choices = choices
        self.unset = unset
        self.unknown = unknown

    def named(self, environ: Environ | None = None) -> str | None:
        """The choice it names, `unset` when it names none, None when it isn't a name."""
        value = self.given(environ).lower()
        if not value:
            return self.unset
        return value if value in self.choices else None

    def value(self, environ: Environ | None = None) -> str:
        """The choice it names, and `unknown` for a name that isn't one, which the log says."""
        named = self.named(environ)
        if named is None:
            _unread(self, self.unknown, self.given(environ))
            return self.unknown
        return named


@dataclass(frozen=True)
class Which:
    """The accounts an `Accounts` variable turns its feature on for: every one, or those listed, by
    address as written. Neither is false."""
    every: bool = False
    listed: frozenset[str] = frozenset()

    def __bool__(self) -> bool:
        return self.every or bool(self.listed)


class Accounts(Variable):
    """ON, or all, for every account, and OFF for none; else a comma-separated list of addresses, where an
    entry that is no address names no account, which the log says."""

    def value(self, environ: Environ | None = None) -> Which:
        value = self.given(environ)
        if value.lower() in ON | {"all"}:
            return Which(every=True)
        if value.lower() in OFF:
            return Which()
        entries = {x.strip() for x in value.split(",") if x.strip()}
        listed = frozenset(x for x in entries if "@" in x)
        if listed != entries:
            _unread(self, "the addresses it lists" if listed else "off", value)
        return Which(listed=listed)


class Seconds(Variable):
    """A whole number of seconds, `default` when unset, and for one that isn't a number, which the log says."""

    def __init__(self, name: str, means: str, *, default: int):
        super().__init__(name, means)
        self.default = default

    def value(self, environ: Environ | None = None) -> int:
        value = self.given(environ)
        if not value:
            return self.default
        try:
            return int(value)
        except ValueError:
            _unread(self, str(self.default), value)
            return self.default


class Text(Variable):
    """As set, None when unset or empty."""

    def value(self, environ: Environ | None = None) -> str | None:
        return self.raw(environ) or None


class Secret(Text):
    """A password or a key: as set, and never logged."""


class Directory(Variable):
    """A path, "" when unset, which `settings` resolves and checks."""

    def value(self, environ: Environ | None = None) -> str:
        return self.raw(environ) or ""


def credential(name: str, environ: Environ | None = None) -> str | None:
    """The variable an account's credential names (`{"env": NAME}` in mailmind.config.json), as set. The
    file names it, so it is declared there, not here."""
    return _env(environ).get(name)


# ── each variable ──────────────────────────────────────────────────────────────────────────────

HOME = Directory("MAILMIND_HOME", "The data directory (settings). Unset, the repo root. It must exist.")
PROFILE = Choice("MAILMIND_PROFILE", "How the process runs (profile): full, or read-only, which writes nothing outside. "
                                     "A name that is none reads as read-only, and the app refuses to start on it.",
                 choices=("full", "read-only"), unset="full", unknown="read-only")
NOSYNC = Switch("MAILMIND_NOSYNC", "The older switch for the read-only profile, which wins over MAILMIND_PROFILE.",
                typo=True)
WORKERS = Choice("MAILMIND_WORKERS", "One gate for every account's IMAP work (global), or one each (account) (gates).",
                 choices=("global", "account"), unset="global", unknown="global")
POOL = Accounts("MAILMIND_POOL", "Keep an IMAP connection for each account named, for reads (mailbox.pool). "
                                 "Every account leaves out Proton Bridge, which only a list names.")
WARM_FOLDERS = Accounts("MAILMIND_WARM_FOLDERS", "Sync each named account's Drafts, Archive and Sent with its INBOX "
                                                 "(mailbox.warm).")
LOG_LEVEL = Choice("MAILMIND_LOG_LEVEL", "debug shows debug lines and each error's text at the terminal, never in "
                                         "the file (logging).",
                   choices=("info", "debug"), unset="info", unknown="info")
RECONCILE_SECS = Seconds("MAILMIND_RECONCILE_SECS", "About how long reconcile takes to go round every account.",
                         default=1800)
EXPIRE_SECS = Seconds("MAILMIND_EXPIRE_SECS", "The seconds between the expiry rules' rounds.", default=3600)
NO_DRAFT_TICK = Switch("MAILMIND_NO_DRAFT_TICK", "Draft no replies ahead (the draft-ahead job).", typo=True)
NO_BATCHES = Switch("MAILMIND_NO_BATCHES", "Triage a backlog one message at a time, at full price, rather than "
                                           "as a batch.", typo=False)
NO_SCHEMA = Switch("MAILMIND_NO_SCHEMA", "Ask the model for free-form JSON rather than structured output.",
                   typo=False)
MODEL = Text("MAILMIND_MODEL", "The Claude model every feature asks. Unset, models.DEFAULT.")
PROTON_BRIDGE_PASS = Secret("MAILMIND_PROTON_BRIDGE_PASS", "The Proton Bridge password, before any other source.")
PROTON_OP_ITEM = Text("MAILMIND_PROTON_OP_ITEM", "The 1Password item holding the Proton Bridge password.")
ANTHROPIC_API_KEY = Secret("ANTHROPIC_API_KEY", "The Claude API key, when mailmind.config.json names none "
                                                "(bootstrap.assistant). Neither: AI is off.")
ANTHROPIC_BASE_URL = Text("ANTHROPIC_BASE_URL", "Where API calls go, in place of https://api.anthropic.com.")

VARIABLES: tuple[Variable, ...] = (HOME, PROFILE, NOSYNC, WORKERS, POOL, WARM_FOLDERS, LOG_LEVEL, RECONCILE_SECS,
                                   EXPIRE_SECS, NO_DRAFT_TICK, NO_BATCHES, NO_SCHEMA, MODEL, PROTON_BRIDGE_PASS,
                                   PROTON_OP_ITEM, ANTHROPIC_API_KEY, ANTHROPIC_BASE_URL)


# ── a value a variable doesn't take ────────────────────────────────────────────────────────────

_said: set[tuple[str, str]] = set()
_said_lock = threading.Lock()


def _unread(variable: Variable, reads_as: str, value: str) -> None:
    """Say, once for each value, that `variable` is set to one it doesn't take and what it reads as. The
    line names the variable and the reading, never the value, which could be anything someone typed."""
    with _said_lock:
        if (variable.name, value) in _said:
            return
        _said.add((variable.name, value))
    from mailmind.platform.logging import get_logger    # it imports settings, which imports this
    get_logger("env").warning("%(what)s is set to a value it doesn't take, so it reads as %(why)s",
                              {"what": variable.name, "why": reads_as})


def forget() -> None:
    """Say each again: a test's start."""
    with _said_lock:
        _said.clear()
