"""A person's preferences: the block in mailmind.config.json, read and written through the Directory, and their route. The file is
the owner's real one in use, so what a block's absence means and what a write leaves alone are held here."""

import pytest

from mailmind.accounts.domain import preferences
from mailmind.kernel import send_window
from mailmind.kernel.errors import Refused


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
