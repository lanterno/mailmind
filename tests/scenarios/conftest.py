"""The scenarios' `world` and `app`: mmtest's world over the fakes, and the screens over a client on it
(tests/framework/mmtest/plugin.py). They take the place of tests/conftest.py's fixtures of the same names here."""
import pytest

import conftest


@pytest.fixture(scope="session")
def mm_guarded():
    return conftest._home


@pytest.fixture
def world(mm_world):
    return mm_world


@pytest.fixture
def app(mm_app):
    return mm_app
