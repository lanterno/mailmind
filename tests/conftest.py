"""Shared setup for the pytest-native tests in this directory.

Before any MailMind module loads, this takes any Claude key out of the environment, pins the AI
engine to the API (so a missing key means AI is off, never a `claude` CLI call), and installs the
tripwire in guard.py. Every test then fails if the tripwire refused anything during it.

`data_dir` points every module's data path at a fresh temp directory and creates the schema
there. Tests that touch the store, config, rules, views or audit trails must use it.
"""
import os

import pytest

import guard

os.environ.pop("ANTHROPIC_API_KEY", None)
os.environ["MAILMIND_AI"] = "api"
guard.install()


@pytest.fixture(autouse=True)
def _tripwire():
    guard.violations.clear()
    yield
    found = list(guard.violations)
    guard.violations.clear()
    if found:
        pytest.fail("the test tripwire refused: " + "; ".join(found), pytrace=False)


# (module, attribute, file name) for every path a module builds next to its own source.
DATA_PATHS = (
    ("store", "DB", "mailmind.db"),
    ("config", "CONFIG", "mailmind.config.json"),
    ("config", "HERE", ""),                      # config.save's temp file goes here
    ("rules", "FILE", "mailmind_rules.json"),
    ("mailclass", "RULES_FILE", "mailmind_rules.json"),
    ("views", "FILE", "mailmind_views.json"),
    ("views", "HERE", ""),                       # views.save's temp file goes here
    ("manage", "AUDIT_FILE", "sweep_audit.jsonl"),
    ("server", "RECON_AUDIT", "reconcile_audit.jsonl"),
    ("server", "SWEEP_AUDIT", "sweep_audit.jsonl"),
)


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    """A temp directory holding this test's database, config, rules, views and audit trails."""
    import importlib
    for mod, attr, name in DATA_PATHS:
        monkeypatch.setattr(importlib.import_module(mod), attr, str(tmp_path / name) if name else str(tmp_path))
    import mailclass
    monkeypatch.setattr(mailclass, "_rules_cache", {})
    import store
    store.init()
    return tmp_path


def _redirect(mp, directory):
    import importlib
    for mod, attr, name in DATA_PATHS:
        mp.setattr(importlib.import_module(mod), attr, os.path.join(directory, name) if name else str(directory))
    import mailclass
    mp.setattr(mailclass, "_rules_cache", {})


@pytest.fixture(scope="session")
def world_template(tmp_path_factory):
    """The world's database, built once by the app's own ingest. Tests get a copy via `world`."""
    from fakes.imap_server import FakeIMAPServer
    from fixtures import world
    import sqlite3
    import store
    d = tmp_path_factory.mktemp("world-template")
    with pytest.MonkeyPatch.context() as mp, FakeIMAPServer() as srv:
        _redirect(mp, str(d))
        placed = world.populate(srv)
        world.write_config(srv, str(d))
        store.init()
        world.ingest_all()
        db = sqlite3.connect(os.path.join(d, "mailmind.db"))
        db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        db.close()
    return os.path.join(d, "mailmind.db"), placed


@pytest.fixture
def world(world_template, data_dir):
    """A fresh copy of the world: a running fake server holding the corpus, a config pointing at
    it, and the database the app built from it, in this test's data_dir."""
    import shutil
    from fakes.imap_server import FakeIMAPServer
    from fixtures import world as w
    template, placed = world_template
    for suffix in ("", "-wal", "-shm"):
        p = data_dir / ("mailmind.db" + suffix)
        if p.exists():
            p.unlink()
    shutil.copyfile(template, data_dir / "mailmind.db")
    with FakeIMAPServer() as srv:
        assert w.populate(srv) == placed
        w.write_config(srv, str(data_dir))
        yield w.World(srv, placed, data_dir)
