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
