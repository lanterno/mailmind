"""A world and a client kept up, for `mm` (test-architecture.md section 3): `mm up page` starts one in a process of its
own, and `mm attach`, `mm <verb>` and `mm down` speak to it over a Unix socket, one JSON line each way. A scenario run
with --keep serves its failed world the same way.

The session is this worktree's: its socket and its record are named from the worktree's path, in the system's temp
folder, whose path is short enough for a socket (a Unix socket path can't pass 104 bytes).
"""
import contextlib
import hashlib
import json
import os
import re
import shlex
import signal
import socket
import tempfile
import time
import traceback
from datetime import timedelta

from . import mail
from .world import BUILD, ROOT

_NAME = "mm-" + hashlib.sha256(ROOT.encode()).hexdigest()[:10]
RECORD = os.path.join(tempfile.gettempdir(), _NAME + ".json")
SOCKET = os.path.join(tempfile.gettempdir(), _NAME + ".sock")
DRAFT = os.path.join(BUILD, "draft_scenario.py")

HELP = """\
shown [--diff]                what is on screen, as text (--diff: what changed since the last shown)
press KEYS                    keys as a person presses them: e, cmd+k, "g i", escape
type TEXT                     words, where the keyboard is
click ROLE [LABEL] [in REGION] [subject=…] [sender=…]   e.g. click button "Reply all" in reader; click row in list subject=Venue
find ROLE [LABEL] [in REGION] [subject=…]               whether it's there and visible, and what it says
open PATH                     the page at PATH: /, /m/ada@example.test/INBOX
deliver [SUBJECT…] [--from WHO] [--to ACCOUNT] [--folder F] [--cc N] [--long]
judge SUBJECT [IMPORTANCE]    the AI's triage says it needs you (importance 4 or more)
clock +7h | +30m | +2d | +10s move the one test clock on
run JOB [ARG…]                a pass of a paused job: outbox, commands, drafts, snoozes, sync ACCOUNT FOLDER…
first-run                     the first-run guide not yet offered
restart                       the app's server stopped and started again over the same home
where SUBJECT                 where the message is on the fake IMAP server
sent                          what the fake SMTP server took
events                        the core's high-water event id
settle                        wait until the page, the core and the fakes are quiet
picture                       a PNG of the page; prints its path
save-step PHRASE              append what you did since the last save to build/mm/draft_scenario.py
truth                         what the core holds, as the oracle reads it
down                          stop the world and the page"""


class Session:
    """A world, a client on it, and the screens over them, answering `mm`'s verbs."""

    def __init__(self, world, client):
        from .screens import App
        self.world, self.client = world, client
        self.app = App(client, world)
        self.last = None
        self.done = []                     # what was done since the last save-step, as scenario lines

    def answer(self, line):
        words = shlex.split(line)
        if not words:
            return ""
        verb, args = words[0], words[1:]
        handler = getattr(self, "do_" + verb.replace("-", "_"), None)
        if handler is None:
            return f"no verb {verb!r}: `help` lists them"
        return handler(*args)

    def do_help(self):
        return HELP

    def do_shown(self, *flags):
        now = self.client.shown()
        said = now.diff(self.last) if "--diff" in flags and self.last is not None else now.text()
        self.last = now
        return said or "(nothing changed)"

    def do_press(self, *keys):
        self.client.press(" ".join(keys))
        self.done.append(f"app.press({' '.join(keys)!r})")
        return "ok"

    def do_type(self, *text):
        self.client.type(" ".join(text))
        self.done.append(f"app.type({' '.join(text)!r})")
        return "ok"

    def _find(self, args):
        role, label, within, where = args[0], None, None, {}
        rest = list(args[1:])
        while rest:
            word = rest.pop(0)
            if word == "in" and rest:
                within = rest.pop(0)
            elif "=" in word:
                key, _, value = word.partition("=")
                where[key] = value
            elif label is None:
                label = word
        call = f"app.find({role!r}" + (f", {label!r}" if label else "") + (f", within={within!r}" if within else "")
        call += "".join(f", {k}={v!r}" for k, v in where.items()) + ")"
        # A label typed at the prompt is part of the name ("Everything" finds "Everything 11"), as Playwright reads it.
        return self.client.find(role, label, within=within, exact=False, **where), call

    def do_click(self, *args):
        found, call = self._find(args)
        self.client.click(found)
        self.done.append(f"app.client.click({call})")
        return "ok"

    def do_find(self, *args):
        found, _ = self._find(args)
        n = found.count
        if n == 0:
            return f"no {found.description}"
        if n > 1:
            return f"{n} of {found.description}"
        return f"{found.description}: {'visible' if found.visible else 'not visible'}, says {found.text!r}"

    def do_open(self, path="/"):
        self.client.open(path)
        self.done.append(f"app.open({path!r})")
        return "ok"

    def do_deliver(self, *args):
        rest, subject, given = list(args), [], {"from": "lin", "to": "ada", "folder": "INBOX"}
        message = mail.Mail()
        while rest:
            word = rest.pop(0)
            if word in ("--from", "--to", "--folder", "--cc") and rest:
                given[word[2:]] = rest.pop(0)
            elif word == "--long":
                message = message.body("\n\n".join(f"Paragraph {i}: the samples, the schedule and the room." for i in range(200)))
            else:
                subject.append(word)
        message = message.from_(given["from"]).subject(" ".join(subject) or "A note")
        if "cc" in given:
            message = message.cc(int(given["cc"]))
        note = self.world.deliver(message, to=given["to"], folder=given["folder"])
        self.done.append(f"world.deliver(mail.from_({given['from']!r}).subject({note.subject!r}), to={given['to']!r})")
        return f"delivered {note.subject!r} to {note.account} {note.folder} (uid {note.uid})"

    def do_judge(self, subject, importance="5"):
        found = [d for d in self.world.where(subject) if d[1] != "Trash"]
        if not found:
            return f"no message {subject!r} on the server"
        account, folder, _ = found[0]
        uid = next(uid for uid, m in self.world.imap.mailbox(account, folder).messages.items()
                   if str(m.headers().get("Subject") or "") == subject)
        from .world import Delivered
        self.world.judge(Delivered(account, folder, uid, "", subject, "", [], []), importance=int(importance))
        self.done.append(f"world.judge(note, importance={int(importance)})  # note: what world.deliver returned for {subject!r}")
        return "ok"

    def do_clock(self, amount):
        found = re.fullmatch(r"\+(\d+)([smhd])", amount)
        if not found:
            return "clock +N with s, m, h or d: +7h"
        unit = {"s": "seconds", "m": "minutes", "h": "hours", "d": "days"}[found.group(2)]
        self.world.clock.advance(timedelta(**{unit: int(found.group(1))}))
        self.done.append(f"world.clock.advance({unit}={found.group(1)})")
        return f"now {self.world.clock.now.isoformat()}"

    def do_run(self, job, *args):
        self.world.run(job, *args)
        self.done.append(f"world.run({', '.join(repr(a) for a in (job, *args))})")
        return "ok"

    def do_first_run(self):
        self.world.first_run()
        self.done.append("world.first_run()")
        return "ok"

    def do_restart(self):
        self.world.restart_app()
        self.done.append("world.restart_app()")
        return "ok"

    def do_where(self, *subject):
        return "\n".join(f"{a} {f} {' '.join(flags)}".rstrip() for a, f, flags in self.world.where(" ".join(subject))) \
            or "nowhere"

    def do_sent(self):
        return "\n".join(f"{s.subject!r} from {s.user} to {', '.join(s.recipients)}" for s in self.world.smtp.sent) \
            or "nothing sent"

    def do_events(self):
        return self.world.high_water()

    def do_settle(self):
        return f"settled in {self.client.settle():.0f} ms"

    def do_picture(self):
        return self.client.picture()

    def do_truth(self):
        return self.client.truth().text()

    def do_save_step(self, *phrase):
        os.makedirs(os.path.dirname(DRAFT), exist_ok=True)
        fresh = not os.path.exists(DRAFT)
        with open(DRAFT, "a", encoding="utf-8") as f:
            if fresh:
                f.write('"""A draft scenario `mm save-step` wrote: name it as a sentence, and move it to\n'
                        'tests/scenarios/<feature>/ with the assertions that make it a test."""\n'
                        "from mmtest import mail  # noqa: F401\n\n\ndef test_draft(world, app):\n")
            f.write(f"    # {' '.join(phrase) or 'a step'}\n")
            f.writelines(f"    {line}\n" for line in self.done or ["pass"])
        self.done = []
        return f"saved to {os.path.relpath(DRAFT)}"


def serve(session, path=SOCKET, record=None):
    """Answer `mm` on `path` until `down`: one JSON line in ({"line": "press e"}), one out ({"ok": …, "text": …})."""
    with contextlib.suppress(FileNotFoundError):
        os.unlink(path)
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(path)
    os.chmod(path, 0o600)
    listener.listen(4)
    listener.settimeout(0.5)
    write_record({"pid": os.getpid(), "socket": path, "home": session.world.home, "origin": session.world.app.origin,
                  **(record or {})})
    stopping = []
    signal.signal(signal.SIGTERM, lambda *_: stopping.append(True))
    try:
        while not stopping:
            try:
                conn, _ = listener.accept()
            except (socket.timeout, InterruptedError):
                continue
            with conn, conn.makefile("rwb") as stream:
                for raw in stream:
                    line = json.loads(raw).get("line", "")
                    if line.strip() == "down":
                        stopping.append(True)
                        reply = {"ok": True, "text": "down"}
                    else:
                        try:
                            reply = {"ok": True, "text": str(session.answer(line))}
                        except Exception as e:
                            reply = {"ok": False, "text": f"{type(e).__name__}: {e}\n" +
                                     "".join(traceback.format_exception(e)[-3:])}
                    stream.write((json.dumps(reply) + "\n").encode())
                    stream.flush()
                    if stopping:
                        break
    finally:
        listener.close()
        with contextlib.suppress(FileNotFoundError):
            os.unlink(path)
        with contextlib.suppress(FileNotFoundError):
            os.unlink(RECORD)


def write_record(record):
    with open(RECORD + ".tmp", "w") as f:
        json.dump(record, f)
    os.replace(RECORD + ".tmp", RECORD)


def read_record():
    try:
        with open(RECORD) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def alive(record):
    if not record:
        return False
    try:
        os.kill(record["pid"], 0)
    except (ProcessLookupError, PermissionError):
        return False
    return os.path.exists(record["socket"])


class Link:
    """A connection to the session: `ask("press e")` returns (ok, text)."""

    def __init__(self, path=SOCKET, timeout=120):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(timeout)
        self.sock.connect(path)
        self.stream = self.sock.makefile("rwb")

    def ask(self, line):
        self.stream.write((json.dumps({"line": line}) + "\n").encode())
        self.stream.flush()
        raw = self.stream.readline()
        if not raw:
            return False, "the session ended"
        reply = json.loads(raw)
        return reply["ok"], reply["text"]

    def close(self):
        self.stream.close()
        self.sock.close()


def wait_for_record(pid, timeout=180):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        record = read_record()
        if record and record.get("pid") == pid and alive(record):
            return record
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return None
        time.sleep(0.2)
    return None
