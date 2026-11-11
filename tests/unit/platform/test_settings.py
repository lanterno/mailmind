"""mailmind.platform.settings: where the data lives (MAILMIND_HOME, or the repo root), the file
names inside it, and how the process's Settings is read, replaced and restored."""
import os
import threading

import pytest

from mailmind.platform import settings
from mailmind.platform.settings import Settings, SettingsError

REPO = os.path.realpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))


@pytest.fixture(autouse=True)
def _restore():
    before = settings._current
    yield
    settings.configure(before)


def test_unset_home_is_the_repo_root_where_the_data_has_always_lived():
    s = settings.from_env({})
    assert os.path.realpath(s.home) == REPO
    assert os.path.isfile(os.path.join(s.home, "pyproject.toml"))
    assert s.port == 8721


def test_an_empty_home_counts_as_unset():
    assert settings.from_env({"MAILMIND_HOME": ""}) == settings.from_env({})


def test_every_data_file_lives_in_the_home_under_its_usual_name(tmp_path):
    """And there is no other: the rule book, the saved views, the snippets and the audit trails are in the database."""
    s = settings.from_env({"MAILMIND_HOME": str(tmp_path)})
    assert s.home == str(tmp_path)
    paths = [k for k, v in vars(settings.Settings).items() if isinstance(v, property)]
    assert {k: os.path.relpath(getattr(s, k), tmp_path) for k in paths} == {
        "db": "mailmind.db", "config": "mailmind.config.json", "log": "mailmind.log"}


def test_a_relative_home_is_taken_from_the_current_directory(tmp_path, monkeypatch):
    (tmp_path / "data").mkdir()
    monkeypatch.chdir(tmp_path)
    assert settings.from_env({"MAILMIND_HOME": "data"}).home == os.path.join(os.getcwd(), "data")


def test_a_leading_tilde_is_the_users_home(tmp_path, monkeypatch):
    (tmp_path / "mm").mkdir()
    monkeypatch.setenv("HOME", str(tmp_path))
    assert settings.from_env({"MAILMIND_HOME": "~/mm"}).home == str(tmp_path / "mm")


def test_a_home_that_is_missing_or_a_file_is_refused_and_never_created(tmp_path):
    missing = tmp_path / "nowhere"
    with pytest.raises(SettingsError, match="MAILMIND_HOME is not a directory"):
        settings.from_env({"MAILMIND_HOME": str(missing)})
    assert not missing.exists()
    (tmp_path / "file").write_text("")
    with pytest.raises(SettingsError):
        settings.from_env({"MAILMIND_HOME": str(tmp_path / "file")})


def test_current_reads_the_environment_once(tmp_path, monkeypatch):
    first, second = tmp_path / "a", tmp_path / "b"
    first.mkdir()
    second.mkdir()
    settings.configure(None)
    monkeypatch.setenv("MAILMIND_HOME", str(first))
    assert settings.current().home == str(first)
    monkeypatch.setenv("MAILMIND_HOME", str(second))
    assert settings.current().home == str(first)
    settings.configure(None)                            # None: read the environment again
    assert settings.current().home == str(second)


def test_configure_replaces_and_use_restores(tmp_path):
    outer, inner = Settings(home=str(tmp_path / "o")), Settings(home=str(tmp_path / "i"), port=9000)
    settings.configure(outer)
    with settings.use(inner) as got:
        assert got is inner and settings.current() is inner
    assert settings.current() is outer
    with pytest.raises(RuntimeError):
        with settings.use(inner):
            raise RuntimeError("the block failed")
    assert settings.current() is outer


def test_every_thread_sees_the_replaced_settings(tmp_path):
    settings.configure(Settings(home=str(tmp_path)))
    seen = []
    t = threading.Thread(target=lambda: seen.append(settings.current().home))
    t.start()
    t.join()
    assert seen == [str(tmp_path)]


def test_settings_are_frozen(tmp_path):
    s = Settings(home=str(tmp_path))
    with pytest.raises(AttributeError):
        s.home = "/elsewhere"
