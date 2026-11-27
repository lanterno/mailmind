"""The Mac client: MailMind Dev, the Mac app's Debug build, driven from inside by its driver (test-architecture.md 5.2),
in lane A: on this Mac beside the person using it, never taking the screen, the keyboard or the menu bar (section 10).

Two parts:

- `DevApp` is the world's app on the Mac, in the place `served.Served` has for the page: the process, started by path
  from this worktree's Debug build with a temp home, the Keychain's stand-in, the tests' database key and the core's
  clock paused, in a process group of its own, its pid written into the home and only that pid ever stopped. The app
  connects out to a Unix socket this makes in a short temp path, and speaks JSON lines. The core's test world (its
  clock, its paused jobs, `settle`, the high-water event id) and its routes are reached through the driver's `core`
  command, as `mm-served`'s lines reach the page's server.
- `MacClient` is the client the screens call (`press`, `type`, `click`, `find`, `shown`, `settle`…), as `PageClient` is.
  Its input is real input made in the app (MailMindTestKit's keys under a named layout, the mouse, the menu bar as
  AppKit validates it); what it reads is the app's accessibility tree.

Lane A can't vouch for activation, the real menu bar or a drag between apps: the driver says so with a menu or a drag,
and scenarios that need them are marked `needs_session` (lane B).
"""
import contextlib
import fcntl
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import tempfile
import time
from dataclasses import dataclass

from .shown import Shown

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
APPLE = os.path.join(ROOT, "shells", "apple")
DERIVED = os.path.join(APPLE, "build", "DerivedData")
APP = os.path.join(DERIVED, "Build", "Products", "Debug", "MailMindDev.app")
EXECUTABLE = os.path.join(APP, "Contents", "MacOS", "MailMindDev")
PID_FILE = "mailmind-dev.pid"
SETTLE_S = 20.0

# Regions a person names, as the Mac app holds them: by anchor (anchors.toml, an accessibility identifier), or by
# role and label.
REGIONS = {
    "list": {"id": "list"},
    "reader": {"id": "reader"},
    "composer": {"id": "composer"},
    "toast": {"id": "toast"},
    "float": {"id": "toast"},            # the Mac says floats as toasts that offer something (anchors.toml)
    "tray": {"id": "tray"},
    "rail": {"role": "AXGroup", "label": "Mailboxes"},
    "top bar": {"id": "top bar"},
    "dialog": {"role": "AXGroup", "label": "Dialog"},
}
# The Mac's roles for the roles a scenario names, as the page's ARIA roles are named.
PLACES = {"INBOX": "Inbox", "Sent": "Sent", "Drafts": "Drafts", "Archive": "Archive", "Trash": "Trash", "Junk": "Junk"}
ROLES = {"button": "AXButton", "tab": "AXButton", "textbox": "AXTextField|AXTextArea", "combobox": "AXTextField|AXComboBox",
         "checkbox": "AXCheckBox", "link": "AXLink", "heading": "AXHeading", "text": "AXStaticText", "row": "row"}


# ── the build ───────────────────────────────────────────────────────────

def _sources():
    for top in ("Sources", "TestKit", "Generated", "Fonts"):
        for folder, _, files in os.walk(os.path.join(APPLE, top)):
            for name in files:
                yield os.path.join(folder, name)
    yield os.path.join(APPLE, "project.yml")
    yield os.path.join(APPLE, "Release", "mac.yml")


def build():
    """MailMind Dev, built once for what the sources hold now, into shells/apple/build/DerivedData: the one Debug build
    of this worktree, which `just apple test` shares. A run, or another worker, that finds it newer than every source
    uses it."""
    if not os.path.isdir(os.path.join(APPLE, "build", "MailMindCore.xcframework")):
        raise RuntimeError("no shells/apple/build/MailMindCore.xcframework: `just apple xcframework` builds the core")
    os.makedirs(os.path.join(APPLE, "build"), exist_ok=True)
    with open(os.path.join(APPLE, "build", "mac-e2e.lock"), "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        newest = max(os.path.getmtime(p) for p in _sources() if os.path.exists(p))
        if os.access(EXECUTABLE, os.X_OK) and os.path.getmtime(EXECUTABLE) >= newest:
            return EXECUTABLE
        subprocess.run(["xcodegen", "generate", "--quiet"], cwd=APPLE, check=True)
        done = subprocess.run(
            ["xcodebuild", "-quiet", "-jobs", "4", "-project", "MailMind.xcodeproj", "-scheme", "MailMind-macOS",
             "-configuration", "Debug", "-destination", "platform=macOS,arch=arm64", "-derivedDataPath", DERIVED,
             "CODE_SIGNING_ALLOWED=NO", "build"], cwd=APPLE, capture_output=True, text=True)
        if done.returncode != 0:
            errors = [line for line in done.stdout.splitlines() + done.stderr.splitlines() if "error" in line]
            raise RuntimeError("MailMind Dev didn't build:\n" + "\n".join(errors[-20:]))
        os.utime(EXECUTABLE)
    return EXECUTABLE


# ── the link ────────────────────────────────────────────────────────────

class DriverError(AssertionError):
    pass


class Link:
    """The runner's end of the driver's socket: listening in a short temp folder of its own, since a Unix socket's path
    can't pass 104 bytes and agents' homes are longer."""

    def __init__(self):
        self.folder = tempfile.mkdtemp(prefix="mmd-", dir="/tmp" if len(tempfile.gettempdir()) > 60 else None)
        self.path = os.path.join(self.folder, "driver.sock")
        if len(self.path.encode()) >= 104:
            raise RuntimeError(f"the driver's socket path is too long for a Unix socket: {self.path}")
        self.listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.listener.bind(self.path)
        os.chmod(self.path, 0o600)
        self.listener.listen(1)
        self.conn = self.stream = None
        self.hello = None
        self._next = 0

    def accept(self, proc, timeout):
        """The app connecting out, and its hello."""
        self.listener.settimeout(0.5)
        deadline = time.monotonic() + timeout
        while self.conn is None:
            if proc.poll() is not None:
                raise DriverError(f"MailMind Dev ended before its driver connected (status {proc.returncode})")
            if time.monotonic() > deadline:
                raise DriverError(f"MailMind Dev's driver didn't connect in {timeout:.0f}s")
            with contextlib.suppress(socket.timeout):
                self.conn, _ = self.listener.accept()
        self.conn.settimeout(None)
        self.stream = self.conn.makefile("rwb")
        self.hello = self._read(timeout)
        return self.hello

    def ask(self, verb, timeout=120, **fields):
        self._next += 1
        line = {"id": self._next, "do": verb, **fields}
        self.stream.write((json.dumps(line) + "\n").encode())
        self.stream.flush()
        reply = self._read(timeout)
        if reply.get("id") != self._next:
            raise DriverError(f"the driver answered {reply.get('id')} to {self._next}")
        if reply.get("on_screen"):
            raise DriverError(f"lane A broken: MailMind Dev's windows could be seen: {reply['on_screen']}")
        if not reply.get("ok"):
            raise DriverError(f"{verb}: {reply.get('error')}")
        return reply

    def _read(self, timeout):
        self.conn.settimeout(timeout)
        try:
            raw = self.stream.readline()
        except socket.timeout:
            raise DriverError(f"the driver said nothing for {timeout:.0f}s") from None
        if not raw:
            raise DriverError("MailMind Dev hung up")
        return json.loads(raw)

    def close(self):
        for part in (self.stream, self.conn, self.listener):
            if part is not None:
                with contextlib.suppress(OSError):
                    part.close()
        shutil.rmtree(self.folder, ignore_errors=True)


# ── the world's app ─────────────────────────────────────────────────────

@dataclass
class Response:
    status: int
    headers: dict
    body: object

    @property
    def json(self):
        assert isinstance(self.body, (dict, list)), f"not JSON: {self.headers.get('content-type')}"
        return self.body


class CoreClock:
    """The core's test clock, with this process's beside it (`local`, time_machine's), as ServerClock keeps the page's."""

    def __init__(self, app, local, now):
        self._app, self._local, self.now = app, local, now

    def move_to(self, when):
        self._local.move_to(when)
        self.now = when
        self._app.core("set", at=when.isoformat())

    def shift(self, delta):
        self.move_to(self.now + delta)


class DevApp:
    """MailMind Dev over a world's home, as the world's app: started with `start`, stopped with `stop`."""

    origin = "MailMind Dev"

    def __init__(self, home, now, local_clock, log_path, executable=None):
        self.home, self.log_path = home, log_path
        self.executable = executable or EXECUTABLE
        self.proc = self.link = None
        self.clock = CoreClock(self, local_clock, now)

    def environment(self, socket_path):
        """What the app is started with: only what it needs, a run of its own (docs/test-architecture.md section 6)."""
        import fake_keychain
        env = {name: os.environ[name] for name in ("HOME", "PATH", "TMPDIR", "USER", "LOGNAME") if name in os.environ}
        env.update({
            "TZ": "UTC", "LANG": "en_US.UTF-8",
            "MAILMIND_HOME": self.home,
            "MAILMIND_TEST_KEYCHAIN": os.environ[fake_keychain.VARIABLE],
            "MAILMIND_DB_KEY": os.environ["MAILMIND_DB_KEY"],
            "MAILMIND_TEST_CLOCK": "paused",
            "MAILMIND_TEST_NOW": self.clock.now.isoformat(),
            "MAILMIND_TEST_DRIVER": socket_path,
        })
        if os.environ.get("ANTHROPIC_BASE_URL"):
            env["ANTHROPIC_BASE_URL"] = os.environ["ANTHROPIC_BASE_URL"]
        return env

    def start(self):
        if not os.access(self.executable, os.X_OK):
            raise RuntimeError(f"no MailMind Dev at {self.executable}: `just e2e --client mac` builds it")
        self.link = Link()
        with open(self.log_path, "ab") as log:
            # A process group of its own, so nothing the runner does reaches another, and its language English
            # whatever the Mac's (the argument domain, which no run keeps).
            self.proc = subprocess.Popen([self.executable, "-AppleLanguages", "(en)", "-AppleLocale", "en_US"],
                                         env=self.environment(self.link.path), stdin=subprocess.DEVNULL,
                                         stdout=log, stderr=log, start_new_session=True)
        with open(os.path.join(self.home, PID_FILE), "w") as f:
            f.write(str(self.proc.pid))
        try:
            hello = self.link.accept(self.proc, timeout=60)
            if hello.get("lane") != "A":
                raise DriverError(f"the driver said {hello}")
            self._wait_for_core(60)
        except BaseException:
            self.stop()
            raise
        return self

    def _wait_for_core(self, timeout):
        deadline = time.monotonic() + timeout
        while True:
            try:
                self.core("high_water")
                return
            except DriverError as e:
                if "isn't open" not in str(e) or time.monotonic() > deadline:
                    raise DriverError(f"MailMind Dev's core didn't open: {e}; {self.output()}") from None
            time.sleep(0.05)

    def ask(self, verb, timeout=120, **fields):
        if self.link is None:
            raise DriverError("MailMind Dev isn't running")
        return self.link.ask(verb, timeout=timeout, **fields)

    def core(self, op, timeout=120, **fields):
        return self.ask("core", timeout=timeout, op=op, **fields).get("said", "")

    def output(self, lines=30):
        try:
            with open(self.log_path, "rb") as log:
                last = log.read().decode("utf-8", "replace").splitlines()[-lines:]
        except OSError as e:
            last = [f"(unread: {e})"]
        return f"its output is in {self.log_path}, ending:\n" + "\n".join(last)

    # What the world asks of its app, as of the page's server.

    def high_water(self):
        return self.core("high_water")

    def work(self, job, *args):
        if job == "kicked":
            self.core("kicked")
        else:
            self.core("pump", args=[job, *args])

    def settle(self, timeout=30):
        left = self.core("settle", seconds=timeout, timeout=timeout + 30)
        if left not in ("0", ""):
            raise AssertionError(f"the core still answers {left} request(s) after {timeout}s")

    def check(self):
        """The tripwire's findings have no in-process counterpart in the core yet (test-architecture.md 7)."""

    def request(self, method, path, body=None, headers=None):
        sent = body if isinstance(body, (bytes, str)) else json.dumps(body if body is not None else {})
        if isinstance(sent, bytes):
            sent = sent.decode()
        answer = self.ask("core", op="route", method=method, target=path, body=sent if method == "POST" else "")
        kind = answer.get("content_type", "")
        text = answer.get("body", "")
        parsed = json.loads(text) if text and kind.startswith(("application/json", "application/problem+json")) else text
        return Response(answer["status"], {"content-type": kind}, parsed)

    def get(self, path, **kw):
        return self.request("GET", path, **kw)

    def post(self, path, body=None, **kw):
        return self.request("POST", path, body, **kw)

    def stop(self):
        """The app asked to quit (its core closed first); only its own pid is ever signalled, if it won't."""
        proc, link, self.proc, self.link = self.proc, self.link, None, None
        if proc is None:
            return
        try:
            if link is not None and link.conn is not None and proc.poll() is None:
                with contextlib.suppress(DriverError, OSError):
                    link.ask("quit", timeout=30)
            try:
                proc.wait(15)
            except subprocess.TimeoutExpired:
                os.kill(proc.pid, signal.SIGTERM)
                try:
                    proc.wait(10)
                except subprocess.TimeoutExpired:
                    os.kill(proc.pid, signal.SIGKILL)
                    proc.wait()
        finally:
            if link is not None:
                link.close()
            with contextlib.suppress(FileNotFoundError):
                os.unlink(os.path.join(self.home, PID_FILE))

    def restart(self):
        with self.stopped():
            pass

    @contextlib.contextmanager
    def stopped(self):
        """The app stopped while the test changes what it stores, and started again over the same home."""
        self.stop()
        try:
            yield
        finally:
            self.start()


# ── the client ──────────────────────────────────────────────────────────

class Element:
    """Something in MailMind Dev's windows, found by role and label within a region; read afresh at each ask."""

    def __init__(self, client, spec, description):
        self.client, self.spec, self.description = client, spec, description

    def __repr__(self):
        return f"<{self.description}>"

    def _all(self):
        return self.client.app.ask("find", **self.spec)["found"]

    def _one(self):
        found = self._all()
        return found[0] if len(found) == 1 else None

    @property
    def count(self):
        return len(self._all())

    @property
    def exists(self):
        return self.count > 0

    @property
    def visible(self):
        one = self._one()
        return bool(one and one["visible"])

    @property
    def enabled(self):
        one = self._one()
        return bool(one and one["enabled"])

    @property
    def focused(self):
        one = self._one()
        return bool(one and one["focused"])

    @property
    def selected(self):
        one = self._one()
        return bool(one and one["selected"])

    @property
    def text(self):
        one = self._one()
        return " ".join((one or {}).get("text", "").split())

    @property
    def value(self):
        return (self._one() or {}).get("value", "")

    def click(self, count=1):
        self.client.click(self, count)

    def find(self, role, label=None, **where):
        return self.client.find(role, label, within=self, **where)


class MacClient:
    """MailMind Dev on the world, through its driver. The app is the world's (`world.app`, a `DevApp`); this is how the
    screens reach its windows."""

    kind = "mac"
    keys_reach_the_page = False           # a key goes to what has the keyboard, or nowhere: the oracle holds that

    def __init__(self, world, trace=None):
        self.world, self.trace = world, trace
        self._before = None
        world.clients.append(self)

    @property
    def app(self):
        return self.world.app

    def close(self):
        if self in self.world.clients:
            self.world.clients.remove(self)

    # ── the client interface ────────────────────────────────────────────

    def open(self, path="/"):
        """The page's paths, as the Mac goes there: `/` is the window as it is; `/m/<account>/<folder>` that folder's
        place in the rail, clicked, unless it is the place shown already. The Mac's rail shows every account's Inbox
        as one place, so the account goes unsaid there."""
        found = re.fullmatch(r"/m/([^/]+)/(.+)", path)
        with self._step(f"open {path}"):
            if not found:
                return
            name = PLACES.get(found.group(2), found.group(2))
            shown = self.shown()
            if (shown.list or {}).get("title") == name:
                return
            rail = shown.rail or {}
            if any(p["current"] and (p["name"] == name or p["name"].startswith(name + " ")) for p in rail.get("places", [])):
                return
            self.app.ask("click", count=1, **self.find("button", name, within="rail").spec)

    def press(self, keys, layout="us"):
        with self._step(f"press {keys}" + (f" under {layout}" if layout != "us" else "")):
            self.app.ask("press", keys=keys, layout=layout)

    def type(self, text):
        if self.trace is not None:
            self.trace.typed_into(text, self._into_body())
        with self._step(f"type {text!r}"):
            self.app.ask("type", text=text)

    def _into_body(self):
        focus = self.shown().focus or {}
        return focus.get("region") == "composer" and focus.get("label") == "message body"

    def click(self, target, count=1):
        with self._step(f"click {target.description}" + (f" ×{count}" if count > 1 else "")):
            self.app.ask("click", count=count, **target.spec)

    def drag(self, source, onto):
        with self._step(f"drag {source.description} onto {onto.description}"):
            self.app.ask("drag", source=source.spec, onto=onto.spec)

    def choose_menu(self, *path):
        with self._step("choose " + " › ".join(path)):
            self.app.ask("menu", path=list(path))

    def find(self, role, label=None, within=None, exact=True, near=None, **where):
        spec = {"role": ROLES.get(role, role)}
        said = role
        if label is not None:
            if isinstance(label, re.Pattern):
                spec.update(label=label.pattern, match="regex")
                said += f" /{label.pattern}/"
            else:
                spec.update(label=label, match="exact" if exact else "contains")
                said += f" {label!r}"
        if where:
            spec["where"] = {k: str(v) for k, v in where.items()}
            said += "".join(f" {k}={v!r}" for k, v in where.items())
        if near is not None:
            spec["near"] = near
        if within is not None:
            if isinstance(within, Element):
                spec["within"] = within.spec.get("within_self") or {"id": within.spec.get("id", "")}
                said += f" in {within.description}"
            else:
                if within not in REGIONS:
                    raise KeyError(f"no region {within!r}: name one of {', '.join(REGIONS)}")
                # The list's tabs are its tablist's on the page; on the Mac, the buttons of its tabs' group.
                spec["within"] = {"id": "tabs"} if role == "tab" and within == "list" else REGIONS[within]
                said += f" in {within}"
        return Element(self, spec, said)

    def region(self, name):
        return Element(self, {"role": None, "within": REGIONS[name], "within_self": REGIONS[name]}, name)

    def shown(self):
        return Shown.read(self.app.ask("shown")["shown"])

    def showing(self):
        """Whether the app is up and its window can be read."""
        return self.app is not None and self.app.link is not None

    def truth(self):
        from . import oracle
        return oracle.read(self.world, self.shown() if self.showing() else None)

    def picture(self, path=None):
        """The key window drawn by itself: approximate, since lane A captures no screen."""
        return self.app.ask("picture", **({"path": str(path)} if path else {}))["path"]

    def tree(self, within=None):
        return self.app.ask("tree", **({"within": within} if within else {}))["tree"]

    def settle(self, ceiling=SETTLE_S):
        if self.app is None or self.app.link is None:
            return 0.0
        took = self.app.ask("settle", ceiling=ceiling, timeout=ceiling + 60).get("ms", 0.0)
        if self.trace is not None:
            self.trace.settled(took)
        return took

    # ── the world tells it ──────────────────────────────────────────────

    def clock_moved(self, delta):
        """The world's clock moved on: the app's moves as far, running what came due on the way."""
        self.app.ask("clock", advance=delta.total_seconds())

    def app_restarted(self):
        """A new process: the window opens afresh where the app opens it."""

    # ── steps ───────────────────────────────────────────────────────────

    def _step(self, phrase):
        from .page import _Acting
        return _Acting(self, phrase)

    def begin_step(self):
        self._before = self.shown() if self.showing() else None

    def end_step(self):
        out = {"requests": []}
        if self.showing():
            out["shown"] = self.shown().diff(self._before)
        return out

