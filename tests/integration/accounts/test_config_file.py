"""mailmind.config.json through mailmind.accounts.adapters.config_file: parsed once per change of
the file, each read a copy of its own, each write atomic and readable by its owner only."""
import os

import pytest

from mailmind.accounts.adapters import config_file


@pytest.fixture
def parses(monkeypatch):
    """The paths ConfigFile parsed, in order."""
    seen = []
    real = config_file.ConfigFile._parse

    def spy(self, path):
        seen.append(os.path.basename(path))
        return real(self, path)
    monkeypatch.setattr(config_file.ConfigFile, "_parse", spy)
    return seen


def test_a_request_parses_the_file_once_however_often_it_reads_it(app, parses, monkeypatch):
    from mailmind.bootstrap.accounts import config
    calls = []
    real = config._raw

    def counted():
        calls.append(1)
        return real()
    monkeypatch.setattr(config, "_raw", counted)
    config.save(real())                         # a new file: the next read parses it
    parses.clear()
    assert app.get("/api/digest").status == 200
    assert len(calls) > 1                       # the request consults the file several times
    assert parses == ["mailmind.config.json"]   # and parses it once
    parses.clear()
    assert app.get("/api/digest").status == 200
    assert parses == []                         # unchanged since: not at all
