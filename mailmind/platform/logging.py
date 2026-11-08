"""What the app says of its own running, on the standard library's `logging`.

A module asks for its logger by name, and a line says what varies by naming it:

    from mailmind.platform.logging import get_logger
    log = get_logger("jobs")                                   # the logger `mailmind.jobs`
    log.warning("%(job)s failed", {"job": name}, exc_info=e)

Importing the names keeps `logging`, in that module, the standard library's, and this module's own
`import logging` is the standard library's since imports are absolute.

A log is personal data, so `Redact` withholds whatever it can't vouch for. A value shows only under a
name on `ALLOWED`, and even there anything that looks like an address is withheld. A positional
argument that isn't a number, an `extra=` or a mapping's entry under another name, and a value that
isn't plain all show as `<withheld>`. Of an exception, only its class name shows. So a message is a
literal, never an f-string, which `tests/unit/platform/test_logging.py` holds the package to.

A line goes to two places. On stdout, for someone at a terminal: `[jobs] sync failed: ConnectionResetError`.
This goes from the moment this module loads, as a print did, for the command line too. And, once
`setup()` has run, as one JSON object a line to `mailmind.log` in the data directory, readable by its
owner only, kept to `BACKUPS` older files of `MAX_BYTES`, with the ids of the request and the job round
it happened in (`for_request`, `for_round`). The `mailmind` logger doesn't propagate, so no handler
this module didn't make hears a record.

`MAILMIND_LOG_LEVEL=DEBUG` turns the terminal's withholding off: debug lines, and each exception's text
and traceback, for someone chasing a fault. The file is the same either way.
"""
import contextlib
import contextvars
import json
import logging
import logging.handlers
import os
import re
import secrets
import sys
from collections.abc import Mapping
from datetime import UTC, datetime

from mailmind.platform import environment, settings

ROOT = "mailmind"
WITHHELD = "<withheld>"
MAX_BYTES = 1 << 20                     # of the file, before it rotates
BACKUPS = 3
_MAX_TEXT = 300                         # of a message or a value

# The names whose values a line may show: the app's own words, ids and counts, never a person's.
ALLOWED = frozenset({"adapters", "count", "engine", "event", "handler", "id", "job", "method", "path", "port", "what",
                     "why"})

_ADDRESS = re.compile(r"""[^\s<>"'(),;:\[\]]*(?:@|%40)[^\s<>"'(),;:\[\]]+""", re.IGNORECASE)
_CONTROL = re.compile(r"[\x00-\x1f\x7f]+")
_STOCK = frozenset(vars(logging.makeLogRecord({}))) | {"message", "asctime"}   # what's on any record, unasked

_request_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("mailmind_request_id", default=None)
_job_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("mailmind_job_id", default=None)


def get_logger(name=""):
    """The logger whose lines are tagged `[name]`; with no name, the one whose lines have no tag."""
    return logging.getLogger(f"{ROOT}.{name}" if name else ROOT)


@contextlib.contextmanager
def _carrying(var, value):
    token = var.set(value)
    try:
        yield value
    finally:
        var.reset(token)


def for_request():
    """A `with` block whose lines carry a new request id. A thread the block starts doesn't."""
    return _carrying(_request_id, secrets.token_hex(6))


def for_round(job):
    """A `with` block whose lines carry a new id for this round of `job`, a name from the code."""
    return _carrying(_job_id, f"{job}-{secrets.token_hex(4)}")


def correlation():
    """The ids this moment is running under, those it has."""
    ids = {"request_id": _request_id.get(), "job_id": _job_id.get()}
    return {name: value for name, value in ids.items() if value}


def _scrub(text):
    """On one line, short, with anything that looks like an address withheld. It cuts first since the
    search goes quadratic on a long run, and at twice the length so no address is cut in two in sight."""
    return _ADDRESS.sub(WITHHELD, _CONTROL.sub(" ", text[:2 * _MAX_TEXT]))[:_MAX_TEXT]


def _shown(name, value):
    """What a line may say of `value`, given under `name`."""
    if name not in ALLOWED:
        return WITHHELD
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return _scrub(value) if isinstance(value, str) else WITHHELD


def _rendered(template, args):
    """The message. A template that isn't a string could be anything once it is one, and one that
    won't take its arguments says what it can with none."""
    if not isinstance(template, str):
        return WITHHELD
    try:
        return _scrub(template % args if args else template)
    except (TypeError, ValueError, KeyError):
        return _scrub(template)


class Redact(logging.Filter):
    """Hands on a record holding only what a line may show: a message with what it names filled in,
    `fields` (each value by its name), and `error`, the exception's class name. The record it was
    given is left as it was, for a handler that withholds nothing."""

    def filter(self, record):
        args = record.args
        extras = {k: v for k, v in vars(record).items() if k not in _STOCK}
        named = {str(k): _shown(str(k), v) for k, v in args.items()} if isinstance(args, Mapping) else {}
        fields = {**{k: _shown(k, v) for k, v in extras.items()}, **named}
        if not isinstance(args, Mapping) and args:
            filled = tuple(a if isinstance(a, (int, float)) else WITHHELD for a in args)
        else:
            filled = named
        kind = record.exc_info[0] if record.exc_info else None
        return logging.makeLogRecord({**vars(record), "msg": _rendered(record.msg, filled), "args": None,
                                      "fields": fields, "error": kind.__name__ if kind else None,
                                      "exc_info": None, "exc_text": None, "stack_info": None})


def _tag(record):
    return record.name.removeprefix(ROOT).removeprefix(".")


class HumanFormatter(logging.Formatter):
    """`[jobs] sync failed: ConnectionResetError`: the tag, the message and what went wrong."""

    def format(self, record):
        tag, error = _tag(record), getattr(record, "error", None)
        line = record.getMessage() + (f": {error}" if error else "")
        line = f"[{tag}] {line}" if tag else line
        return f"{line}\n{self.formatException(record.exc_info)}" if record.exc_info else line


class JsonFormatter(logging.Formatter):
    """One JSON object: when, how grave, whose, the message, the ids it ran under, what went wrong
    and its fields. It shows what's on the record, so it comes after `Redact`."""

    def format(self, record):
        entry = {"ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
                 "level": record.levelname, "logger": _tag(record), "msg": record.getMessage(), **correlation()}
        if getattr(record, "error", None):
            entry["error"] = getattr(record, "error")
        for name, value in getattr(record, "fields", {}).items():
            entry.setdefault(name, value)
        return json.dumps(entry)


class _Terminal(logging.StreamHandler):
    """stdout as it is at each line, as a print's was, so a redirect or a test's capture hears it. A
    line it can't write (stdout closed) is lost, which is nothing to stop the caller for."""

    def __init__(self):
        logging.Handler.__init__(self)

    @property
    def stream(self):
        return sys.stdout

    def handleError(self, record):
        pass


class _OwnFile(logging.handlers.RotatingFileHandler):
    """The rotating file, readable by its owner only: a rotated one keeps its mode, and a file that was
    already there is brought into line. A line it can't write is lost, and that stops nothing."""

    def _open(self):
        fd = os.open(self.baseFilename, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        os.fchmod(fd, 0o600)
        return os.fdopen(fd, self.mode, encoding=self.encoding, errors=self.errors)

    def handleError(self, record):
        pass


_root = logging.getLogger(ROOT)
_redact = Redact()
_terminal = _Terminal()
_terminal.setFormatter(HumanFormatter())
_terminal.addFilter(_redact)
_root.addHandler(_terminal)
_root.setLevel(logging.INFO)
_root.propagate = False
_file: _OwnFile | None = None


def setup():
    """Open the log file in the data directory, on its first line (wiring.start), and
    read MAILMIND_LOG_LEVEL. Asked again it adds nothing, and under other Settings the file moves."""
    global _file
    path = os.path.abspath(settings.current().log)
    if _file is None or _file.baseFilename != path:
        _close_file()
        _file = _OwnFile(path, maxBytes=MAX_BYTES, backupCount=BACKUPS, encoding="utf-8", delay=True)
        _file.setLevel(logging.INFO)
        _file.addFilter(_redact)
        _file.setFormatter(JsonFormatter())
        _root.addHandler(_file)
    debug = environment.LOG_LEVEL.value() == "debug"     # with the file open, which hears a level it doesn't take
    _root.setLevel(logging.DEBUG if debug else logging.INFO)
    (_terminal.removeFilter if debug else _terminal.addFilter)(_redact)


def _close_file():
    global _file
    if _file is not None:
        _root.removeHandler(_file)
        _file.close()
        _file = None


def stop():
    """As this module left things when it loaded: no file, and the terminal withholding. A test's end."""
    _close_file()
    _root.setLevel(logging.INFO)
    _terminal.addFilter(_redact)
