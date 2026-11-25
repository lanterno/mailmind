"""`just apple demo`: the Mac app on fictional mail, with nothing of yours anywhere near it.

The world `just demo` serves runs here too: three made-up accounts on the fake IMAP and SMTP servers, on loopback, the
corpus in their folders (its dates moved up so its newest message is today), and the fake Claude API. The app opens a temp
data folder that goes when this ends (MAILMIND_HOME), its database encrypted with a key made for the run (MAILMIND_DB_KEY,
never the Keychain's), the accounts' passwords in a file standing in for the login Keychain (MAILMIND_TEST_KEYCHAIN). With
that file the core reaches no host off this machine and runs no program, and this process runs under the test suite's
tripwire as well. The app keeps such a run's preferences apart from yours by itself (Sources/Mac/Shared/
RunPreferences.swift): none of your MailMind's settings or window frames are read or written, and the run's own are kept
in the temp home, as Library/Preferences/<the app's identifier>.plist, which this checks after each run.

    python shells/apple/Measure/demo_home.py APP            # the app, until you quit it (or Ctrl-C here)
    python shells/apple/Measure/demo_home.py APP --smoke    # opens, waits for mail in the inbox's rows, quits: 0 if it showed
    python shells/apple/Measure/demo_home.py APP --measure  # launch to first rows and idle memory: one launch to sync, then 7;
                                                            # the medians go to build/measure/budgets.txt as well
    python shells/apple/Measure/demo_home.py APP --picture  # the window drawn light and dark: build/screenshots/window

`--arch x86_64` after any of them runs that half of a universal app (a release's) through /usr/bin/arch: on Apple
silicon, Rosetta runs the Intel half as an Intel Mac would.
"""
import os
import plistlib
import secrets
import signal
import statistics
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
SUPPORT = os.path.join(ROOT, "tests", "support")
MEASURED = os.path.join(os.path.dirname(HERE), "build", "measure")
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
# Every Release build of the app is this one, a person's own MailMind and a demo's alike. The Debug build, MailMind
# Dev, is io.github.lanterno.mailmind.dev, and `identifier` reads it from the app.
IDENTIFIER = "io.github.lanterno.mailmind"


def identifier(app):
    """The bundle identifier of the app whose executable is `app`, from its Info.plist; MailMind's when it has none."""
    info = os.path.join(os.path.dirname(os.path.dirname(app)), "Info.plist")
    try:
        with open(info, "rb") as file:
            return plistlib.load(file).get("CFBundleIdentifier") or IDENTIFIER
    except (OSError, plistlib.InvalidFileException):
        return IDENTIFIER


def kept_apart(home, app_identifier=IDENTIFIER):
    """What the app kept of its preferences in the demo's home, as a dict, or None when it kept nothing there. A run
    that kept nothing there may have used the person's own (an app built before RunPreferences, or one that lost it)."""
    path = os.path.join(home, "Library", "Preferences", f"{app_identifier}.plist")
    try:
        with open(path, "rb") as file:
            kept = plistlib.load(file)
    except (OSError, plistlib.InvalidFileException):
        return None
    return kept if isinstance(kept, dict) else None


def check_kept_apart(home, app_identifier=IDENTIFIER):
    """Ends the demo, saying why, unless the app kept its preferences in the demo's home; else says what it kept."""
    kept = kept_apart(home, app_identifier)
    if kept is None:
        sys.exit("The Mac app kept no preferences in the demo's home, so it may have read or written your own "
                 "MailMind's: see Sources/Mac/Shared/RunPreferences.swift.")
    keys = ", ".join(sorted(kept)) or "none set yet"
    print(f"the Mac app kept its preferences in the demo's home, apart from yours: {keys}")


def launch(app, env, pictures=None):
    """One measured run of the app, `app` being the command that starts it: its output's `name=value` lines, or None
    when it didn't show mail."""
    env = {**env, "MAILMIND_MEASURE": "home", **({"MAILMIND_PICTURE": pictures} if pictures else {})}
    try:
        run = subprocess.run(app, env=env, capture_output=True, text=True, timeout=LIMIT)
    except subprocess.TimeoutExpired:
        print(f"the app said nothing in {LIMIT} s: did it get a window?")
        return None
    said = dict(line.split("=", 1) for line in run.stdout.splitlines() if "=" in line)
    if run.returncode != 0 or "first_rows_ms" not in said:
        sys.stdout.write(run.stdout)
        # How it ended, and the end of what it wrote to standard error: an app that can't start on this Mac (a
        # system function its macOS lacks, which dyld names as it launches) says why only there.
        ended = f"signal {-run.returncode}" if run.returncode < 0 else f"status {run.returncode}"
        print(f"the app ended with {ended}; the last of its standard error:")
        sys.stdout.write("".join(run.stderr.splitlines(keepends=True)[-40:]))
        return None
    return said


def command(app, arch):
    """How to start the app: itself, or one half of it through /usr/bin/arch."""
    if not arch:
        return [app]
    halves = subprocess.run(["/usr/bin/lipo", "-archs", app], capture_output=True, text=True).stdout.split()
    if arch not in halves:
        sys.exit(f"This app holds {' and '.join(halves) or 'no code lipo can read'}, not {arch}. A release's app holds "
                 "both; just apple release-check runs the Apple silicon half.")
    if subprocess.run(["/usr/bin/arch", f"-{arch}", "/usr/bin/true"], capture_output=True).returncode != 0:
        sys.exit(f"This Mac can't run {arch} code. On Apple silicon, Rosetta runs it: softwareupdate --install-rosetta")
    return ["/usr/bin/arch", f"-{arch}", app]


def main():
    app, flags = sys.argv[1], sys.argv[2:]
    arch = flags[flags.index("--arch") + 1] if "--arch" in flags else ""
    mode = next((flag for flag in flags if flag in ("--smoke", "--measure", "--picture")), "")
    app_identifier = identifier(app)
    app = command(app, arch)
    rosetta = arch == "x86_64" and os.uname().machine == "arm64"
    how = f" ({arch}{', under Rosetta' if rosetta else ''})" if arch else ""
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
            print(f"the Mac app{how} showed {said['rows_total']} rows of fictional mail, {said['first_rows_ms']} ms after it "
                  f"started, and kept {said['idle_footprint_mb']} MB idle")
            check_kept_apart(home, app_identifier)
            return
        if mode == "--picture":
            pictures = os.path.join(ROOT, "shells", "apple", "build", "screenshots", "window")
            said = launch(app, env, pictures)
            if said is None:
                sys.exit("The Mac app never showed mail in its inbox on the demo home.")
            print(f"the Mac app drew its window on fictional mail into {pictures}")
            check_kept_apart(home, app_identifier)
            return
        if mode == "--measure":
            first = launch(app, env)     # syncs the world into the new database first: not counted
            if first is None:
                sys.exit("The Mac app never showed mail in its inbox on the demo home.")
            print(f"first launch, syncing the demo accounts: first_rows_ms={first['first_rows_ms']}")
            check_kept_apart(home, app_identifier)
            runs = [launch(app, env) for _ in range(LAUNCHES)]
            if any(r is None for r in runs):
                sys.exit("A measured launch never showed mail.")
            starts = [float(r["first_rows_ms"]) for r in runs]
            memory = [float(r["idle_footprint_mb"]) for r in runs]
            print(f"first_rows_ms runs: {' '.join(f'{s:.1f}' for s in starts)}")
            print(f"first_rows_ms median: {statistics.median(starts):.1f}")
            print(f"idle_footprint_mb median: {statistics.median(memory):.1f} (rows in the inbox: {runs[-1]['rows_total']})")
            # For `just apple budgets`, beside what measure.sh wrote; a later line for a name wins.
            os.makedirs(MEASURED, exist_ok=True)
            with open(os.path.join(MEASURED, "budgets.txt"), "a", encoding="utf-8") as f:
                f.write(f"mac_home_first_rows_ms={statistics.median(starts):.1f}\n")
                f.write(f"mac_home_idle_memory_mb={statistics.median(memory):.1f}\n")
            return
        print("Fictional mail only: a temp data folder, fake servers on loopback and the fake Claude API.", flush=True)
        with subprocess.Popen(app, env=env) as running:
            try:
                running.wait()
            except KeyboardInterrupt:
                running.send_signal(signal.SIGTERM)
                running.wait()
        check_kept_apart(home, app_identifier)


if __name__ == "__main__":
    main()
