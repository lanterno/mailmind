"""`just apple demo`: the Mac app on fictional mail, with nothing of yours anywhere near it.

The world `just demo` serves runs here too: three made-up accounts on the fake IMAP and SMTP servers, on loopback, the
corpus in their folders (its dates moved up so its newest message is today), and the fake Claude API. The app opens a temp
data folder that goes when this ends (MAILMIND_HOME), its database encrypted with a key made for the run (MAILMIND_DB_KEY,
never the Keychain's), the accounts' passwords in a file standing in for the login Keychain (MAILMIND_TEST_KEYCHAIN). With
that file the core reaches no host off this machine and runs no program, and this process runs under the test suite's
tripwire as well.

    python shells/apple/Measure/demo_home.py APP            # the app, until you quit it (or Ctrl-C here)
    python shells/apple/Measure/demo_home.py APP --smoke    # opens, waits for mail in the inbox's rows, quits: 0 if it showed
    python shells/apple/Measure/demo_home.py APP --measure  # launch to first rows and idle memory: one launch to sync, then 7
"""
import os
import secrets
import signal
import statistics
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
SUPPORT = os.path.join(ROOT, "tests", "support")
sys.path[:0] = [SUPPORT, os.path.join(ROOT, "tests"), ROOT]

import guard                                        # noqa: E402
guard.install()                                     # before the world keeps a password: never in the login Keychain

import fake_keychain                                # noqa: E402
from demo import populate                          # noqa: E402
from fakes.anthropic_api import FakeAnthropic      # noqa: E402
from fakes.imap_server import FakeIMAPServer       # noqa: E402
from fakes.smtp_server import FakeSMTPServer       # noqa: E402
from fixtures import world as w                    # noqa: E402

LAUNCHES = 7
# Seconds a measured launch may take. The app gives up after a minute without mail; one that never got a window
# (started where it can't reach the screen, as in a sandbox) never measures at all.
LIMIT = 120


def launch(app, env):
    """One measured run of the app: its output's `name=value` lines, or None when it didn't show mail."""
    try:
        run = subprocess.run([app], env={**env, "MAILMIND_MEASURE": "home"}, capture_output=True, text=True,
                             timeout=LIMIT)
    except subprocess.TimeoutExpired:
        print(f"the app said nothing in {LIMIT} s: did it get a window?")
        return None
    said = dict(line.split("=", 1) for line in run.stdout.splitlines() if "=" in line)
    if run.returncode != 0 or "first_rows_ms" not in said:
        sys.stdout.write(run.stdout)
        return None
    return said


def main():
    app, mode = sys.argv[1], (sys.argv[2:3] or [""])[0]
    with tempfile.TemporaryDirectory(prefix="mailmind-mac-demo-") as home, FakeIMAPServer(keychain=guard.KEYCHAIN) as imap, \
            FakeSMTPServer() as smtp, FakeAnthropic() as claude:
        keychain = os.path.join(home, "keychain.json")
        os.environ[fake_keychain.VARIABLE] = keychain   # where config_entry keeps each world password, and the app reads it
        populate(imap)
        w.connect_smtp(imap, smtp)
        w.write_config(imap, home, smtp)
        # Only what the app needs: the folder, the stand-in, a key of its own, the fake Claude API, and a PATH.
        env = {"MAILMIND_HOME": home, "MAILMIND_TEST_KEYCHAIN": keychain, "MAILMIND_DB_KEY": secrets.token_hex(32),
               "ANTHROPIC_API_KEY": FakeAnthropic.KEY, "ANTHROPIC_BASE_URL": claude.url,
               "PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "HOME": os.environ.get("HOME", home), "TMPDIR": home}
        if mode == "--smoke":
            said = launch(app, env)
            if said is None:
                sys.exit("The Mac app never showed mail in its inbox on the demo home.")
            print(f"the Mac app showed {said['rows_total']} rows of fictional mail, {said['first_rows_ms']} ms after it started")
            return
        if mode == "--measure":
            first = launch(app, env)     # syncs the world into the new database first: not counted
            if first is None:
                sys.exit("The Mac app never showed mail in its inbox on the demo home.")
            print(f"first launch, syncing the demo accounts: first_rows_ms={first['first_rows_ms']}")
            runs = [launch(app, env) for _ in range(LAUNCHES)]
            if any(r is None for r in runs):
                sys.exit("A measured launch never showed mail.")
            starts = [float(r["first_rows_ms"]) for r in runs]
            memory = [float(r["idle_footprint_mb"]) for r in runs]
            print(f"first_rows_ms runs: {' '.join(f'{s:.1f}' for s in starts)}")
            print(f"first_rows_ms median: {statistics.median(starts):.1f}")
            print(f"idle_footprint_mb median: {statistics.median(memory):.1f} (rows in the inbox: {runs[-1]['rows_total']})")
            return
        print("Fictional mail only: a temp data folder, fake servers on loopback and the fake Claude API.", flush=True)
        with subprocess.Popen([app], env=env) as running:
            try:
                running.wait()
            except KeyboardInterrupt:
                running.send_signal(signal.SIGTERM)
                running.wait()


if __name__ == "__main__":
    main()
