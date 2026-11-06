"""mailmind.config.json through mailmind.accounts.adapters.config_file: parsed once per change of
the file, each read a copy of its own, each write atomic and readable by its owner only."""
import json
import os
import stat

import pytest

from mailmind.accounts.adapters import config_file
from mailmind.platform import settings

ADA = {"email": "ada@example.test", "type": "imap", "host": "127.0.0.1", "credential": {"plain": "x"}}
BEA = {"email": "bea@example.test", "type": "imap", "host": "127.0.0.1", "credential": {"plain": "y"}}


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


def put(data_dir, raw):
    (data_dir / "mailmind.config.json").write_text(json.dumps(raw))


def test_a_file_unchanged_is_parsed_once(data_dir, parses):
    put(data_dir, {"accounts": [ADA]})
    f = config_file.ConfigFile()
    assert [f.read() for _ in range(5)] == [{"accounts": [ADA]}] * 5
    assert parses == ["mailmind.config.json"]


def test_a_save_is_read_at_once(data_dir, parses):
    put(data_dir, {"accounts": [ADA]})
    f = config_file.ConfigFile()
    f.read()
    f.write({"accounts": [ADA, BEA]})
    assert f.read() == {"accounts": [ADA, BEA]}
    assert len(parses) == 2


def test_an_edit_in_place_is_read_at_once(data_dir, parses):
    path = data_dir / "mailmind.config.json"
    put(data_dir, {"accounts": [ADA]})
    f = config_file.ConfigFile()
    f.read()
    ino = os.stat(path).st_ino
    with open(path, "r+") as fh:                 # the same inode and size, a later mtime
        text = fh.read()
        fh.seek(0)
        fh.write(text.replace('"x"', '"z"'))
    st = os.stat(path)
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))
    assert os.stat(path).st_ino == ino
    assert f.read()["accounts"][0]["credential"] == {"plain": "z"}


def test_each_read_is_a_copy_of_its_own(data_dir):
    put(data_dir, {"accounts": [ADA]})
    f = config_file.ConfigFile()
    mine = f.read()
    mine["accounts"].append(BEA)
    mine["accounts"][0]["alias"] = "changed"
    assert f.read() == {"accounts": [ADA]}


def test_no_file_reads_as_no_accounts_until_there_is_one(data_dir, parses):
    f = config_file.ConfigFile()
    assert f.read() == {"accounts": []}
    f.read()["accounts"].append(ADA)
    assert f.read() == {"accounts": []}
    put(data_dir, {"accounts": [BEA]})
    assert f.read() == {"accounts": [BEA]}
    assert parses == ["mailmind.config.json"]


def test_a_file_that_isnt_json_raises_every_time(data_dir):
    (data_dir / "mailmind.config.json").write_text("{not json")
    f = config_file.ConfigFile()
    for _ in range(2):
        with pytest.raises(json.JSONDecodeError):
            f.read()


def test_another_data_directory_is_read_from_its_own_file(data_dir, tmp_path_factory):
    put(data_dir, {"accounts": [ADA]})
    other = tmp_path_factory.mktemp("other-home")
    (other / "mailmind.config.json").write_text(json.dumps({"accounts": [BEA]}))
    f = config_file.ConfigFile()
    assert f.read() == {"accounts": [ADA]}
    with settings.use(settings.Settings(home=str(other))):
        assert f.read() == {"accounts": [BEA]}
    assert f.read() == {"accounts": [ADA]}


def test_a_write_is_atomic_and_its_owners_only(data_dir):
    f = config_file.ConfigFile()
    f.write({"accounts": [ADA]})
    path = data_dir / "mailmind.config.json"
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert json.loads(path.read_text()) == {"accounts": [ADA]}
    assert [p.name for p in data_dir.iterdir() if p.name.startswith(".cfg-")] == []


def test_a_write_of_the_wrong_shape_leaves_the_file(data_dir):
    put(data_dir, {"accounts": [ADA]})
    f = config_file.ConfigFile()
    with pytest.raises(ValueError):
        f.write({"accounts": "wiped"})
    assert f.read() == {"accounts": [ADA]}


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
