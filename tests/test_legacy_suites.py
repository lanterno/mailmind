"""Run each of the repo's script-style suites in its own interpreter.

Those suites make a temp directory the app's data directory (mailmind.platform.settings) when
they are imported, and some replace module functions with fakes. Two of them in one process would
overwrite each other's, so each runs the way `python3 <suite>.py` does: alone, from the repo root.
Its output is shown if it fails. MAILMIND_HOME names a temp directory of its own, so a path a
suite forgets to redirect still lands there and not in the repo.

Any `test*.py` added at the repo root is picked up here. New pytest-native tests live beside this
file.
"""
import os
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SUITES = sorted(p.name for p in ROOT.glob("test*.py"))


def test_the_suites_are_found():
    assert {"tests.py", "tests_sanitize.py", "test_hardening.py"} <= set(SUITES)


@pytest.mark.parametrize("suite", SUITES)
def test_suite(suite, tmp_path):
    env = {**os.environ, "MAILMIND_HOME": str(tmp_path)}
    r = subprocess.run([sys.executable, suite], cwd=ROOT, env=env, capture_output=True, text=True, timeout=600)
    assert r.returncode == 0, f"{suite} exited {r.returncode}\n--- stdout\n{r.stdout[-6000:]}\n--- stderr\n{r.stderr[-6000:]}"
