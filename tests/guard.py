"""A tripwire for the test process: no test may touch the owner's mail data, reach past this
machine, or start a credential helper or the claude CLI.

conftest.py installs it before any MailMind module is imported. Python audit hooks see every
open(), sqlite3.connect(), rename, delete, socket connect, DNS lookup and process start in the
process, so a module path a fixture forgot to redirect fails the test instead of reading or
writing real mail. Each refusal raises, so the access never happens, and is recorded, so a caller
that swallows the exception still fails the test (see the autouse fixture in conftest.py).
A hook can't be removed, which is the point.

One gap: socket.connect() given a host *name* resolves it in C before its audit event fires.
imaplib, smtplib and urllib all resolve through socket.getaddrinfo first, which is checked.
"""
import fnmatch
import ipaddress
import os
import re
import sys
import threading

ROOT = os.path.realpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
# Where the owner's data may live: the repo root, and any directory MAILMIND_HOME named when the
# tests started (protect() adds it; conftest takes the variable out of the environment).
_data_dirs = {ROOT.lower()}

# The owner's data at the repo root (CLAUDE.md, "Nothing personal is committed"), and the temp
# files config.save, the saved views and the snippets write beside it.
PRIVATE = ("mailmind.db", "mailmind.db-*", "mailmind.config.json*", "mailmind_rules.json",
           "mailmind_views.json", "mailmind_snippets.json", "*.jsonl", "*.sqlite", "*.db-journal", ".cfg-*.tmp", ".views-*.tmp",
           ".snippets-*.tmp",
           "mailmind.log", "mailmind.log.*")
# A real AI call, a 1Password read and a Keychain read.
PROGRAMS = ("claude", "op", "security")

violations = []
_busy = threading.local()


class Tripwire(PermissionError):
    pass


def _private(path):
    if isinstance(path, int):
        return False                                    # a file descriptor, already open
    try:
        p = os.fsdecode(os.fspath(path))
    except TypeError:
        return False
    if p.startswith("file:"):                           # an sqlite URI
        p = p[5:].split("?", 1)[0]
    if not p or p == ":memory:":
        return False
    d, name = os.path.split(os.path.realpath(os.path.abspath(p)))
    return d.lower() in _data_dirs and any(fnmatch.fnmatchcase(name.lower(), pat) for pat in PRIVATE)


def protect(directory):
    """Guard the data files in `directory` as those at the repo root are."""
    _data_dirs.add(os.path.realpath(os.path.abspath(os.path.expanduser(directory))).lower())


def _remote(host):
    if host is None:
        return False
    if isinstance(host, bytes):
        host = host.decode("ascii", "replace")
    host = str(host).strip().lower()
    if host in ("", "localhost"):
        return False
    try:
        return not ipaddress.ip_address(host.split("%", 1)[0]).is_loopback
    except ValueError:
        return True                                     # a name other than localhost


_SHELLS = ("sh", "bash", "zsh", "dash")


def _shell_programs(script):
    """The first word of each command in a shell script, roughly."""
    return [seg.split()[0] for seg in re.split(r"[;&|\n()]+", script) if seg.split()]


def _program(executable, args):
    """Which blocked program a process start would run, if any. Only the program itself counts
    (and, for `sh -c`, each command's first word), never its arguments."""
    if isinstance(args, (str, bytes, os.PathLike)):
        args = [args]
    args = [os.fsdecode(a) for a in (args or [])]
    first = os.fsdecode(executable) if executable else (args[0] if args else "")
    names = [first]
    if os.path.basename(first) in _SHELLS and "-c" in args[1:]:
        i = args.index("-c", 1)
        names += _shell_programs(args[i + 1] if i + 1 < len(args) else "")
    return next((os.path.basename(n) for n in names if os.path.basename(n) in PROGRAMS), None)


def _refuse(what):
    violations.append(what)
    raise Tripwire(f"test tripwire: {what}")


def _hook(event, args):
    if getattr(_busy, "on", False):
        return
    _busy.on = True
    try:
        if event == "open" and _private(args[0]):
            _refuse(f"open {args[0]!r}")
        elif event == "sqlite3.connect" and _private(args[0]):
            _refuse(f"sqlite3.connect {args[0]!r}")
        elif event in ("os.rename", "os.remove", "os.truncate", "os.chmod", "os.utime") \
                and any(_private(a) for a in args[:2] if isinstance(a, (str, bytes, os.PathLike))):
            _refuse(f"{event} {args[:2]!r}")
        elif event == "socket.connect" and isinstance(args[1], tuple) and _remote(args[1][0]):
            _refuse(f"connect to {args[1]!r}")
        elif event in ("socket.getaddrinfo", "socket.gethostbyname", "socket.gethostbyname_ex") \
                and _remote(args[0]):
            _refuse(f"DNS lookup of {args[0]!r}")
        elif event == "subprocess.Popen":
            prog = _program(args[0], args[1])
            if prog:
                _refuse(f"start {prog}")
        elif event == "os.system":
            prog = next((n for n in _shell_programs(os.fsdecode(args[0])) if os.path.basename(n) in PROGRAMS), None)
            if prog:
                _refuse(f"start {prog}")
    finally:
        _busy.on = False


_installed = False


def install():
    global _installed
    if not _installed:
        sys.addaudithook(_hook)
        _installed = True
