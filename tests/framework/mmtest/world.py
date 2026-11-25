"""The world: a home in a temp folder, the fake servers, the corpus in them, the app's server over it all, and one test
clock. A scenario and `mm` reach the app's mail only through this.

It sits on what the suites already have: fixtures/world.py's corpus and accounts, tests/fakes' IMAP, SMTP and Claude
servers, and support/served.py, which starts `mailmind-server` behind the launcher's line protocol (mm-served). The
page's server is the Rust one only (test-architecture.md section 4). Background jobs are paused, as the served suites
have them: a scenario pumps one with `world.run("outbox")`.
"""
import contextlib
import fcntl
import hashlib
import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email import policy
from email.parser import BytesParser

from . import mail as mailing

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
BUILD = os.path.join(ROOT, "build", "mm")
# The world's clock stands here until a scenario moves it: conftest.NOW, a Monday noon the corpus is dated around.
NOW = datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc)
SERVER = os.path.join(ROOT, "core", "target", "debug", "mm-served")
CORPORA = ("tiny",)


def server_command():
    """The Rust server's launcher: MAILMIND_TEST_SERVER when it names one, else the debug build in this worktree."""
    named = os.environ.get("MAILMIND_TEST_SERVER")
    if named:
        if "mm-served" not in named:
            raise RuntimeError(f"the page runs on the Rust server only; MAILMIND_TEST_SERVER names {named}")
        return named
    if not os.access(SERVER, os.X_OK):
        raise RuntimeError(f"no {SERVER}: build it with `cargo build -p mm-served` in core/ (`just e2e` does)")
    return SERVER


def _web_sources():
    web = os.path.join(ROOT, "web")
    for top in ("src", "assets", "public"):
        for folder, _, files in os.walk(os.path.join(web, top)):
            for name in sorted(files):
                yield os.path.join(folder, name)
    for name in sorted(os.listdir(web)):
        path = os.path.join(web, name)
        if os.path.isfile(path):
            yield path


def page_build():
    """The page, built once for what web/ holds now, into build/mm/dist-<hash>: a run, or another worker, that finds it
    built uses it, so a scenario never waits for a build it doesn't need."""
    digest = hashlib.sha256()
    for path in _web_sources():
        digest.update(os.path.relpath(path, ROOT).encode())
        with open(path, "rb") as f:
            digest.update(f.read())
    out = os.path.join(BUILD, "dist-" + digest.hexdigest()[:16])
    if os.path.exists(os.path.join(out, "index.html")):
        return out
    os.makedirs(BUILD, exist_ok=True)
    with open(os.path.join(BUILD, "dist.lock"), "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if not os.path.exists(os.path.join(out, "index.html")):
            web = os.path.join(ROOT, "web")
            if not os.path.isdir(os.path.join(web, "node_modules")):
                raise RuntimeError("web/node_modules isn't installed: run `just setup`")
            for old in os.listdir(BUILD):                   # one build kept: the one for what web/ holds now
                if old.startswith("dist-"):
                    shutil.rmtree(os.path.join(BUILD, old), ignore_errors=True)
            subprocess.run([shutil.which("npx") or "npx", "vite", "build", "--outDir", out, "--emptyOutDir"], cwd=web,
                           check=True, capture_output=True)
    return out


class TestClock:
    """The one clock the world, the core and every client read. It stands still until a scenario moves it: a long delay
    (a toast's life, Undo Send's window, a snooze) passes only here. Settling moves a client's own clock through short
    ones by itself."""

    __test__ = False

    def __init__(self, world):
        self._world = world

    @property
    def now(self):
        return self._world.app.clock.now

    def advance(self, delta=None, **parts):
        """Move every clock on: `world.clock.advance(seconds=11)`, or by a timedelta."""
        delta = delta if delta is not None else timedelta(**parts)
        with self._world.doing(f"clock +{_span(delta)}"):
            self._world.app.clock.shift(delta)
            for client in self._world.clients:
                client.clock_moved(delta)

    def move_to(self, when):
        self.advance(when - self.now)


def _span(delta):
    """A timedelta as a person writes it: 19h 59m."""
    seconds, parts = int(delta.total_seconds()), []
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60), ("s", 1)):
        if seconds >= size:
            parts.append(f"{seconds // size}{unit}")
            seconds %= size
    return " ".join(parts) or "0s"


@dataclass
class Sent:
    """A message the fake SMTP server took: its envelope and what it said."""

    user: str
    recipients: list           # the envelope's, bare and lowercased, sorted
    data: bytes

    @property
    def message(self):
        return BytesParser(policy=policy.default).parsebytes(self.data)

    @property
    def subject(self):
        return str(self.message["Subject"] or "")

    @property
    def text(self):
        """The plain text a person wrote, as the message carries it, with its line ends as \\n."""
        part = self.message.get_body(preferencelist=("plain",))
        return part.get_content().replace("\r\n", "\n") if part is not None else ""


class Smtp:
    """The fake SMTP server, as a scenario reads it: what was sent, waited for by pumping the outbox."""

    def __init__(self, world, fake):
        self._world, self.fake = world, fake
        self._seen = 0

    @property
    def sent(self):
        with self.fake.lock:
            return [Sent(d.user, sorted(a.lower() for a in d.rcpts), d.data) for d in self.fake.messages]

    def wait_for_one(self):
        """The one message sent since the last wait, after a pass of the outbox (`world.run("outbox")`). The jobs are
        paused and the clock stands still, so one pass sends all that is due: a send still inside its Undo window goes
        only once the scenario has moved the clock past it."""
        with self._world.doing("the outbox sends what is due"):
            if not self.sent[self._seen:]:
                self._world.app.work("outbox")
            fresh = self.sent[self._seen:]
            if len(fresh) != 1:
                raise AssertionError(f"{len(fresh)} messages were sent, not one: {[s.subject for s in fresh]}")
            self._seen += 1
        return fresh[0]


@dataclass
class Delivered:
    """A message the world delivered: where it landed and who it names."""

    account: str
    folder: str
    uid: int
    message_id: str
    subject: str
    sender: str
    to: list
    cc: list

    def reply_all_recipients(self, me):
        """Who a reply all to it goes to, bare and sorted: the sender, To and Cc, less `me` (a short name or address)."""
        mine = mailing.address(me).lower()
        return [a for a in mailing.addresses([self.sender, *self.to, *self.cc]) if a != mine]


class Account:
    """One of the world's accounts. Setting what the app's config holds for it restarts the app over the change, as
    Settings would leave it."""

    def __init__(self, world, name, address):
        self._world, self.name, self.address = world, name, address

    @property
    def signature(self):
        return self._world._entry(self.address).get("signature", "")

    @signature.setter
    def signature(self, text):
        self._world._edit_entry(self.address, signature=text)


class World:
    """A home with fake servers and the app's server over it. `start` builds it; `stop` takes it down. Every client
    attached to it (`clients`) hears when its clock moves or its app restarts."""

    __test__ = False

    def __init__(self, home, template, *, guarded="", log=print):
        self.home = str(home)
        self.template = template                  # (database path, {corpus file: (account, folder, uid)})
        self.guarded = guarded
        self.clients = []
        self.clock = TestClock(self)
        self.trace = None                         # the scenario's trace, once a runner gives it one
        self.app = None
        self._log = log
        self._stack = contextlib.ExitStack()

    # ── up and down ─────────────────────────────────────────────────────

    def start(self, dist):
        import time_machine
        from fakes.imap_server import FakeIMAPServer
        from fakes.smtp_server import FakeSMTPServer
        from fixtures import world as corpus
        from mailmind.platform import settings
        import canaries
        import fake_keychain
        import guard
        import served
        stack = self._stack
        os.makedirs(self.home, exist_ok=True)
        stack.enter_context(_variable(fake_keychain.VARIABLE, os.path.join(self.home, "keychain.json")))
        stack.enter_context(settings.use(settings.Settings(home=self.home)))
        database, placed = self.template
        for suffix in ("", "-wal", "-shm"):
            with contextlib.suppress(FileNotFoundError):
                os.unlink(os.path.join(self.home, "mailmind.db" + suffix))
        shutil.copyfile(database, os.path.join(self.home, "mailmind.db"))
        self.imap = stack.enter_context(FakeIMAPServer(keychain=guard.KEYCHAIN))
        smtp = stack.enter_context(FakeSMTPServer())
        if corpus.populate(self.imap) != placed:
            raise RuntimeError("the corpus landed elsewhere than in the template")
        corpus.connect_smtp(self.imap, smtp)
        corpus.write_config(self.imap, self.home, smtp)
        self.smtp = Smtp(self, smtp)
        self.placed = placed
        self.accounts = {name: Account(self, name, address)
                         for name, (address, *_rest) in zip(("ada", "bea", "cy"), corpus.ACCOUNTS)}
        local = stack.enter_context(time_machine.travel(NOW, tick=False))
        # served.command() reads the launcher from here: the Rust one, always.
        stack.enter_context(_variable("MAILMIND_TEST_SERVER", server_command()))
        env = {**os.environ, "MAILMIND_HOME": self.home, "MAILMIND_TEST_DIST": dist, "MAILMIND_TEST_GUARDED": self.guarded}
        env.setdefault("MAILMIND_TEST_LOCAL_AI", "")          # set and empty: no model server on this Mac is asked
        self.app = served.Served(env, NOW, local, os.path.join(self.home, "server.log"))
        canaries.log_file(self.app.log_path)      # the world's canaries are never in what the server says
        self.app.start()
        stack.callback(self._stop_app)
        # Past the first run: a new world has had the guide offered, so it stays out of every scenario but its own.
        self._preferences(guide_offered=True)
        return self

    def _preferences(self, **chosen):
        answer = self.app.post("/api/preferences", chosen)
        if answer.status != 200:
            raise AssertionError(f"preferences {chosen}: {answer.status}")

    def _stop_app(self):
        if self.app is not None and self.app.proc is not None:
            self.app.stop()

    def stop(self):
        self._stack.close()

    def check(self):
        """What the server's own checks refused (the tripwire) fails the run."""
        self.app.settle()
        self.app.check()

    # ── what a scenario does to it ──────────────────────────────────────

    def account(self, name):
        return self.accounts[name]

    def corpus(self, name):
        if name not in CORPORA:
            raise NotImplementedError(f"the {name!r} world isn't built yet: only {', '.join(CORPORA)} is")
        return self

    def first_run(self):
        """A person who hasn't been offered the first-run guide yet; each client opens its page again on that."""
        with self.doing("a first run: the guide not yet offered"):
            self._preferences(guide_offered=False)
            for client in self.clients:
                client.app_restarted()

    def deliver(self, message, to="ada", folder="INBOX"):
        """Deliver `message` (a mail.Mail) to an account's folder on the fake server and run the app's sync of it, as
        the background sync would on hearing of it."""
        account = self.accounts[to].address
        with self.doing(f"deliver {message.subject_!r} from {message.sender} to {to}'s {folder}"):
            raw = message.build(account, self.clock.now)
            uid = self.imap.deliver(account, folder, raw)
            self.app.work("sync", account, folder)
        head = BytesParser(policy=policy.default).parsebytes(raw, headersonly=True)
        return Delivered(account, folder, uid, str(head["Message-ID"]), str(head["Subject"]), str(head["From"]),
                         _list(head["To"]), _list(head["Cc"]))

    def judge(self, delivered, importance=5, category="action-needed", action="Reply"):
        """What the AI's triage said of a message, as a round of it would leave it: importance 4 or more needs you. Kept
        while the app is stopped, since a triage round would judge every message."""
        from mailmind.bootstrap.assistant import answers
        from mailmind.bootstrap.mailbox import store
        with self.doing(f"triage says {delivered.subject!r} is importance {importance}, {category}"), self.app.stopped():
            c = store.conn()
            try:
                row = c.execute("SELECT pk FROM messages WHERE account=? AND folder=? AND uid=?",
                                (delivered.account, delivered.folder, delivered.uid)).fetchone()
                if row is None:
                    raise AssertionError(f"{delivered.subject!r} has no row: was it synced?")
                answers.save_summary(c, row["pk"], "A summary.", importance, category, action, "claude-opus-5-5",
                                     self.clock.now.isoformat())
                c.commit()
            finally:
                c.close()

    def run(self, job, *args):
        """One pass of a paused background job: outbox, commands, drafts, snoozes, sync <account> <folder>, kicked…"""
        with self.doing(f"run {job}" + "".join(f" {a}" for a in args)):
            self.app.work(job, *args)

    def restart_app(self):
        """The same home, a new process: only what the app stored remains. Each client opens the page again."""
        with self.doing("restart the app"):
            self.app.restart()

    def where(self, subject):
        """Where on the fake server each message with this subject is: [(account, folder, flags)], sorted."""
        found = []
        with self.imap.lock:
            for user, account in self.imap.accounts.items():
                for name, box in account.mailboxes.items():
                    for m in box.messages.values():
                        if str(m.headers().get("Subject") or "") == subject:
                            found.append((user, name, sorted(m.flags)))
        return sorted(found)

    def subject_of(self, corpus_file):
        """A corpus message's subject, as the list shows it."""
        from fixtures import world as corpus
        head = BytesParser(policy=policy.default).parsebytes(corpus.raw(corpus_file), headersonly=True)
        return str(head["Subject"])

    def high_water(self):
        return self.app.high_water()

    # ── for the trace ───────────────────────────────────────────────────

    def mark(self):
        """Where the fakes' logs stand, for `since`."""
        with self.imap.lock:
            imap = len(self.imap.commands)
        return imap, len(self.smtp.fake.messages)

    def since(self, mark):
        """What the fakes saw since `mark`: IMAP commands that change mail, and messages sent."""
        from fixtures import world as corpus
        imap, smtp = mark
        with self.imap.lock:
            changed = [f"{c['user']} [{c['mailbox']}] {c['line'].split(' ', 1)[1]}"
                       for c in self.imap.commands[imap:] if c["command"] in corpus.WRITES]
        sent = [f"{s.subject!r} to {', '.join(s.recipients)}" for s in self.smtp.sent[smtp:]]
        return {"imap": changed, "smtp": sent}

    @contextlib.contextmanager
    def doing(self, phrase):
        """One step of the world's: a step of the trace, after which every client has caught up with it. A step that
        stopped the app (`app.stopped()`) has each client open its page again on the new process."""
        process = self.app.proc
        with self.trace.step(phrase) if self.trace is not None else contextlib.nullcontext():
            yield
            for client in self.clients:
                if self.app.proc is not process:
                    client.app_restarted()
                client.settle()

    # ── inside ──────────────────────────────────────────────────────────

    def _config_path(self):
        return os.path.join(self.home, "mailmind.config.json")

    def _entry(self, address):
        with open(self._config_path()) as f:
            return next(a for a in json.load(f)["accounts"] if a["email"] == address)

    def _edit_entry(self, address, **fields):
        with self.doing(f"set the {', '.join(fields)} of {address}"), self.app.stopped():
            path = self._config_path()
            with open(path) as f:
                written = json.load(f)
            next(a for a in written["accounts"] if a["email"] == address).update(fields)
            with open(path, "w") as f:
                json.dump(written, f, indent=2)


def _list(header):
    return [a.strip() for a in str(header or "").split(",") if a.strip()]


@contextlib.contextmanager
def _variable(name, value):
    before = os.environ.get(name)
    os.environ[name] = value
    try:
        yield
    finally:
        if before is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = before
