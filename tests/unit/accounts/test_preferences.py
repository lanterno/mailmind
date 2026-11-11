"""A person's preferences: the block in mailmind.config.json, read and written through the Directory, and their route. The file is
the owner's real one in use, so what a block's absence means and what a write leaves alone are held here."""
import json

import pytest

from mailmind.accounts.adapters import config_file, secrets
from mailmind.accounts.application.directory import Directory
from mailmind.accounts.domain import preferences
from mailmind.kernel import send_window
from mailmind.kernel.errors import Refused

ADA = {"email": "ada@example.test", "type": "imap", "host": "imap.example.test", "credential": {"plain": "app-password"}}
KEPT = {"anthropic_api_key": {"plain": "a-key"}, "signature_default": "Yours,\nAda", "webmail": ["https://mail.example.test"],
        "something_a_later_version_wrote": {"deep": [1, 2.5, "ü"]}}


def test_the_defaults_are_what_the_app_does_with_no_block():
    assert preferences.DEFAULTS["undo_send_seconds"] == send_window.DEFAULT == 10
    assert preferences.DEFAULTS["density"] == "comfortable" and preferences.DEFAULTS["high_contrast"] is False
    assert preferences.DEFAULTS["single_keys"] is True and preferences.DEFAULTS["conversations"] is True


@pytest.mark.parametrize("block", [None, [], "dark", 7, {}])
def test_a_block_that_is_no_object_is_the_same_as_none(block):
    assert preferences.read({"preferences": block}) == dict(preferences.DEFAULTS)


@pytest.mark.parametrize("name, junk", [
    ("density", "tiny"), ("density", 3), ("theme", ["day"]), ("high_contrast", "yes"), ("high_contrast", 1),
    ("undo_send_seconds", 0), ("undo_send_seconds", 3600), ("undo_send_seconds", "10"), ("undo_send_seconds", True), ("undo_send_seconds", 10.5),
    ("quiet_from", "25:00"), ("quiet_from", "7:00"), ("quiet_until", None), ("draft_tone", "rude"), ("single_keys", None),
])
def test_a_value_edited_by_hand_that_cant_be_that_preference_is_its_default(name, junk):
    got = preferences.read({"preferences": {name: junk, "density": "compact"}} if name != "density" else {"preferences": {name: junk}})
    assert got[name] == preferences.DEFAULTS[name]
    if name != "density":
        assert got["density"] == "compact"


def test_changes_are_judged_whole_before_anything_is_read():
    with pytest.raises(Refused):
        preferences.changes({"density": "compact", "theme": "noon"})
    assert preferences.changes({"density": "compact", "made_up": 1}) == {"density": "compact"}


# ── through the Directory, in the file ───────────────────────────────────────────────────────────────────────────────

def path_of(data_dir):
    return data_dir / "mailmind.config.json"


def file_of(data_dir):
    return json.loads(path_of(data_dir).read_text())


def with_file(data_dir, **more):
    path_of(data_dir).write_text(json.dumps({**file_of(data_dir), **more}))


@pytest.fixture
def config(data_dir):
    """The Directory over a file holding an account, and keys of its own beside it as the owner's may."""
    path_of(data_dir).write_text(json.dumps({"accounts": [ADA], **KEPT}))
    return Directory(config_file.ConfigFile(), secrets, lambda: None)


def test_a_change_leaves_every_other_key_in_the_file_as_it_was(config, data_dir):
    was = file_of(data_dir)
    assert config.set_preferences({"density": "compact"})["density"] == "compact"
    now = file_of(data_dir)
    assert now.pop("preferences") == {"density": "compact"}
    assert now == was
    assert list(now) == list(was)                # in the order it had
    assert path_of(data_dir).stat().st_mode & 0o077 == 0


def test_a_change_keeps_the_rest_of_the_block_and_what_it_doesnt_know(config, data_dir):
    with_file(data_dir, preferences={"theme": "dusk", "a_later_one": {"x": 1}})
    config.set_preferences({"high_contrast": True})
    assert file_of(data_dir)["preferences"] == {"theme": "dusk", "a_later_one": {"x": 1}, "high_contrast": True}
    assert config.preferences()["theme"] == "dusk"


@pytest.mark.parametrize("seconds", [send_window.SHORTEST, send_window.LONGEST])
def test_each_end_of_the_undo_send_window_can_be_set(config, seconds):
    assert config.set_preferences({"undo_send_seconds": seconds})["undo_send_seconds"] == seconds


@pytest.mark.parametrize("change, said", [
    *(({"undo_send_seconds": s}, "the Undo Send window must be") for s in (send_window.SHORTEST - 1, send_window.LONGEST + 1, 0, 86400)),
    *(({"quiet_from": t}, "quiet hours need a time such as 22:00") for t in ("25:00", "7:00", "22:60", "22:00:00", "soon")),
    ({"quiet_until": "soon"}, "quiet hours need a time such as 07:00"),
], ids=repr)
def test_a_value_no_preference_can_be_is_refused_and_nothing_is_written(config, data_dir, change, said):
    before = path_of(data_dir).read_bytes()
    with pytest.raises(Refused, match=f"^{said}"):
        config.set_preferences(change)
    assert path_of(data_dir).read_bytes() == before


def test_a_file_that_wont_parse_is_never_overwritten(config, data_dir):
    path_of(data_dir).write_text("{ not json")
    with pytest.raises(ValueError):
        config.set_preferences({"density": "cozy"})
    assert path_of(data_dir).read_text() == "{ not json"


@pytest.mark.parametrize("change", [{}, {"made_up": 1}])
def test_a_change_of_nothing_writes_nothing(config, data_dir, change):
    before = path_of(data_dir).stat().st_mtime_ns, path_of(data_dir).read_bytes()
    assert config.set_preferences(change) == dict(preferences.DEFAULTS)
    assert (path_of(data_dir).stat().st_mtime_ns, path_of(data_dir).read_bytes()) == before


def test_what_cant_be_a_preference_is_left_out_of_what_a_browser_held_without_a_word(config, data_dir):
    held = {"theme": "noon", "made_up": 1, "single_keys": "1", "ai_daily_limit_usd": 2, "conversations": False}
    assert config.import_preferences(held)["conversations"] is False
    assert file_of(data_dir)["preferences"] == {"conversations": False}
    assert "ai_daily_limit_usd" not in file_of(data_dir)


def test_with_nothing_to_bring_in_the_file_is_not_written(config, data_dir):
    with_file(data_dir, preferences={"density": "cozy"})
    before = path_of(data_dir).read_bytes()
    assert config.import_preferences({"density": "compact", "theme": "noon"})["density"] == "cozy"
    assert path_of(data_dir).read_bytes() == before


def test_one_preference_is_what_the_file_says_or_none(config, data_dir):
    assert config.preference("undo_send_seconds") is None
    with_file(data_dir, preferences={"undo_send_seconds": 30})
    assert config.preference("undo_send_seconds") == 30
    with_file(data_dir, preferences={"undo_send_seconds": 3600})
    assert config.preference("undo_send_seconds") is None


def test_the_first_run_guide_is_offered_once_and_the_file_remembers(config, data_dir):
    """It lives on the server so that clearing a browser's data doesn't offer it again."""
    assert config.preferences()["guide_offered"] is False
    assert config.set_preferences({"guide_offered": True})["guide_offered"] is True
    assert file_of(data_dir)["preferences"]["guide_offered"] is True
    assert Directory(config_file.ConfigFile(), secrets, lambda: None).preferences()["guide_offered"] is True
