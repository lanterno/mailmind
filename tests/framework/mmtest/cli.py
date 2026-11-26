"""`mm`: the framework as a command, for driving a live app by hand (test-architecture.md section 3).

    just mm up page            # a world, the page on it, and a prompt (`--detach`: no prompt; `--headed`: see Chromium)
    just mm up mac             # a world and MailMind Dev on it, through its driver (lane A: off the screen, never key)
    just mm attach             # a prompt on the session that is up
    just mm shown              # one verb, and its answer: what a script or an agent uses
    just mm down               # stop it

Everything runs on fictional mail in a temp home, on loopback, under the tests' tripwire: the session's process scrubs
MailMind's variables from its environment, keys its database with the tests' key and keeps every secret in the
Keychain's stand-in, as conftest.py does for a test.
"""
import os
import signal
import subprocess
import sys
import tempfile

from . import session

HERE = os.path.dirname(os.path.abspath(__file__))
FRAMEWORK = os.path.dirname(HERE)
MM = os.path.join(FRAMEWORK, "mm")
USAGE = """\
mm up page [--world tiny] [--headed] [--detach]   start a world and the page on it, then a prompt
mm up mac [--world tiny] [--detach]                start a world and MailMind Dev on it, then a prompt
mm attach                                          a prompt on the session that is up
mm down                                            stop it
mm VERB [ARGS…]                                    one verb: mm shown, mm press e, mm help…"""


def main(argv):
    if not argv or argv[0] in ("-h", "--help"):
        print(USAGE)
        return 0
    verb, args = argv[0], argv[1:]
    if verb == "_serve":
        return _serve(args)
    if verb == "up":
        return up(args)
    if verb == "attach":
        return attach()
    if verb == "down":
        return down()
    return once(" ".join(_quoted(a) for a in argv))


def _quoted(word):
    import shlex
    return shlex.quote(word)


def up(args):
    if not args or args[0] not in ("page", "mac"):
        print("mm up page | mac", file=sys.stderr)
        return 2
    kind = args[0]
    what = "the page" if kind == "page" else "MailMind Dev"
    record = session.read_record()
    if session.alive(record):
        print(f"already up at {record['origin']}: `just mm attach`, or `just mm down` first")
        return 1
    world = _option(args, "--world", "tiny")
    log = os.path.join(tempfile.gettempdir(), session._NAME + ".log")
    with open(log, "wb") as out:
        child = subprocess.Popen([sys.executable, MM, "_serve", kind, world, "--headed" if "--headed" in args else ""],
                                 stdin=subprocess.DEVNULL, stdout=out, stderr=out, start_new_session=True)
    print(f"starting a {world} world and {what} (its log: {log})…", flush=True)
    record = session.wait_for_record(child.pid)
    if record is None:
        with open(log, encoding="utf-8", errors="replace") as f:
            print("it didn't start:\n" + "".join(f.readlines()[-30:]), file=sys.stderr)
        return 1
    where = f" at {record['origin']}" if kind == "page" else ""
    print(f"up: {what}{where}, its home {record['home']}\n"
          "  just mm attach    a prompt     just mm shown    one verb     just mm down    stop it")
    if "--detach" in args or not sys.stdin.isatty():
        return 0
    return attach()


def _option(args, name, default):
    return args[args.index(name) + 1] if name in args and args.index(name) + 1 < len(args) else default


def attach():
    record = session.read_record()
    if not session.alive(record):
        print("nothing is up: `just mm up page`", file=sys.stderr)
        return 1
    try:
        import readline                                      # noqa: F401  history and editing at the prompt
    except ImportError:
        pass
    link = session.Link(record["socket"])
    print("mm: `help` lists the verbs; `exit` leaves it up, `down` stops it")
    try:
        while True:
            try:
                line = input("mm> ").strip()
            except EOFError:
                print()
                break
            if line in ("exit", "quit"):
                break
            if not line:
                continue
            ok, text = link.ask(line)
            print(text if ok else f"failed: {text}")
            if line == "down" or text == "the session ended":
                break
    finally:
        link.close()
    return 0


def once(line):
    record = session.read_record()
    if not session.alive(record):
        print("nothing is up: `just mm up page`", file=sys.stderr)
        return 1
    link = session.Link(record["socket"])
    try:
        ok, text = link.ask(line)
    finally:
        link.close()
    print(text if ok else f"failed: {text}", file=sys.stdout if ok else sys.stderr)
    return 0 if ok else 1


def down():
    record = session.read_record()
    if record is None:
        print("nothing is up")
        return 0
    if session.alive(record):
        try:
            link = session.Link(record["socket"], timeout=60)
            link.ask("down")
            link.close()
        except OSError:
            os.kill(record["pid"], signal.SIGTERM)
    elif os.path.exists(session.RECORD):
        os.unlink(session.RECORD)
    print("down")
    return 0


def _serve(args):
    """The session's own process: the world, and Chromium and the page or MailMind Dev, answering on the socket until
    `down`."""
    kind, world_name = args[0], args[1] if len(args) > 1 else "tiny"
    guarded = prepare()
    from fixtures import world as corpus
    import guard
    from fakes.anthropic_api import FakeAnthropic
    from .page import PageClient, launch
    from .world import NOW, World, page_build
    home = tempfile.mkdtemp(prefix="mm-home-")
    with FakeAnthropic() as claude:
        os.environ["ANTHROPIC_BASE_URL"] = claude.url        # no key, so AI is off; and nothing reaches a real API
        template = corpus.build_template(tempfile.mkdtemp(prefix="mm-template-"), NOW, guard.KEYCHAIN)
        world = World(os.path.join(home, "home"), template, guarded=guarded).corpus(world_name)
        playwright = browser = None
        if kind == "mac":
            from . import mac
            mac.build()
            world.start(kind="mac")
            client = mac.MacClient(world)
        else:
            world.start(page_build())
            playwright, browser = launch(headed="--headed" in args)
            client = PageClient(world, browser)
        try:
            client.open("/")
            session.serve(session.Session(world, client), record={"kind": kind, "world": world_name})
        finally:
            client.close()
            if browser is not None:
                browser.close()
                playwright.stop()
            world.stop()
            import shutil
            shutil.rmtree(home, ignore_errors=True)
            shutil.rmtree(os.path.dirname(template[0]), ignore_errors=True)
    return 0


def prepare():
    """This process as conftest.py makes a test's: MailMind's variables gone (MAILMIND_HOME guarded, not used), UTC, the
    tests' database key, the tripwire and the Keychain's stand-in. Returns the folder MAILMIND_HOME named, if any."""
    tests = os.path.dirname(FRAMEWORK)
    root = os.path.dirname(tests)
    sys.path[:0] = [root, tests, os.path.join(tests, "support"), FRAMEWORK]
    import time
    import guard
    from mailmind.platform import environment
    home = os.environ.get("MAILMIND_HOME", "")
    for variable in environment.VARIABLES:
        os.environ.pop(variable.name, None)
    os.environ["TZ"] = "UTC"
    time.tzset()
    os.environ[environment.DB_KEY.name] = "5e" * 32        # conftest's TEST_DB_KEY: the tests', never the Keychain's
    os.environ["MAILMIND_TEST_LOCAL_AI"] = ""                # set and empty: the server asks no model server on this Mac
    if home:
        guard.protect(home)
    guard.install(loud=True)
    from mailmind.assistant.adapters import local_servers
    from mailmind.bootstrap import wiring
    local_servers.known = lambda: []
    wiring.assemble()
    return home
