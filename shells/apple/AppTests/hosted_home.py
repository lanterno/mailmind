"""`just apple test-hosted`: the hosted tests' app on fictional mail, with nothing of a person's anywhere near it.

The hosted tests' host is the Mac app itself, which xcodebuild starts. This makes the world `just apple demo` runs it
on (Measure/demo_home.py): three made-up accounts on the fake IMAP and SMTP servers on loopback, the corpus in their
folders, and the fake Claude API; a temp data folder that goes when this ends, a database key made for the run, and a
file standing in for the login Keychain. Then it runs the command after `--` (xcodebuild's test) with each of those
handed to the app as xcodebuild hands a variable to the process the tests run in: named `TEST_RUNNER_<name>`, which
it passes on as `<name>`. The servers stay up until the command ends.

The variables, not the test bundle, name the home, because the app opens its core as its window first appears, before
XCTest loads the bundle: a variable the bundle set would come too late for that, and racing it would leave a run
whose home depends on timing. An app started as a test host with no home named opens nothing (`CoreHome.ofThisProcess`
throws `NoHomeNamed` for an XCTest run), so a run started any other way, from Xcode say, fails its tests and never
reaches a person's data. Its preferences are kept apart by the app itself (`RunPreferences`), in the temp home, which
this checks after the run as the demo does.

    python shells/apple/AppTests/hosted_home.py -- xcodebuild ... test-without-building
"""
import os
import secrets
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
sys.path[:0] = [os.path.join(ROOT, "tests", "support"), os.path.join(ROOT, "tests"), ROOT,
                os.path.join(os.path.dirname(HERE), "Measure")]

import guard                                        # noqa: E402
guard.install()                                     # before the world keeps a password: never in the login Keychain

import fake_keychain                                # noqa: E402
from demo import populate                           # noqa: E402
from demo_home import check_kept_apart              # noqa: E402
from fakes.anthropic_api import FakeAnthropic       # noqa: E402
from fakes.imap_server import FakeIMAPServer        # noqa: E402
from fakes.smtp_server import FakeSMTPServer        # noqa: E402
from fixtures import world as w                     # noqa: E402


def main():
    if "--" not in sys.argv:
        sys.exit("hosted_home.py -- COMMAND...: the command that runs the hosted tests")
    command = sys.argv[sys.argv.index("--") + 1:]
    with tempfile.TemporaryDirectory(prefix="mailmind-mac-hosted-") as home, \
            FakeIMAPServer(keychain=guard.KEYCHAIN) as imap, FakeSMTPServer() as smtp, FakeAnthropic() as claude:
        keychain = os.path.join(home, "keychain.json")
        os.environ[fake_keychain.VARIABLE] = keychain   # where config_entry keeps each world password, and the app reads it
        populate(imap)
        w.connect_smtp(imap, smtp)
        w.write_config(imap, home, smtp)
        app = {"MAILMIND_HOME": home, "MAILMIND_TEST_KEYCHAIN": keychain, "MAILMIND_DB_KEY": secrets.token_hex(32),
               "ANTHROPIC_API_KEY": FakeAnthropic.KEY, "ANTHROPIC_BASE_URL": claude.url}
        env = {**os.environ, **{f"TEST_RUNNER_{name}": value for name, value in app.items()}}
        print("The hosted tests' app on fictional mail: a temp home, fake servers on loopback and the fake Claude API.",
              flush=True)
        status = subprocess.run(command, env=env).returncode
        check_kept_apart(home)
    sys.exit(status)


if __name__ == "__main__":
    main()
