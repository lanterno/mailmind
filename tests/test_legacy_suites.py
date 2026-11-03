"""Run each of the repo's script-style suites in its own interpreter.

Those suites point module globals (store.DB, config.CONFIG, rules.FILE, ...) at temp files when
they are imported. Two of them in one process would overwrite each other's settings, so each runs
the way `python3 <suite>.py` does: alone, from the repo root. Its output is shown if it fails.

Any `test*.py` added at the repo root is picked up here. New pytest-native tests live beside this
file.
"""
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SUITES = sorted(p.name for p in ROOT.glob("test*.py"))


def test_the_suites_are_found():
    assert {"tests.py", "tests_sanitize.py", "test_hardening.py"} <= set(SUITES)


@pytest.mark.parametrize("suite", SUITES)
def test_suite(suite):
    r = subprocess.run([sys.executable, suite], cwd=ROOT, capture_output=True, text=True, timeout=600)
    assert r.returncode == 0, f"{suite} exited {r.returncode}\n--- stdout\n{r.stdout[-6000:]}\n--- stderr\n{r.stderr[-6000:]}"
