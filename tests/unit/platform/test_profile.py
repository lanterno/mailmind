"""mailmind.platform.profile: how the process runs, full or read-only, from MAILMIND_PROFILE and
the older MAILMIND_NOSYNC."""
import pytest

from mailmind.platform import profile
from mailmind.platform.profile import Profile, ProfileError
from mailmind.platform.settings import SettingsError

FULL, READ_ONLY = Profile.FULL, Profile.READ_ONLY


@pytest.mark.parametrize("value, want", [
    (None, FULL), ("", FULL), ("full", FULL), (" Full ", FULL), ("read-only", READ_ONLY), ("READ-ONLY", READ_ONLY)])
def test_mailmind_profile_names_the_profile_and_unset_is_full(value, want):
    env = {} if value is None else {"MAILMIND_PROFILE": value}
    assert profile.current(env) is want
    assert profile.check(env) is want


@pytest.mark.parametrize("value, want", [
    ("1", READ_ONLY), ("true", READ_ONLY), ("yes", READ_ONLY), (" On ", READ_ONLY), ("anything", READ_ONLY),
    ("", FULL), ("0", FULL), ("false", FULL), ("No", FULL), ("off", FULL)])
def test_the_older_switch_still_selects_read_only_and_fails_toward_it(value, want):
    assert profile.current({"MAILMIND_NOSYNC": value}) is want


def test_when_the_two_disagree_read_only_wins():
    assert profile.current({"MAILMIND_PROFILE": "full", "MAILMIND_NOSYNC": "1"}) is READ_ONLY
    assert profile.current({"MAILMIND_PROFILE": "read-only", "MAILMIND_NOSYNC": "0"}) is READ_ONLY


@pytest.mark.parametrize("value", ["readonly", "read_only", "demo", "fulll"])
def test_a_name_that_is_no_profile_counts_as_read_only_and_check_refuses_it(value):
    env = {"MAILMIND_PROFILE": value}
    assert profile.current(env) is READ_ONLY
    with pytest.raises(ProfileError, match=f"must be one of full, read-only, not '{value}'"):
        profile.check(env)
    assert issubclass(ProfileError, SettingsError)


def test_it_reads_the_process_environment_at_every_call(monkeypatch):
    monkeypatch.delenv("MAILMIND_NOSYNC", raising=False)
    monkeypatch.setenv("MAILMIND_PROFILE", "read-only")
    assert profile.current() is READ_ONLY
    monkeypatch.setenv("MAILMIND_PROFILE", "full")
    assert profile.current() is FULL


def test_the_app_refuses_to_start_on_a_name_that_is_no_profile(tmp_path, monkeypatch):
    """Before it creates or migrates anything, or starts a worker."""
    from mailmind.bootstrap import wiring
    from mailmind.platform import settings
    monkeypatch.setenv("MAILMIND_PROFILE", "readonly")
    spawned = []
    with settings.use(settings.Settings(home=str(tmp_path))), pytest.raises(ProfileError):
        wiring.start(spawn=spawned.append)
    assert spawned == []
    assert list(tmp_path.iterdir()) == []
