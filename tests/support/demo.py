"""`just demo`: the whole app on fictional mail, with nothing of yours anywhere near it.

The test suite's world runs for real: three made-up accounts on the fake IMAP and SMTP servers, on loopback, with the
corpus in their folders (its dates moved up so its newest message is today), and the fake Claude API, which answers from
each call's schema. `python -m mailmind serve` runs against them as it would against real servers, in a temp data
directory that goes when it stops, its database encrypted with a key made for the run (never the Keychain's), the
accounts' passwords in the tests' stand-in for the Keychain (a file in that directory), and with the suite's tripwire
on, here and in the server: it refuses the real data files, any host off this machine and the programs that hold your
credentials. What a send sends lands in the fake SMTP server, and what a filing moves moves on the fake IMAP server.

    python tests/support/demo.py [port]     # Ctrl-C ends it
"""
import email.utils
import os
import re
import secrets
import signal
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
TESTS = os.path.dirname(HERE)
ROOT = os.path.dirname(TESTS)
sys.path[:0] = [HERE, TESTS, ROOT]

import guard                                        # noqa: E402
guard.install()                                     # before the world keeps a password: never in the login Keychain

import fake_keychain                                # noqa: E402
from fakes.anthropic_api import FakeAnthropic      # noqa: E402
from fakes.imap_server import FakeIMAPServer       # noqa: E402
from fakes.smtp_server import FakeSMTPServer       # noqa: E402
from fixtures import world as w                    # noqa: E402

CORPUS_NOW = datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc)     # conftest.NOW, which conftest can't lend without pytest
DATE = re.compile(rb"^Date:[ \t]*([^\r\n]+)", re.I | re.M)
SERVE = ("import sys; sys.path[:0] = [{support!r}, {root!r}]; import guard; guard.install(); "
         "import runpy; sys.argv = {argv!r}; runpy.run_module('mailmind', run_name='__main__')")


def moved_up(raw):
    """A message with its Date as far ahead as today is of the corpus's own day, so it's new enough for a first sync."""
    def now_ish(m):
        when = email.utils.parsedate_to_datetime(m.group(1).decode("ascii", "replace"))
        return b"Date: " + email.utils.format_datetime(when + (datetime.now(timezone.utc) - CORPUS_NOW)).encode()
    return DATE.sub(now_ish, raw, count=1)


def populate(server):
    for address, kind, _, extra in w.ACCOUNTS:
        server.add_account(address, kind=kind, folders=extra)
    for name, account, folder, flags, thread in w.MAIL:
        server.deliver(account, folder, moved_up(w.raw(name)), flags=flags, thread_of=moved_up(w.raw(thread)) if thread else None)


def main():
    argv = ["mailmind", "serve", *sys.argv[1:2]]
    with tempfile.TemporaryDirectory(prefix="mailmind-demo-") as home, FakeIMAPServer() as imap, FakeSMTPServer() as smtp, \
            FakeAnthropic() as claude:
        os.environ[fake_keychain.VARIABLE] = os.path.join(home, "keychain.json")   # shared with the server, which reads it
        populate(imap)
        w.connect_smtp(imap, smtp)
        w.write_config(imap, home, smtp)
        env = {**os.environ, "MAILMIND_HOME": home, "ANTHROPIC_API_KEY": FakeAnthropic.KEY, "ANTHROPIC_BASE_URL": claude.url,
               "MAILMIND_DB_KEY": secrets.token_hex(32), "PYTHONUNBUFFERED": "1"}   # a key of its own: no Keychain
        print("Fictional mail only: a temp data directory, fake servers on loopback and the fake Claude API.", flush=True)
        code = SERVE.format(support=HERE, root=ROOT, argv=argv)
        with subprocess.Popen([sys.executable, "-c", code], cwd=ROOT, env=env) as serving:
            try:
                serving.wait()
            except KeyboardInterrupt:
                serving.send_signal(signal.SIGINT)
                serving.wait()


if __name__ == "__main__":
    main()
