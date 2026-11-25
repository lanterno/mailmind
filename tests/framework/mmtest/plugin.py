"""mmtest's pytest plugin: tests/conftest.py loads it, and it acts only on the scenarios under tests/scenarios/.

- Every scenario is marked `scenario` (run with --e2e, as `just e2e` does) and by its feature, its folder's name
  (`-m compose`), so a change runs its own area first.
- `@clients(...)` runs one on each client named, `--client` narrows the run to one. The Mac's client isn't built yet,
  so a scenario asking for it is skipped there.
- The fixtures `mm_world`, `mm_client` and `mm_app` make a world, a page client on it, and the screens over that;
  tests/scenarios/conftest.py names them `world` and `app`. The page's problems (an uncaught error, a console error, a
  request off its origin) and the server's tripwire fail the scenario as it ends.
- A failing scenario writes its trace to build/traces/<test>/ and prints a one-line repro. With --keep it leaves the
  world and the page up and prints the `mm attach` line, until `mm down`.
- The slowest steps are reported at the end, as the slowest tests are; `--catalogue` writes build/catalogue.md from
  the scenarios that passed.
"""
import os
import re
import sys
from datetime import datetime

import pytest

SCENARIOS = os.path.join("tests", "scenarios")
_FAILED = pytest.StashKey[bool]()
_KEPT = pytest.StashKey[dict]()
_steps, _catalogue = [], []


def pytest_addoption(parser):
    group = parser.getgroup("mmtest", "the end-to-end scenarios (tests/scenarios)")
    group.addoption("--e2e", action="store_true", help="also run the scenarios, which drive the app in a client (just e2e)")
    group.addoption("--client", choices=("page", "mac"), help="run the scenarios on this client only")
    group.addoption("--keep", action="store_true",
                    help="when a scenario fails, leave its world and client up for `mm attach` (with -n0), until `mm down`")
    group.addoption("--catalogue", action="store_true", help="write build/catalogue.md from the scenarios that passed")
    group.addoption("--headed", action="store_true", help="show the page's Chromium")


def _features(root):
    folder = os.path.join(str(root), SCENARIOS)
    return sorted(d for d in os.listdir(folder) if os.path.isdir(os.path.join(folder, d)) and not d.startswith(("_", ".")))\
        if os.path.isdir(folder) else []


def pytest_configure(config):
    for line in ("scenario: an end-to-end scenario (tests/scenarios); skipped unless pytest runs with --e2e",
                 "clients(*names): the clients a scenario runs on (mmtest.clients)",
                 "free_running: background jobs run by themselves (not yet)",
                 "needs_session: needs a real, active session (lane B)"):
        config.addinivalue_line("markers", line)
    for feature in _features(config.rootpath):
        config.addinivalue_line("markers", f"{feature}: the {feature} scenarios")


def _feature_of(item):
    parts = os.path.relpath(str(item.path), str(item.config.rootpath)).split(os.sep)
    if parts[:2] != SCENARIOS.split(os.sep) or len(parts) < 4:
        return None
    return parts[2]


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(config, items):
    for item in items:
        feature = _feature_of(item)
        if feature is not None:
            item.add_marker(pytest.mark.scenario)
            item.add_marker(getattr(pytest.mark, feature))


def pytest_generate_tests(metafunc):
    if "mm_client_kind" not in metafunc.fixturenames:
        return
    marker = metafunc.definition.get_closest_marker("clients")
    names = marker.args if marker else ("page",)
    only = metafunc.config.getoption("--client")
    params = []
    for name in names:
        marks = []
        if only and name != only:
            marks.append(pytest.mark.skip(reason=f"--client {only}"))
        elif name == "mac":
            marks.append(pytest.mark.skip(reason="the Mac's client comes with its driver (test-architecture.md 11, step 4)"))
        params.append(pytest.param(name, marks=marks, id=name))
    metafunc.parametrize("mm_client_kind", params)


# ── fixtures ──────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def mm_dist():
    from .world import page_build
    return page_build()


@pytest.fixture(scope="session")
def mm_browser(request):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not request.config.getoption("--headed"))
        yield browser
        browser.close()


@pytest.fixture(scope="session")
def mm_guarded():
    """Folders whose data files the server's tripwire refuses besides the repo root: the MAILMIND_HOME the run began
    with. tests/scenarios/conftest.py says which, from tests/conftest.py, which it imports as it is collected: imported
    during a test, that module would run again and clear the environment the session set up (served.serving says so)."""
    return ""


@pytest.fixture
def mm_world(request, tmp_path, world_template, mm_dist, mm_guarded):
    """A world over the fakes, with the app's server on it, for this scenario alone."""
    from .world import World
    world = World(tmp_path / "home", world_template, guarded=mm_guarded)
    world.start(mm_dist)
    yield world
    if request.node.stash.get(_KEPT, None) is None:
        try:
            world.check()
        finally:
            world.stop()


@pytest.fixture
def mm_client_kind():
    return "page"                            # parametrized by pytest_generate_tests for a scenario that takes `app`


@pytest.fixture
def mm_client(request, mm_world, mm_client_kind):
    from .oracle import Oracle
    from .page import PageClient
    from .trace import Trace
    if mm_client_kind != "page":
        pytest.skip(f"no {mm_client_kind} client yet")
    browser = request.getfixturevalue("mm_browser")
    trace = Trace(mm_world)
    client = PageClient(mm_world, browser, trace=trace)
    trace.client = client
    trace.oracle = Oracle(mm_world, client, trace)
    mm_world.trace = trace
    request.node.stash[_KEPT] = None
    request.node.mm = {"world": mm_world, "client": client, "trace": trace}
    yield client
    failed = request.node.stash.get(_FAILED, False)
    if failed and request.config.getoption("--keep"):
        request.node.stash[_KEPT] = {}
        _keep(request, mm_world, client)
        return
    try:
        if not failed:
            assert client.problems == [], f"the page had problems: {client.problems}"
            assert client.blocked == [], f"the page reached past its origin: {client.blocked}"
    finally:
        client.close()


@pytest.fixture
def mm_app(mm_client, mm_world):
    from .screens import App
    return App(mm_client, mm_world)


def _keep(request, world, client):
    from . import session
    out = sys.__stderr__
    if os.environ.get("PYTEST_XDIST_WORKER"):
        out.write("\n--keep: a worker can't hold a world for you; run the scenario again with -n0\n")
        world.stop()
        client.close()
        return
    live = session.Session(world, client)
    out.write(f"\nkept: {request.node.nodeid} failed; its world and page are up at {world.app.origin}\n"
              f"  just mm attach        # drive it a command at a time\n"
              f"  just mm down          # and stop it, which ends this run\n")
    out.flush()
    try:
        session.serve(live)
    finally:
        client.close()
        world.stop()


# ── reports ───────────────────────────────────────────────────────────

@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item, call):
    rep = yield
    live = getattr(item, "mm", None)
    if live is None:
        return rep
    trace, client = live["trace"], live["client"]
    if rep.when == "call" and rep.failed:
        item.stash[_FAILED] = True
        from .trace import folder_for
        folder = folder_for(item.nodeid)
        shown = picture = None
        try:
            shown = client.shown().text() if client.pw is not None else None
            picture = client.picture() if client.pw is not None else None
        except Exception as e:                           # a page that broke can't always be read
            shown = f"(couldn't read what's shown: {e})"
        trace.write(folder, shown, picture)
        kind = item.callspec.params.get("mm_client_kind", "page") if hasattr(item, "callspec") else "page"
        base = item.nodeid.split("[")[0]
        rep.sections.append(("mmtest", f"repro: just e2e {base} --client {kind}\ntrace: {os.path.relpath(folder)}/trace.txt\n"
                                       + (f"\nshown at the failure:\n{shown}\n" if shown else "")
                                       + f"\nsteps:\n{trace.text()}"))
    if rep.when == "teardown":
        item.user_properties.append(("mm_steps", [[item.nodeid, s["phrase"], s["ms"], s["settle_ms"]] for s in trace.steps]))
        if not item.stash.get(_FAILED, False):
            item.user_properties.append(("mm_catalogue", [_feature_of(item), item.name, trace.phrases]))
        rep.user_properties = list(item.user_properties)
    return rep


def pytest_runtest_logreport(report):
    if report.when != "teardown":
        return
    for name, value in report.user_properties:
        if name == "mm_steps":
            _steps.extend(value)
        elif name == "mm_catalogue":
            _catalogue.append(value)


def pytest_terminal_summary(terminalreporter, config):
    if not _steps:
        return
    terminalreporter.section("slowest scenario steps")
    for nodeid, said, ms, settle in sorted(_steps, key=lambda s: s[2], reverse=True)[:8]:
        terminalreporter.write_line(f"{ms:8.0f} ms  (settling {settle:5.0f})  {said}  — {nodeid}")
    if config.getoption("--catalogue"):
        path = write_catalogue(config.rootpath)
        terminalreporter.write_line(f"\nthe catalogue: {os.path.relpath(path)}")


def sentence(name):
    """A test's name as the sentence it is: test_a_reply_answers_the_sender[page] -> "A reply answers the sender"."""
    words = re.sub(r"\[.*\]$", "", name).removeprefix("test_").replace("_", " ")
    return words[:1].upper() + words[1:]


def write_catalogue(root):
    path = os.path.join(str(root), "build", "catalogue.md")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    by_feature = {}
    for feature, name, phrases in _catalogue:
        by_feature.setdefault(feature or "other", {})[sentence(name)] = phrases
    lines = ["# The scenarios", "",
             f"Written by `just e2e catalogue` from the scenarios that passed, {datetime.now():%Y-%m-%d %H:%M}. Each is "
             "the sentence its test is named, then its steps in the words its screens use.", ""]
    for feature in sorted(by_feature):
        lines += [f"## {feature}", ""]
        for said, phrases in sorted(by_feature[feature].items()):
            lines += [f"### {said}", ""] + [f"{i}. {p}" for i, p in enumerate(phrases, 1)] + [""]
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return path
